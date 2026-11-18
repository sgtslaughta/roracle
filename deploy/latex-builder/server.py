"""latex-builder: POST /build (docs/enterprise/latex-builder.md). The only place user TeX runs.

One latexmk per request in a fresh temp dir, no shell escape, paranoid file access, rlimits, wall-clock kill of the
whole process group, size caps. No egress is enforced by the deployment (compose network / NetworkPolicy), not here."""

import json
import os
import re
import shutil
import signal
import subprocess
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

MAX_BODY = 2 * 1024 * 1024
MAX_PDF = int(os.environ.get("LATEX_MAX_PDF_BYTES", 100 * 1024 * 1024))
MAX_TIMEOUT_S = 120
MAX_FILES = 20
WORK_BYTES = 256 * 1024 * 1024
MEM_BYTES = 2 * 1024**3
CPU_S = 120
NPROC = 256
FILE_OK = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}\.(?:bib|tex)$")
ENV = {
    "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
    "openin_any": "p",
    "openout_any": "p",
    "shell_escape": "f",
    "MKTEXTFM": "0",  # no on-demand font generation
    "MKTEXPK": "0",
    "MKTEXMF": "0",
}
# Active content a published paper must not carry (viewers run or fetch it). Checked on qpdf's decoded object JSON.
BAD_KEYS = frozenset(
    "/JavaScript /JS /AA /Launch /EmbeddedFile /EmbeddedFiles /RichMedia /SubmitForm /ImportData /GoToR /GoToE "
    "/Movie /Sound /Rendition /XFA /FileAttachment /3D".split()
)
BAD_ACTIONS = frozenset(
    "/JavaScript /Launch /GoToR /GoToE /ImportData /SubmitForm /Named /Rendition /Movie /Sound".split()
)
WEB = ("http://", "https://", "mailto:")
JSON_CAP = 64 * 1024 * 1024
_sem = threading.BoundedSemaphore(max(1, int(os.environ.get("LATEX_CONCURRENCY", 2))))


class Bad(Exception):
    def __init__(self, status: int, msg: str):
        super().__init__(msg)
        self.status, self.msg = status, msg


def parse_request(raw: bytes) -> tuple[str, dict[str, str], float]:
    try:
        d = json.loads(raw)
        src, files, t = d["source"], d.get("files") or {}, float(d.get("timeout_s", MAX_TIMEOUT_S))
    except (ValueError, KeyError, TypeError):
        raise Bad(400, "malformed request") from None
    if not isinstance(src, str) or not isinstance(files, dict) or len(files) > MAX_FILES:
        raise Bad(400, "malformed request")
    for k, v in files.items():
        if not isinstance(v, str) or not FILE_OK.match(k) or k == "main.tex":
            raise Bad(400, "invalid file name (only simple *.bib / *.tex names)")
    return src, files, max(1.0, min(t, MAX_TIMEOUT_S))


def error_excerpt(log: str) -> str:
    """TeX error lines (`! ...` plus the `l.N` context), never the whole log; max 500 chars."""
    out: list[str] = []
    lines = log.splitlines()
    for i, ln in enumerate(lines):
        if ln.startswith("!"):
            out.append(ln)
            out += [x for x in lines[i + 1 : i + 6] if x.startswith("l.")][:1]
    return ("\n".join(out) or "LaTeX could not build the document")[:500]


def _limits() -> None:  # runs in the child between fork and exec; only async-safe calls
    import resource

    for r, v in (
        (resource.RLIMIT_CPU, CPU_S),
        (resource.RLIMIT_AS, MEM_BYTES),
        (resource.RLIMIT_FSIZE, WORK_BYTES),
        (resource.RLIMIT_NPROC, NPROC),
        (resource.RLIMIT_CORE, 0),
    ):
        resource.setrlimit(r, (v, v))
    os.setsid()


def _dir_bytes(p: Path) -> int:
    return sum(f.stat().st_size for f in p.rglob("*") if f.is_file() and not f.is_symlink())


def build_env(root: Path) -> dict[str, str]:
    """Per-request environment: TeX's writable dirs live in this request's own root, never shared between builds."""
    return {
        **ENV,
        "HOME": str(root / "work"),
        "TEXMFVAR": str(root / "texmf-var"),
        "TEXMFCONFIG": str(root / "texmf-config"),
    }


def _uri_ok(v: object) -> bool:
    """qpdf JSON strings are `u:<text>` or `b:<hex>`; anything else (a reference, a number) cannot be verified."""
    if not isinstance(v, str):
        return False
    if v.startswith("u:"):
        text = v[2:]
    elif v.startswith("b:"):
        try:
            text = bytes.fromhex(v[2:]).decode("latin-1")
        except ValueError:
            return False
    else:
        return False
    return text.strip().lower().startswith(WEB)


def forbidden_features(doc: object) -> list[str]:
    """Walk qpdf's `--json=2` output iteratively: forbidden keys, bad /S action types, non-web /URI values."""
    found: set[str] = set()
    stack = [doc]
    while stack:
        n = stack.pop()
        if isinstance(n, dict):
            for k, v in n.items():
                if k in BAD_KEYS:
                    found.add(k[1:])
                elif k == "/URI" and not _uri_ok(v):
                    found.add("URI")
                elif k == "/S" and v in BAD_ACTIONS:
                    found.add(v[1:])
                stack.append(v)
        elif isinstance(n, list):
            stack.extend(n)
    return sorted(found)


