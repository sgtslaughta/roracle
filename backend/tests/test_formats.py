import json

import pytest
from archive_helpers import make_gz, make_tar, make_zip

from roracle.ingest.formats import prepare
from roracle.queue import NonRetryable

CAP = 100_000
DOC = rb"\documentclass{article}" + b"\n" + rb"\begin{document}HELLO\end{document}"


def test_plain_file_untouched():
    assert prepare(b"x", "a.pdf", CAP) == (b"x", "a.pdf")


def test_tsv_becomes_csv_with_quoting():
    data, name = prepare(b"a\tb\n1\tx,y\n", "t.TSV", CAP)
    assert name == "t.csv"
    assert data.decode().splitlines() == ["a,b", '1,"x,y"']


def test_json_becomes_markdown_fence():
    data, name = prepare(b'{"k": [1, 2]}', "d.json", CAP)
    assert name == "d.md"
    text = data.decode()
    assert text.startswith("# d.json\n") and "```json\n" in text
    assert json.dumps({"k": [1, 2]}, indent=2) in text


def test_json_fence_longer_than_inner_backticks():
    data, _ = prepare(json.dumps({"k": "```"}).encode(), "d.json", CAP)
    assert "````json" in data.decode()


def test_invalid_json_nonretryable():
    with pytest.raises(NonRetryable, match="invalid JSON"):
        prepare(b"{nope", "d.json", CAP)


def test_nxml_renamed():
    assert prepare(b"<a/>", "PMC1.nxml", CAP) == (b"<a/>", "PMC1.xml")


@pytest.mark.parametrize(
    ("name", "build"),
    [
        ("p.tar.gz", lambda f: make_tar(f, gz=True)),
        ("p.tgz", lambda f: make_tar(f, gz=True)),
        ("p.tar", lambda f: make_tar(f)),
        ("p.zip", make_zip),
    ],
)
def test_latex_archive_becomes_one_tex(name, build):
    files = {
        "main.tex": DOC.replace(b"HELLO", rb"A \input{sec/one} B \include{two} C \input{missing}"),
        "sec/one.tex": b"ONE",
        "two.tex": b"TWO",
        "notes.txt": b"x",
    }
    data, out = prepare(build(files), name, CAP)
    assert out == "p.tex"
    t = data.decode()
    assert "A ONE B TWO C" in t and r"\input{missing}" in t


def test_bare_gz_with_tex():
    data, out = prepare(make_gz(DOC), "1234.5678.gz", CAP)
    assert out == "1234.5678.tex" and b"HELLO" in data


def test_main_selection_prefers_main_then_largest():
    files = {"a.tex": DOC + b"A" * 50, "main.tex": DOC.replace(b"HELLO", b"MAINDOC"), "b.tex": b"no class"}
    assert b"MAINDOC" in prepare(make_zip(files), "p.zip", CAP)[0]
    files = {"a.tex": DOC.replace(b"HELLO", b"SMALL"), "b.tex": DOC.replace(b"HELLO", b"LARGE" + b"x" * 80)}
    assert b"LARGE" in prepare(make_zip(files), "p.zip", CAP)[0]


def test_include_depth_limited():
    files = {"main.tex": DOC.replace(b"HELLO", rb"\input{l0}")}
    for i in range(15):
        files[f"l{i}.tex"] = rb"\input{l%d}" % (i + 1) if i < 14 else b"END"
    t = prepare(make_zip(files), "p.zip", CAP)[0].decode()
    assert "END" not in t and r"\input{l" in t


def test_self_include_loop_terminates():
    files = {"main.tex": DOC.replace(b"HELLO", rb"\input{main}")}
    prepare(make_zip(files), "p.zip", CAP)


def test_multi_file_archive_refused_without_latex():
    with pytest.raises(NonRetryable, match="archive was not unpacked"):
        prepare(make_zip({"a.txt": b"1", "b.txt": b"2"}), "p.zip", CAP)


def test_bare_gz_not_tex_refused():
    with pytest.raises(NonRetryable, match="archive was not unpacked"):
        prepare(make_gz(b"plain"), "p.txt.gz", CAP)


def test_corrupt_archive_nonretryable():
    with pytest.raises(NonRetryable):
        prepare(b"junk", "p.zip", CAP)
