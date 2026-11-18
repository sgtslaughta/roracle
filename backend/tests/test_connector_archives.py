import io

from archive_helpers import make_gz, make_tar, make_zip

from roracle.connectors import Item
from roracle.connectors.archives import expand

DOC = rb"\documentclass{article}\begin{document}x\end{document}"


def item(name: str, data: bytes, etag: str = "e1", size=None) -> Item:
    return Item(
        key=f"k/{name}", etag=etag, filename=name, content_type="application/zip",
        size=len(data) if size is None else size, open=lambda: io.BytesIO(data),
    )  # fmt: skip


DATA: dict[str, bytes] = {}


def run(items, cap=10_000):
    """Items are only readable until the iterator advances, so read each one on the spot."""
    warns, out = [], []
    DATA.clear()
    for it in expand(iter(items), cap, warns.append):
        out.append(it)
        if it.size is None or it.size <= cap:
            DATA[it.key] = it.open().read()
    return out, warns


def test_plain_items_pass_through_untouched():
    it = item("a.pdf", b"%PDF")
    out, _ = run([it])
    assert out == [it]


def test_zip_expanded_one_item_per_member():
    z = make_zip({"a.txt": b"hello", "d/b.md": b"# b", "img.png": b"png"})
    out, warns = run([item("p.zip", z)])
    assert [i.key for i in out] == ["k/p.zip!a.txt", "k/p.zip!d/b.md", "k/p.zip!img.png"]
    assert [i.filename for i in out] == ["a.txt", "b.md", "img.png"]
    assert [i.content_type for i in out] == ["text/plain", "text/markdown", "image/png"]
    assert DATA[out[0].key] == b"hello" and DATA[out[1].key] == b"# b"
    assert out[0].etag.startswith("e1!5-") and out[0].size == 5
    assert warns == []


def test_etag_changes_when_member_changes():
    a = run([item("p.zip", make_zip({"a.txt": b"one"}))])[0][0].etag
    b = run([item("p.zip", make_zip({"a.txt": b"two"}))])[0][0].etag
    assert a != b


def test_tar_gz_and_tar_expanded():
    out, _ = run([item("p.tar.gz", make_tar({"a.txt": b"A", "b.txt": b"B"}, gz=True))])
    assert [DATA[i.key] for i in out] == [b"A", b"B"]
    out, _ = run([item("p.tar", make_tar({"a.txt": b"A"}))], cap=100_000)
    assert out[0].key == "k/p.tar!a.txt"


def test_latex_source_archive_stays_one_item():
    z = make_zip({"main.tex": DOC, "fig.png": b"x"})
    out, _ = run([item("paper.zip", z)])
    assert len(out) == 1 and out[0].key == "k/paper.zip" and DATA[out[0].key] == z
    out, _ = run([item("e.gz", make_gz(DOC))])
    assert len(out) == 1 and out[0].key == "k/e.gz"


def test_bare_gz_non_tex_becomes_one_member():
    out, _ = run([item("notes.txt.gz", make_gz(b"hi"))])
    assert [(i.key, i.filename) for i in out] == [("k/notes.txt.gz!notes.txt", "notes.txt")]
    assert DATA[out[0].key] == b"hi"


def test_unsafe_and_nested_members_warn_not_yielded():
    z = make_zip({"ok.txt": b"1", "../evil.txt": b"2", "in.zip": make_zip({"x": b"y"})})
    out, warns = run([item("p.zip", z)])
    assert [i.filename for i in out] == ["ok.txt"]
    assert any("evil" in w for w in warns) and any("nested" in w for w in warns)


def test_oversized_archive_passes_through_so_run_counts_too_large():
    z = make_zip({"a.txt": b"a" * 50})
    it = item("p.zip", z, size=10**9)
    out, _ = run([it], cap=1000)
    assert out == [it]
    out, _ = run([item("p.zip", z + b"0" * 2000)], cap=1000)  # actual spool size over the cap
    assert len(out) == 1 and out[0].key == "k/p.zip"


def test_oversized_member_reports_size_for_too_large_count():
    out, _ = run([item("p.zip", make_zip({"big.bin": b"z" * 300}))], cap=200)
    assert out[0].size == 300


def test_corrupt_archive_warns_and_is_skipped():
    out, warns = run([item("p.zip", b"junk"), item("a.txt", b"ok")])
    assert [i.filename for i in out] == ["a.txt"] and any("p.zip" in w for w in warns)


def test_zip_bomb_warns_and_is_skipped():
    out, warns = run([item("b.tgz", make_tar({"bomb": b"\0" * 5000}, gz=True))], cap=100)
    assert out == [] and any("uncompressed" in w for w in warns)


def test_source_for_expands_archives_for_every_kind(tmp_path, settings):
    from types import SimpleNamespace

    from roracle.connectors import source_for

    (tmp_path / "p.zip").write_bytes(make_zip({"a.txt": b"A", "../x": b"bad"}))
    (tmp_path / "plain.md").write_text("# hi")
    c = SimpleNamespace(kind="fs", config={"path": str(tmp_path)})
    cfg = settings.model_copy(update={"connector_fs_roots": [str(tmp_path)]})
    warns = []
    keys = sorted(i.key.removeprefix(str(tmp_path)) for i in source_for(c, cfg, None, warns.append))
    assert keys == ["p.zip!a.txt", "plain.md"]
    assert any("unsafe path" in w for w in warns)
