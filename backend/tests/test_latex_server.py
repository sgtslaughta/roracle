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
DOC = "\\documentclass{article}\\begin{document}x %s\\end{document}"


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


def test_forbidden_features_scan():
    ok = b"%PDF-1.4\n1 0 obj<</Type/Annot/Subtype/Link/A<</S/URI/URI(https://a.example/x)>>>>endobj\n"
    assert srv.forbidden_features(ok) == []
    assert srv.forbidden_features(b"<</URI(mailto:a@b.c)>>") == []
    assert srv.forbidden_features(b"<</OpenAction<</S/JavaScript/JS(app.alert(1))>>>>") == [
        "JS",
        "JavaScript",
    ]
    assert srv.forbidden_features(b"<</S/#4Aava#53cript>>") == ["JavaScript"]  # hex-escaped name
    assert srv.forbidden_features(b"<</A<</S/URI/URI(file:///etc/passwd)>>>>") == ["URI"]
    assert srv.forbidden_features(b"<</Names<</EmbeddedFiles 1 0 R>>>>") == ["EmbeddedFiles"]
    assert (
        srv.forbidden_features(b"<</Subtype/Link>> stream\n/JavaScript\nendstream") == []
    )  # stream body skipped
    assert srv.forbidden_features(b"<</JSON 1>>") == []  # whole names only


def test_build_env_is_per_request_and_hardened():
    a, b = srv.build_env(Path("/tmp/latex-a")), srv.build_env(Path("/tmp/latex-b"))
    for k in ("TEXMFVAR", "TEXMFCONFIG", "HOME"):
        assert a[k].startswith("/tmp/latex-a/") and b[k].startswith("/tmp/latex-b/")
    assert len({a["TEXMFVAR"], a["TEXMFCONFIG"], a["HOME"]}) == 3
    assert (a["MKTEXTFM"], a["MKTEXPK"], a["MKTEXMF"]) == ("0", "0", "0")
    assert (a["openin_any"], a["openout_any"], a["shell_escape"]) == ("p", "p", "f")
    assert not a["TEXMFVAR"].endswith("/work")  # cache dirs are not the TeX cwd


@e2e
def test_e2e_pdf_active_content_never_returned():
    for body in (
        "\\pdfcatalog{/OpenAction << /S /JavaScript /JS (app.alert(1)) >>}",
        "\\pdfannot width 10pt height 10pt{/Subtype /Link /A << /S /Launch /F (calc.exe) >>}",
        "\\pdfobj{<< /S /JavaScript /JS (x) >>}\\pdfcatalog{/AA << /O \\pdflastobj\\space 0 R >>}",
        "\\pdfannot width 10pt height 10pt{/Subtype /Link /A << /S /URI /URI (file:///etc/passwd) >>}",
    ):
        r = post(DOC % body)
        if r.status_code == 200:  # Ghostscript dropped it: the returned PDF must be clean
            assert srv.forbidden_features(r.content) == [] and b"app.alert" not in r.content, body
        else:
            assert r.status_code == 422 and "PDF features that are not allowed" in r.json()["detail"], (
                body,
                r.text,
            )


@e2e
def test_e2e_hyperref_web_links_still_pass():
    doc = "\\documentclass{article}\\usepackage{hyperref}\\begin{document}\\href{https://example.org}{a} \\url{mailto:a@b.c}\\end{document}"
    r = post(doc)
    assert r.status_code == 200 and r.content.startswith(b"%PDF-") and b"example.org" in r.content
