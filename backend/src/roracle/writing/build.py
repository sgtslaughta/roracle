"""Sandboxed Markdown -> PDF build (R7-R9). Draft Markdown is untrusted.

pandoc (`--sandbox`, no raw HTML/TeX, no filters/templates, rlimits, empty env, fresh cwd) -> AST sanitizer ->
pandoc HTML5 + CSP meta -> separate Chromium Gotenberg -> PDF."""

import json
import re
import resource
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Protocol

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from roracle.auth import Principal
from roracle.config import Settings
from roracle.models import Document, DocumentVersion
from roracle.writing import cite_check
from roracle.writing.ast_sanitize import Ref, sanitize, words_inlines
from roracle.writing.markdown import _FM, parse
from roracle.writing.profiles import Profile

STDOUT_CAP = 50 * 1024 * 1024
RLIMIT_AS = 2 * 1024**3
RLIMIT_CPU_S = 60
FROM = "markdown-raw_html-raw_tex-raw_attribute-tex_math_single_backslash-yaml_metadata_block-pandoc_title_block-mmd_title_block"
CSP = "default-src 'none'; img-src data:; style-src 'unsafe-inline'; font-src data:"
CSS = (
    "<style>@page{size:A4;margin:22mm}body{max-width:none;margin:0;font:11pt/1.45 serif}"
    "img{max-width:100%}pre,code{white-space:pre-wrap}table{border-collapse:collapse}"
    "td,th{border:1px solid #888;padding:2px 6px}</style>"
)
_HEAD = re.compile(r"<head(?:\s[^>]*)?>", re.IGNORECASE)
_ACTIVE = re.compile(r"<\s*(?:script|iframe|object|embed|link|base|form)\b", re.IGNORECASE)
_transport: httpx.BaseTransport | None = None  # test hook (httpx.MockTransport)


class BuildError(Exception):
    """`status`: 422 bad content, 501 not enabled, 503 build service unavailable."""

    def __init__(self, message: str, status: int = 422):
        super().__init__(message)
        self.message, self.status = message, status


class Builder(Protocol):
    def build(self, markdown: str, refs: list[Ref], profile: Profile) -> bytes: ...


class DisabledLatexBuilder:
    def build(self, markdown: str, refs: list[Ref], profile: Profile) -> bytes:
        raise BuildError("LaTeX builds are not enabled", 501)


def pandoc_path() -> str | None:
    if p := shutil.which("pandoc"):
        return p
    try:
        import pypandoc
    except ImportError:  # pragma: no cover
        return None
    f = Path(pypandoc.__file__).parent / "files" / "pandoc"
    return str(f) if f.is_file() else None


def _limits() -> None:  # runs in the child between fork and exec
    resource.setrlimit(resource.RLIMIT_AS, (RLIMIT_AS, RLIMIT_AS))
    resource.setrlimit(resource.RLIMIT_CPU, (RLIMIT_CPU_S, RLIMIT_CPU_S))
    resource.setrlimit(resource.RLIMIT_FSIZE, (STDOUT_CAP, STDOUT_CAP))  # stdout is a file: caps its size


def run_pandoc(args: list[str], data: bytes, timeout: float) -> bytes:
    exe = pandoc_path()
    if exe is None:
        raise BuildError("build service unavailable", 503)
    with tempfile.TemporaryDirectory(prefix="ror-build-") as cwd:
        out, err = Path(cwd, ".out"), Path(cwd, ".err")
        try:
            with out.open("wb") as o, err.open("wb") as e:
                r = subprocess.run(
                    [exe, "--sandbox", *args],
                    check=False,
                    input=data,
                    stdout=o,
                    stderr=e,
                    cwd=cwd,
                    env={"PATH": "/usr/bin:/bin"},
                    timeout=timeout,
                    preexec_fn=_limits,
                )
        except subprocess.TimeoutExpired:
            raise BuildError("build timed out", 503) from None
        if out.stat().st_size >= STDOUT_CAP:
            raise BuildError("document too large to build")
        if r.returncode != 0:
            raise BuildError("document could not be converted: " + err.read_text("utf-8", "replace")[:200])
        return out.read_bytes()