def _run(cmd: list[str], cwd: Path, env: dict[str, str], timeout_s: float, stdout=subprocess.DEVNULL) -> int:
    p = subprocess.Popen(  # noqa: PLW1509 - handler threads hold no locks the child needs before exec
        cmd, cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=stdout,
        stderr=subprocess.DEVNULL, preexec_fn=_limits,
    )
    try:
        return p.wait(timeout_s)
    except subprocess.TimeoutExpired:
        raise Bad(503, "build timed out") from None
    finally:
        with_suppress_kill(p)  # stray children (bibtex, dvips) die with the group


def verify_pdf(pdf: Path, root: Path, env: dict[str, str], timeout_s: float) -> None:
    """qpdf decodes every object; any failure, timeout or oversize output means the PDF cannot be verified."""
    unverified = Bad(422, "PDF could not be verified")
    js = root / "check.json"
    try:
        with js.open("wb") as f:
            rc = _run(["qpdf", "--json=2", "--json-stream-data=none", str(pdf)], root, env, timeout_s, f)
        if rc != 0 or js.stat().st_size > JSON_CAP:
            raise unverified
        with js.open("rb") as f:
            doc = json.load(f)
    except (Bad, OSError, ValueError, RecursionError, MemoryError):
        raise unverified from None
    if bad := forbidden_features(doc):
        raise Bad(422, "document uses PDF features that are not allowed: " + ", ".join(bad))


def sanitize_pdf(raw: Path, root: Path, env: dict[str, str], timeout_s: float) -> bytes:
    """Re-distill with Ghostscript (drops catalog actions, JS, attachments), then refuse any residue."""
    out = root / "clean.pdf"
    cmd = ["gs", "-q", "-dSAFER", "-dBATCH", "-dNOPAUSE", "-sDEVICE=pdfwrite", "-dCompatibilityLevel=1.4",
           f"-sOutputFile={out}", str(raw)]
    if _run(cmd, root, env, timeout_s) != 0 or not out.is_file():
        raise Bad(422, "PDF could not be sanitised")
    if out.stat().st_size > MAX_PDF:  # before reading it into memory
        raise Bad(422, "built PDF is too large")
    verify_pdf(raw, root, env, timeout_s)  # the original too: the sanitised copy must not be the only check
    verify_pdf(out, root, env, timeout_s)
    return out.read_bytes()


def compile_pdf(src: str, files: dict[str, str], timeout_s: float) -> bytes:
    root = Path(tempfile.mkdtemp(prefix="latex-"))
    try:
        work = root / "work"  # cwd; TeX's cache dirs and gs output sit beside it, off every TeX search path
        for d in (work, root / "texmf-var", root / "texmf-config"):
            d.mkdir()
        (work / "main.tex").write_text(src, "utf-8")
        for k, v in files.items():
            (work / k).write_text(v, "utf-8")
        cmd = ["latexmk", "-pdf", "-no-shell-escape", "-interaction=nonstopmode", "-halt-on-error", "-norc", "main.tex"]
        env = build_env(root)
        rc = _run(cmd, work, env, timeout_s)
        if _dir_bytes(work) > WORK_BYTES:
            raise Bad(422, "build output too large")
        pdf = work / "main.pdf"
        if rc != 0 or not pdf.is_file():
            log = work / "main.log"
            raise Bad(422, error_excerpt(log.read_text("utf-8", "replace") if log.is_file() else ""))
        if pdf.stat().st_size > MAX_PDF:
            raise Bad(422, "built PDF is too large")
        return sanitize_pdf(pdf, root, env, timeout_s)
    finally:
        shutil.rmtree(root, ignore_errors=True)


def with_suppress_kill(p: subprocess.Popen) -> None:
    try:
        os.killpg(p.pid, signal.SIGKILL)  # p.pid == pgid after setsid
    except (ProcessLookupError, PermissionError):
        pass


class H(BaseHTTPRequestHandler):
    def _send(self, status: int, ctype: str, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _err(self, status: int, msg: str) -> None:
        self._send(status, "application/json", json.dumps({"detail": msg}).encode())

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/health":
            self._send(200, "text/plain", b"ok")
        else:
            self._err(404, "not found")

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/build":
            return self._err(404, "not found")
        try:
            n = int(self.headers.get("Content-Length", ""))
        except ValueError:
            return self._err(411, "Content-Length required")
        if n > MAX_BODY:
            return self._err(413, "request too large")
        if not _sem.acquire(blocking=False):
            return self._err(503, "builder busy")
        try:
            src, files, t = parse_request(self.rfile.read(n))
            self._send(200, "application/pdf", compile_pdf(src, files, t))
        except Bad as e:
            self._err(e.status, e.msg)
        except Exception:  # noqa: BLE001 - never leak internals
            self._err(503, "builder error")
        finally:
            _sem.release()

    def log_message(self, *a) -> None:  # request bodies are user text: log nothing
        pass


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", int(os.environ.get("PORT", 8080))), H).serve_forever()  # noqa: S104
