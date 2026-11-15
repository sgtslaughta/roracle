import hashlib
import io

import pytest

from roracle.storage import EmptyFile, TooLarge, stage


class CountingStream:
    def __init__(self, total):
        self.left = total
        self.read_bytes = 0

    def read(self, n=-1):
        n = self.left if n < 0 else min(n, self.left)
        self.left -= n
        self.read_bytes += n
        return b"\0" * n


def test_stage_hashes_and_counts():
    s = stage(io.BytesIO(b"hello"), max_bytes=10)
    assert s.sha256 == hashlib.sha256(b"hello").hexdigest() and s.size == 5
    s.path.unlink()


def test_stage_rejects_oversize_without_reading_everything():
    stream = CountingStream(total=50 * 1024 * 1024)
    with pytest.raises(TooLarge):
        stage(stream, max_bytes=1024 * 1024)
    assert stream.read_bytes <= 2 * 1024 * 1024


def test_stage_rejects_empty():
    with pytest.raises(EmptyFile):
        stage(io.BytesIO(b""), max_bytes=10)


def test_stage_cleans_temp_file_on_error(tmp_path, monkeypatch):
    monkeypatch.setattr("tempfile.tempdir", str(tmp_path))
    for data, exc in ((b"x" * 20, TooLarge), (b"", EmptyFile)):
        with pytest.raises(exc):
            stage(io.BytesIO(data), max_bytes=10)
    assert list(tmp_path.iterdir()) == []


def test_key_is_content_addressed(store):
    assert store.key("ab" + "0" * 62) == "blobs/ab/ab" + "0" * 62


def test_put_if_absent_uploads_once(store, staged, s3):
    assert store.put_if_absent(staged, "application/pdf") is True
    assert store.put_if_absent(staged, "application/pdf") is False
    assert store.open(staged.sha256).read() == staged.path.read_bytes()
    assert s3.exists(store.key(staged.sha256))