def _title(markdown: str) -> str:
    t = parse(markdown).meta.get("title")
    return " ".join(t.split())[:300] if isinstance(t, str) else ""


def to_html(markdown: str, refs: list[Ref], profile: Profile, timeout: float) -> str:
    body = _FM.sub("", markdown.replace("\r\n", "\n"), count=1)
    ast = json.loads(run_pandoc(["-f", FROM, "-t", "json"], body.encode(), timeout))
    ast = sanitize(ast, refs, profile.citation_style)
    if t := _title(markdown):  # title goes in as AST metadata (pandoc escapes it), never as a CLI string
        ast["meta"] = {"title": {"t": "MetaInlines", "c": words_inlines(t)}}
    html = run_pandoc(
        ["-f", "json", "-t", "html5", "-s", "--mathml"], json.dumps(ast).encode(), timeout
    ).decode()
    m = _HEAD.search(html)
    if m is None or _ACTIVE.search(html):
        raise BuildError("document could not be converted")
    meta = f'<meta http-equiv="Content-Security-Policy" content="{CSP}">'
    return html[: m.end()] + meta + CSS + html[m.end() :]


class PandocBuilder:
    def __init__(self, settings: Settings, transport: httpx.BaseTransport | None = None):
        self.cfg, self.transport = settings, transport

    def build(self, markdown: str, refs: list[Ref], profile: Profile) -> bytes:
        if not self.cfg.draft_gotenberg_url:
            raise BuildError("build service not configured", 503)
        html = to_html(markdown, refs, profile, self.cfg.build_timeout_s)
        return self._pdf(html)

    def _pdf(self, html: str) -> bytes:
        cap, buf = self.cfg.build_max_pdf_bytes, bytearray()
        url = self.cfg.draft_gotenberg_url.rstrip("/") + "/forms/chromium/convert/html"
        try:
            with (
                httpx.Client(transport=self.transport, timeout=self.cfg.build_timeout_s) as c,
                c.stream("POST", url, files={"files": ("index.html", html.encode(), "text/html")}) as r,
            ):
                if r.status_code != 200:
                    down = r.status_code >= 500
                    raise BuildError(
                        "build service unavailable" if down else "build service rejected the document",
                        503 if down else 422,
                    )
                for chunk in r.iter_bytes():
                    buf += chunk
                    if len(buf) > cap:
                        raise BuildError("built PDF is too large")
        except httpx.HTTPError:
            raise BuildError("build service unavailable", 503) from None
        if not buf.startswith(b"%PDF-"):
            raise BuildError("build service returned an invalid PDF", 503)
        return bytes(buf)


def get_builder(settings: Settings, fmt: str) -> Builder:
    if fmt == "latex":
        return DisabledLatexBuilder()  # only `disabled` exists (docs/enterprise/latex-builder.md)
    return PandocBuilder(settings, _transport)


def load_refs(session: Session, p: Principal, markdown: str, limit: int) -> list[Ref]:
    """Citation keys the caller can read (R19: the rest stay unresolved) with their library metadata."""
    keys = list(dict.fromkeys(c.key for c in parse(markdown).citations))[:limit]
    ok = {k: v for k, v in cite_check.resolve_keys(session, p, keys).items() if v}
    if not ok:
        return []
    ids = {v["doc_id"] for v in ok.values()}
    docs = {
        str(i): t for i, t in session.execute(select(Document.id, Document.title).where(Document.id.in_(ids)))
    }
    metas = {
        str(i): m or {}
        for i, m in session.execute(
            select(DocumentVersion.document_id, DocumentVersion.meta).where(
                DocumentVersion.document_id.in_(ids), DocumentVersion.is_latest.is_(True)
            )
        )
    }
    out = []
    for k, v in ok.items():
        m = metas.get(v["doc_id"], {})
        y = m.get("year")
        out.append(
            Ref(
                k,
                str(docs.get(v["doc_id"]) or m.get("title") or ""),
                [a for a in m.get("authors") or [] if isinstance(a, str)],
                y if isinstance(y, int) else None,
            )
        )
    return out
