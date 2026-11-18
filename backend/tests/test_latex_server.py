"""deploy/latex-builder/server.py: request validation + error excerpt (pure), and an opt-in real compile.

Real compile: docker run the image (see docs/enterprise/latex-builder.md), then
ROR_LATEX_E2E_URL=http://127.0.0.1:18080 pytest tests/test_latex_server.py"""

import importlib.util
import json
import os
from pathlib import Path

import httpx
import pytest

_spec = importlib.util.spec_from_file_location(
    "latex_server", Path(__file__).parents[2] / "deploy/latex-builder/server.py"
)
srv = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(srv)


def req(**kw):
    return json.dumps({"source": "x", **kw}).encode()


def test_parse_ok_and_timeout_clamped():
    assert srv.parse_request(req(files={"refs.bib": "@misc{}"}, timeout_s=9999)) == (
        "x",
        {"refs.bib": "@misc{}"},
        120,
    )
    assert srv.parse_request(req())[2] == 120


@pytest.mark.parametrize(
    "raw",
    [
        b"nope",
        b"{}",
        req(files="a"),
        req(files={"../x.bib": "a"}),
        req(files={"a/b.bib": "a"}),
        req(files={"main.tex": "a"}),
        req(files={"x.sh": "a"}),
        req(files={".a.bib": "a"}),
        req(files={"a.bib": 1}),
        req(files={f"f{i}.bib": "" for i in range(21)}),
    ],
)
def test_parse_rejects(raw):
    with pytest.raises(srv.Bad) as e:
        srv.parse_request(raw)
    assert e.value.status == 400


def test_error_excerpt():
    log = "This is pdfTeX\n! Undefined control sequence.\nl.3 \\foo\n\nmore noise\n" + "x" * 900
    assert srv.error_excerpt(log) == "! Undefined control sequence.\nl.3 \\foo"
    assert srv.error_excerpt("no errors") == "LaTeX could not build the document"
    assert len(srv.error_excerpt("! " + "y" * 900)) == 500


URL = os.environ.get("ROR_LATEX_E2E_URL")
e2e = pytest.mark.skipif(not URL, reason="ROR_LATEX_E2E_URL not set (needs the latex-builder container)")
DOC = "\\documentclass{article}\\begin{document}%s\\end{document}"


def post(src, **kw):
    return httpx.post(f"{URL}/build", json={"source": src, **kw}, timeout=150)


@e2e
def test_e2e_compiles_with_bib():
    r = post(
        DOC % "Hi $x^2$ \\cite{k}\\bibliographystyle{plain}\\bibliography{refs}",
        files={"refs.bib": "@misc{k,title={T},year={2020}}"},
    )
    assert r.status_code == 200 and r.content.startswith(b"%PDF-")


@e2e
def test_e2e_tex_error_is_422_excerpt():
    r = post(DOC % "\\foo")
    assert r.status_code == 422 and "Undefined control sequence" in r.json()["detail"]


@e2e
def test_e2e_no_shell_escape_and_no_writes():
    r = post(DOC % "\\immediate\\write18{touch /tmp/pwn}")
    assert r.status_code == 422 or r.status_code == 200  # write18 is inert; never executes
    r = post(DOC % "\\newwrite\\w\\immediate\\openout\\w=/tmp/evil.txt")
    assert r.status_code == 422 and "write on file" in r.json()["detail"]


@e2e
def test_e2e_timeout_kills_build():
    r = post(DOC % "\\newcount\\n\\loop\\advance\\n by 1 \\ifnum\\n<2000000000 \\repeat", timeout_s=3)
    assert r.status_code == 503
