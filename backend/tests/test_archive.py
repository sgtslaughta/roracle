import tarfile

import pytest
from archive_helpers import make_gz, make_tar, make_zip, zip_symlink

from roracle.ingest.archive import Archive, ArchiveError, kind_of
from roracle.storage import TooLarge


def open_archive(tmp_path, data: bytes, name: str, cap: int = 10_000):
    p = tmp_path / "spool"
    p.write_bytes(data)
    return Archive(p, name, cap)


def names(a):
    return [m.name for m in a.members]


@pytest.mark.parametrize(
    ("name", "kind"),
    [
        ("a.zip", "zip"),
        ("a.TAR.GZ", "tar"),
        ("a.tgz", "tar"),
        ("a.tar", "tar"),
        ("a.gz", "gz"),
        ("a.pdf", None),
    ],
)
def test_kind_of(name, kind):
    assert kind_of(name) == kind


def test_zip_members_and_read(tmp_path):
    a = open_archive(tmp_path, make_zip({"a.txt": b"hello", "d/b.md": b"# b"}), "x.zip")
    assert names(a) == ["a.txt", "d/b.md"]
    assert a.read(a.members[0]) == b"hello"
    assert a.skipped == []
    a.close()


def test_zip_unsafe_members_skipped_with_reasons(tmp_path):
    files = {
        "ok.txt": b"ok",
        "../evil.txt": b"x",
        "/abs.txt": b"x",
        "__MACOSX/._ok": b"x",
        ".hidden": b"x",
        "d/": b"",
    }
    nested = make_zip({"in.txt": b"i"})
    data = make_zip({**files, "inner.zip": nested}, extra=[zip_symlink("link.txt")])
    a = open_archive(tmp_path, data, "x.zip")
    assert names(a) == ["ok.txt"]
    reasons = dict(a.skipped)
    assert "unsafe path" in reasons["../evil.txt"]
    assert "unsafe path" in reasons["/abs.txt"]
    assert "nested archive" in reasons["inner.zip"]
    assert "not a regular file" in reasons["link.txt"]
    assert ".hidden" not in reasons and "__MACOSX/._ok" not in reasons


def test_tar_special_members_skipped(tmp_path):
    data = make_tar(
        {"ok.txt": b"ok", "../evil": b"x"},
        special=[
            ("sym", tarfile.SYMTYPE),
            ("hard", tarfile.LNKTYPE),
            ("dev", tarfile.CHRTYPE),
            ("dir", tarfile.DIRTYPE),
        ],
    )
    a = open_archive(tmp_path, data, "x.tar")
    assert names(a) == ["ok.txt"]
    reasons = dict(a.skipped)
    assert {"../evil", "sym", "hard", "dev"} <= set(reasons)
    assert "dir" not in reasons


def test_targz_and_tgz_same_helper(tmp_path):
    a = open_archive(tmp_path, make_tar({"a.txt": b"A"}, gz=True), "x.tar.gz")
    assert names(a) == ["a.txt"]
    assert a.read(a.members[0]) == b"A"


def test_bare_gz_single_member(tmp_path):
    a = open_archive(tmp_path, make_gz(b"just text"), "paper.txt.gz")
    assert names(a) == ["paper.txt"]
    assert a.read(a.members[0]) == b"just text"


def test_not_an_archive_raises(tmp_path):
    with pytest.raises(ArchiveError):
        open_archive(tmp_path, b"not a zip at all", "x.zip")
    with pytest.raises(ArchiveError):
        open_archive(tmp_path, b"not gzip", "x.gz")


def test_member_cap_500_warns(tmp_path):
    warns = []
    p = tmp_path / "s"
    p.write_bytes(make_zip({f"f{i}.txt": b"x" for i in range(503)}))
    a = Archive(p, "x.zip", 10_000, warn=warns.append)
    assert len(a.members) == 500
    assert any("500" in w for w in warns)
    assert any("500" in r for _, r in a.skipped)


def test_member_over_cap_is_too_large_while_reading(tmp_path):
    a = open_archive(tmp_path, make_zip({"big.bin": b"z" * 200}), "x.zip", cap=100)
    with pytest.raises(TooLarge):
        a.read(a.members[0])


def test_total_uncompressed_limit_enforced_while_reading(tmp_path):
    # cap 100: each member 99 bytes (ok), 12 members = 1188 > 10 x cap
    a = open_archive(tmp_path, make_zip({f"m{i}.txt": b"y" * 99 for i in range(12)}), "x.zip", cap=100)
    with pytest.raises(ArchiveError, match="uncompressed"):
        for m in a.members:
            a.read(m)


def test_tar_gz_bomb_rejected_at_listing(tmp_path):
    data = make_tar({"bomb.bin": b"\0" * 5000}, gz=True)
    assert len(data) < 500  # compresses tiny
    with pytest.raises(ArchiveError, match="uncompressed"):
        open_archive(tmp_path, data, "x.tgz", cap=100)
