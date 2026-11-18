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


def feats(**objs):
    """Shape of qpdf --json=2: {"qpdf": [meta, {"obj:N 0 R": {"value": ...}}]}."""
    return srv.forbidden_features(
        {"qpdf": [{}, {f"obj:{i} 0 R": {"value": v} for i, v in enumerate(objs.values())}]}
    )


def test_forbidden_features_walk():
    link = {"/S": "/URI", "/URI": "u:https://a.example/x"}
    assert feats(a={"/A": link}, b={"/URI": "u:MAILTO:a@b.c"}, c={"/URI": "b:" + b"https://x".hex()}) == []
    assert feats(a={"/OpenAction": "1 0 R", "/Type": "/Catalog"}) == []  # hyperref's page-fit destination
    assert feats(a={"/A": {"/S": "/JavaScript", "/JS": "u:app.alert(1)"}}) == ["JS", "JavaScript"]
    assert feats(a={"/URI": "b:" + b"file:///etc/passwd".hex()}) == ["URI"]  # hex-string URI
    assert feats(a={"/URI": "12 0 R"}) == ["URI"]  # indirect reference cannot be verified
    assert feats(a={"/URI": 5}, b={"/URI": ["u:https://a"]}) == ["URI"]
    assert feats(a={"/URI": "u:javascript:alert(1)"}) == ["URI"]
    assert feats(a={"/S": "/Launch"}) == ["Launch"]
    assert feats(a={"/Names": {"/EmbeddedFiles": "1 0 R"}}) == ["EmbeddedFiles"]
    assert feats(a={"/Subtype": "/Link", "/Border": [0, 0, 1]}) == []


def test_forbidden_features_nested_after_fake_stream_token():
    # a dictionary placed after a literal "stream" string is still just data in the decoded JSON
    deep = {"/Contents": "u:stream\nendstream", "/Next": {"/Kids": [{"/A": {"/S": "/Launch"}}]}}
    assert feats(a=deep) == ["Launch"]
    nest: object = {"/A": {"/S": "/Launch"}}
    for _ in range(50000):  # no recursion bomb: iterative walk
        nest = [nest]
    assert feats(a=nest) == ["Launch"]


def test_verify_fails_closed(tmp_path, monkeypatch):
    pdf = tmp_path / "a.pdf"
    pdf.write_bytes(b"%PDF-1.4")

    def timeout(*a, **k):
        raise srv.Bad(503, "build timed out")

    monkeypatch.setattr(srv, "_run", timeout)  # qpdf timeout
    with pytest.raises(srv.Bad) as e:
        srv.verify_pdf(pdf, tmp_path, {}, 1)
    assert (e.value.status, e.value.msg) == (422, "PDF could not be verified")
    monkeypatch.setattr(srv, "_run", lambda *a, **k: 2)  # qpdf failed
    with pytest.raises(srv.Bad) as e:
        srv.verify_pdf(pdf, tmp_path, {}, 1)
    assert e.value.msg == "PDF could not be verified"
    monkeypatch.setattr(srv, "_run", lambda *a, **k: 0)  # qpdf printed garbage
    with pytest.raises(srv.Bad) as e:
        srv.verify_pdf(pdf, tmp_path, {}, 1)
    assert e.value.msg == "PDF could not be verified"


def test_oversize_output_refused_before_read(tmp_path, monkeypatch):
    (tmp_path / "raw.pdf").write_bytes(b"%PDF-1.4")

    def fake_gs(cmd, *a, **k):
        (tmp_path / "clean.pdf").write_bytes(b"%PDF-" + b"x" * 50)
        return 0

    monkeypatch.setattr(srv, "_run", fake_gs)
    monkeypatch.setattr(srv, "MAX_PDF", 10)
    monkeypatch.setattr(Path, "read_bytes", lambda self: pytest.fail("read before size check"))
    with pytest.raises(srv.Bad) as e:
        srv.sanitize_pdf(tmp_path / "raw.pdf", tmp_path, {}, 1)
    assert e.value.msg == "built PDF is too large"


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
        "\\pdfannot width 10pt height 10pt{/Subtype /Link /A << /S /URI /URI <66696c653a2f2f2f6574632f706173737764> >>}",
        "\\immediate\\pdfobj{(javascript:alert(1))}\\pdfannot width 10pt height 10pt{/Subtype /Link /A << /S /URI /URI \\pdflastobj\\space 0 R >>}",
    ):
        r = post(DOC % body)
        if r.status_code == 200:  # Ghostscript dropped it: the returned PDF must be clean
            assert b"app.alert" not in r.content and b"/JavaScript" not in r.content, body
        else:
            assert r.status_code == 422 and r.json()["detail"].startswith(
                ("document uses PDF features", "PDF could not be")
            ), (
                body,
                r.text,
            )


@e2e
def test_e2e_hyperref_web_links_still_pass():
    doc = "\\documentclass{article}\\usepackage{hyperref}\\begin{document}\\href{https://example.org}{a} \\url{mailto:a@b.c}\\end{document}"
    r = post(doc)
    assert r.status_code == 200 and r.content.startswith(b"%PDF-") and b"example.org" in r.content
