import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from api_seed import seed
from sqlalchemy import select

from roracle.ingest import reparse
from roracle.models import DocCitation, Job

TEI = (Path(__file__).parent / "fixtures" / "grobid_tei_ids.xml").read_bytes()
cfg = SimpleNamespace(grobid_url="http://g", grobid_timeout_s=5.0)


def _store(doc=None):
    s = MagicMock()
    s.open.return_value.read.return_value = b"%PDF"
    s.get_bytes.return_value = json.dumps(doc or {}).encode()
    return s


@pytest.fixture
def grobid(monkeypatch):
    from roracle.ingest.parse import parse_tei

    monkeypatch.setattr(reparse, "grobid_metadata", lambda *a, **k: parse_tei(TEI))


def test_handler_updates_refs_ids_and_links(db_session, ana, grobid):
    target, tv = seed(db_session, ana, title="Totally Different Title", year=2017)
    tv.meta = {**tv.meta, "ids": {"arxiv": "1706.03762"}}
    _, v = seed(db_session, ana, title="User Edited Title", authors=("Me",))
    v.references = [{"title": "old", "year": 1}]
    db_session.flush()
    doc = {"texts": [{"text": "arXiv:2611.20773v1 [cs.MA]", "prov": [{"page_no": 1}]}]}
    reparse.handle_reparse_refs(db_session, {"version_id": str(v.id)}, settings=cfg, store=_store(doc))
    db_session.refresh(v)
    assert v.meta["authors"] == ["Me"] and v.meta["venue"] == "NeurIPS"  # untouched
    assert v.meta["ids"]["arxiv"] == "2611.20773" and v.meta["doi"] == "10.1234/agents.1"
    assert len(v.references) == 4 and v.references[0]["ids"] == {"arxiv": "1706.03762"}
    rows = db_session.scalars(select(DocCitation).where(DocCitation.src_version_id == v.id)).all()
    assert [(r.ref_no, r.target_document_id) for r in rows] == [(1, target.id)]


def test_handler_empty_grobid_keeps_refs_and_skips_when_disabled(db_session, ana, monkeypatch):
    _, v = seed(db_session, ana)
    v.references = [{"title": "keep", "year": 1}]
    db_session.flush()
    monkeypatch.setattr(reparse, "grobid_metadata", lambda *a, **k: ({}, []))
    reparse.handle_reparse_refs(db_session, {"version_id": str(v.id)}, settings=cfg, store=_store())
    assert v.references == [{"title": "keep", "year": 1}] and v.meta["ids"] == {}
    _, v2 = seed(db_session, ana)
    off = SimpleNamespace(grobid_url="", grobid_timeout_s=5.0)
    reparse.handle_reparse_refs(db_session, {"version_id": str(v2.id)}, settings=off, store=_store())
    assert "ids" not in v2.meta
    _, v3 = seed(db_session, ana, filename="a.docx", content_type="application/msword")
    reparse.handle_reparse_refs(db_session, {"version_id": str(v3.id)}, settings=cfg, store=_store())
    assert "ids" not in v3.meta


def test_enqueue_missing_only_versions_without_ids_and_dedupes(db_session, ana):
    _, need = seed(db_session, ana)
    _, done = seed(db_session, ana)
    done.meta = {**done.meta, "ids": {}}
    seed(db_session, ana, filename="x.docx", content_type="application/msword")
    seed(db_session, ana, stage="parsed")
    db_session.flush()
    assert reparse.enqueue_missing(db_session) == 1
    jobs = db_session.scalars(select(Job).where(Job.kind == "reparse_refs")).all()
    assert [j.payload for j in jobs] == [{"version_id": str(need.id)}]
    assert reparse.enqueue_missing(db_session) == 0  # idempotent: already queued
    seed(db_session, ana)
    db_session.flush()
    assert reparse.enqueue_missing(db_session, limit=1) == 1
