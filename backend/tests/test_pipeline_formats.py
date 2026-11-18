import io
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from roracle.ingest import pipeline
from roracle.queue import NonRetryable


def run_parse(monkeypatch, settings, data: bytes, filename: str):
    sent = {}

    def fake_convert(d, name, *a, **k):
        sent.update(data=d, name=name)
        return {"texts": [{"text": "x"}], "pages": {}}

    monkeypatch.setattr(pipeline, "convert", fake_convert)
    monkeypatch.setattr(pipeline, "update_version", lambda *a, **k: None)
    store = MagicMock()
    store.open.return_value = io.BytesIO(data)
    cfg = settings.model_copy(update={"grobid_url": "", "gotenberg_url": ""})
    v = SimpleNamespace(id="v", blob_sha256="h", filename=filename, content_type="application/octet-stream")
    pipeline._parse(v, cfg, store, None)
    return sent


def test_tsv_converted_before_docling(monkeypatch, settings):
    sent = run_parse(monkeypatch, settings, b"a\tb\n", "t.tsv")
    assert sent == {"data": b"a,b\n", "name": "t.csv"}


def test_unpacked_archive_refused(monkeypatch, settings):
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("a.txt", "1")
        z.writestr("b.txt", "2")
    with pytest.raises(NonRetryable, match="archive was not unpacked"):
        run_parse(monkeypatch, settings, buf.getvalue(), "p.zip")
