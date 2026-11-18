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


def test_zip_with_too_many_entries_refused_up_front(tmp_path, monkeypatch):
    monkeypatch.setattr("roracle.ingest.archive.MAX_ENTRIES", 10)
    with pytest.raises(ArchiveError, match="too many entries"):
        open_archive(tmp_path, make_zip({f".junk{i}": b"x" for i in range(11)}), "x.zip")


def test_tar_iteration_stops_early_on_many_entries(tmp_path, monkeypatch):
    monkeypatch.setattr("roracle.ingest.archive.MAX_ENTRIES", 10)
    calls = []
    real = tarfile.TarFile.next
    monkeypatch.setattr(tarfile.TarFile, "next", lambda self: (calls.append(1), real(self))[1])
    data = make_tar({"ok.txt": b"ok", **{f".junk{i}": b"x" for i in range(50)}})
    a = open_archive(tmp_path, data, "x.tar", cap=100_000)
    assert names(a) == ["ok.txt"]
    assert len(calls) <= 12  # stopped at the limit, did not read all 51 headers
    assert any("more than 10 entries" in r for _, r in a.skipped)


def test_tar_too_many_entries_and_nothing_accepted_refused(tmp_path, monkeypatch):
    monkeypatch.setattr("roracle.ingest.archive.MAX_ENTRIES", 10)
    with pytest.raises(ArchiveError, match="too many entries"):
        open_archive(tmp_path, make_tar({f".junk{i}": b"x" for i in range(50)}), "x.tar", cap=100_000)


def test_skipped_reasons_capped_with_summary(tmp_path):
    warns = []
    p = tmp_path / "s"
    p.write_bytes(make_zip({f"../bad{i}": b"x" for i in range(120)}))
    a = Archive(p, "x.zip", 10_000, warn=warns.append)
    assert len(a.skipped) == 51 and a.skipped[-1] == ("…", "and 70 more skipped")
    assert len(warns) == 51 and warns[-1] == "… and 70 more skipped"


def _eocd_at(data: bytes) -> int:
    return data.rindex(b"PK\x05\x06")


def test_zip_entry_count_checked_before_zipfile_parses_directory(tmp_path, monkeypatch):
    import zipfile

    monkeypatch.setattr("roracle.ingest.archive.MAX_ENTRIES", 10)
    data = make_zip({f"f{i}.txt": b"x" for i in range(11)})

    def boom(*a, **k):
        raise AssertionError("ZipFile constructed")

    monkeypatch.setattr(zipfile, "ZipFile", boom)
    with pytest.raises(ArchiveError, match="too many entries"):
        open_archive(tmp_path, data, "x.zip")


def test_zip64_entry_count_read_from_zip64_record(tmp_path, monkeypatch):
    import struct
    import zipfile

    monkeypatch.setattr("roracle.ingest.archive.MAX_ENTRIES", 10)
    data = make_zip({"a.txt": b"x"})
    i = _eocd_at(data)
    cd_size, cd_off = struct.unpack("<II", data[i + 12 : i + 20])
    rec = b"PK\x06\x06" + struct.pack("<QHHIIQQQQ", 44, 45, 45, 0, 0, 11, 11, cd_size, cd_off)
    loc = b"PK\x06\x07" + struct.pack("<IQI", 0, i, 1)
    eocd = data[i : i + 8] + struct.pack("<HH", 0xFFFF, 0xFFFF) + data[i + 12 :]
    crafted = data[:i] + rec + loc + eocd
    monkeypatch.setattr(zipfile, "ZipFile", lambda *a, **k: (_ for _ in ()).throw(AssertionError("parsed")))
    with pytest.raises(ArchiveError, match="too many entries"):
        open_archive(tmp_path, crafted, "x.zip")


def test_zip_without_eocd_is_unreadable(tmp_path):
    with pytest.raises(ArchiveError, match="unreadable"):
        open_archive(tmp_path, b"PK\x03\x04" + b"0" * 100, "x.zip")


def test_normal_zip_still_opens_after_precheck(tmp_path):
    assert names(open_archive(tmp_path, make_zip({"a.txt": b"x"}), "x.zip")) == ["a.txt"]
