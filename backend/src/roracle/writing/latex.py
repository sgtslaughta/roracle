"""LaTeX draft builds via the separate latex-builder service (docs/enterprise/latex-builder.md).

The service is the only place TeX runs; this client sends the source (front matter removed) plus a generated
`refs.bib`, caps the response, and maps its answers to BuildError (422 TeX error, 503 unavailable)."""

import re
import time

import httpx

from roracle.config import Settings
from roracle.writing.ast_sanitize import Ref
from roracle.writing.build import BuildError, _remaining, _semaphore, _timeout
from roracle.writing.markdown import _FM
from roracle.writing.profiles import Profile

MAX_TIMEOUT_S = 120
_KEY = re.compile(r"[^A-Za-z0-9_:.+-]")
_ESC = str.maketrans(
    {
        "&": r"\&",
        "%": r"\%",
        "$": r"\$",
        "#": r"\#",
        "_": r"\_",
        "{": "",
        "}": "",
        "\\": " ",
        "~": " ",
        "^": " ",
    }
)


def bibtex(refs: list[Ref]) -> str:
    out = []
    for r in refs:
        a = " and ".join(x.translate(_ESC) for x in r.authors)
        author = f",\n  author = {{{a}}}" if a else ""
        year = f",\n  year = {{{r.year}}}" if r.year else ""
        out.append(
            f"@misc{{{_KEY.sub('_', r.key)},\n  title = {{{r.title.translate(_ESC)}}}{author}{year}\n}}"
        )
    return "\n\n".join(out) + "\n"


class LatexBuilder:
    def __init__(self, settings: Settings, transport: httpx.BaseTransport | None = None):
        self.cfg, self.transport = settings, transport

    def build(self, markdown: str, refs: list[Ref], profile: Profile) -> bytes:
        source = _FM.sub("", markdown.replace("\r\n", "\n"), count=1)
        sem = _semaphore(self.cfg.build_concurrency)
        if not sem.acquire(blocking=False):
            raise BuildError("build service busy", 503)
        try:
            budget = min(self.cfg.build_timeout_s, MAX_TIMEOUT_S)
            deadline = time.monotonic() + budget
            body = {
                "source": source,
                "files": {"refs.bib": bibtex(refs)} if refs else {},
                "timeout_s": budget,
            }
            return self._post(body, deadline)
        finally:
            sem.release()

    def _post(self, body: dict, deadline: float) -> bytes:
        url = self.cfg.latex_builder.rstrip("/") + "/build"
        cap, buf = self.cfg.build_max_pdf_bytes, bytearray()
        try:
            with (
                httpx.Client(transport=self.transport, timeout=_timeout(deadline)) as c,
                c.stream("POST", url, json=body) as r,
            ):
                if r.status_code != 200:
                    r.read()
                    raise _error(r)
                for chunk in r.iter_bytes():
                    _remaining(deadline)
                    buf += chunk
                    if len(buf) > cap:
                        raise BuildError("built PDF is too large")
        except httpx.HTTPError:
            raise BuildError("build service unavailable", 503) from None
        if not buf.startswith(b"%PDF-"):
            raise BuildError("build service returned an invalid PDF", 503)
        return bytes(buf)


def _error(r: httpx.Response) -> BuildError:
    if r.status_code in (400, 413, 422):
        try:
            msg = str(r.json()["detail"])[:500]
        except (ValueError, KeyError, TypeError):
            msg = ""
        return BuildError(msg or "LaTeX could not build the document")
    return BuildError("build service unavailable", 503)
