from unittest.mock import MagicMock

from ingest_helpers import docling_doc, fake_convert, make_version
from test_ingest_handler import _run, _stage

from roracle.ingest import pipeline
from roracle.models import Document, DocumentVersion


def _stamp(monkeypatch, text, grobid):
    doc = docling_doc()
    doc["texts"][0]["prov"] = [{"page_no": 1}]
    doc["texts"][0]["text"] = text
    monkeypatch.setattr(pipeline, "convert", fake_convert(doc))
    monkeypatch.setattr(pipeline, "grobid_metadata", grobid)
    monkeypatch.setattr(pipeline, "_index", lambda *a, **k: None)


def _ids(db_session, session_factory, cfg, store, user, monkeypatch, text, grobid, url="http://grobid"):
    _stamp(monkeypatch, text, grobid)
    v = make_version(db_session, store, user)
    _run(db_session, session_factory, cfg.model_copy(update={"grobid_url": url}), store, MagicMock(), v)
    db_session.expire_all()
    return db_session.get(DocumentVersion, v.id).meta


def test_first_page_stamp_merged_without_overriding_grobid(
    db_session, session_factory, cfg, store, user, monkeypatch
):
    g = lambda *a, **k: ({"title": "T", "doi": "10.9999/g", "ids": {"doi": "10.9999/g"}}, [])
    meta = _ids(
        db_session, session_factory, cfg, store, user, monkeypatch,
        "arXiv:2611.20773v1 [cs.MA] 20 Nov 2026 doi:10.1234/stamp", g,
    )  # fmt: skip
    assert meta["ids"] == {"doi": "10.9999/g", "arxiv": "2611.20773"}


def test_footnote_without_stamp_gives_no_arxiv_id(db_session, session_factory, cfg, store, user, monkeypatch):
    g = lambda *a, **k: ({"title": "T", "ids": {}}, [])
    meta = _ids(db_session, session_factory, cfg, store, user, monkeypatch, "BERT (arXiv:1810.04805)", g)
    assert meta["ids"] == {}


def test_no_ids_marker_without_parsed_grobid(db_session, session_factory, cfg, store, user, monkeypatch):
    stamp = "arXiv:2611.20773v1 [cs.MA]"
    empty = lambda *a, **k: ({}, [])
    assert "ids" not in _ids(db_session, session_factory, cfg, store, user, monkeypatch, stamp, empty)
    assert "ids" not in _ids(db_session, session_factory, cfg, store, user, monkeypatch, stamp, empty, url="")


def test_grobid_failure_is_tolerated(db_session, session_factory, cfg, store, user, monkeypatch):
    cfg = cfg.model_copy(update={"grobid_url": "http://grobid"})

    def bad(*a, **k):
        raise ConnectionError("grobid down")

    monkeypatch.setattr(pipeline, "convert", fake_convert(docling_doc()))
    monkeypatch.setattr(pipeline, "grobid_metadata", bad)
    monkeypatch.setattr(pipeline, "_index", lambda *a, **k: None)
    v = make_version(db_session, store, user)
    _run(db_session, session_factory, cfg, store, MagicMock(), v)
    assert _stage(db_session, v) == "deduped"


def test_grobid_title_beats_docling(db_session, session_factory, cfg, store, user, monkeypatch):
    cfg = cfg.model_copy(update={"grobid_url": "http://grobid"})
    monkeypatch.setattr(pipeline, "convert", fake_convert(docling_doc()))
    monkeypatch.setattr(pipeline, "grobid_metadata", lambda *a, **k: ({"title": "GROBID Title"}, []))
    monkeypatch.setattr(pipeline, "_index", lambda *a, **k: None)
    v = make_version(db_session, store, user)
    _run(db_session, session_factory, cfg, store, MagicMock(), v)
    db_session.expire_all()
    assert db_session.get(Document, v.document_id).title == "GROBID Title"
