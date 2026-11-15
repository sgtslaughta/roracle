import json
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import pytest
from elasticsearch import Elasticsearch
from ingest_helpers import FakeLLM, docling_doc, fake_convert, handler, make_version
from sqlalchemy import select

from roracle.ingest import pipeline
from roracle.ingest.neardup import minhash
from roracle.models import Document, DocumentShare, DocumentVersion, Job
from roracle.queue import enqueue, run_once
from roracle.search_index import index_name

DIMS = 4


@pytest.fixture
def cfg(settings):
    return settings.model_copy(
        update={"embed_model": f"fake-embed-{uuid.uuid4().hex[:8]}", "embed_dims": DIMS, "grobid_url": ""}
    )


@pytest.fixture
def es(cfg):
    c = Elasticsearch(cfg.elastic_url)
    yield c
    c.indices.delete(index=index_name(cfg.embed_model), ignore_unavailable=True)


def _run(db_session, session_factory, cfg, store, es, v, expect_failed=False):
    with session_factory() as s:
        enqueue(s, "ingest", {"version_id": str(v.id)})
        s.commit()
    h = handler(cfg, store, es, FakeLLM(DIMS), session_factory)
    assert run_once(session_factory, {"ingest": h})
    db_session.expire_all()
    job = db_session.scalars(select(Job).order_by(Job.id.desc())).first()
    assert (job.status == "failed") == expect_failed, job.last_error
    return job


def _stage(db_session, v):
    db_session.expire_all()
    return db_session.get(DocumentVersion, v.id).stage


def _chunks(es, cfg, v):
    es.indices.refresh(index=index_name(cfg.embed_model))
    return es.search(index="chunks", query={"term": {"version_id": str(v.id)}}, size=100, sort=["chunk_id"])[
        "hits"
    ]["hits"]


@pytest.mark.integration
def test_full_run_reaches_searchable(db_session, session_factory, cfg, store, es, user, monkeypatch, s3):
    monkeypatch.setattr(pipeline, "convert", fake_convert(docling_doc()))
    v = make_version(db_session, store, user, visibility="shared")
    _run(db_session, session_factory, cfg, store, es, v)
    db_session.expire_all()
    v = db_session.get(DocumentVersion, v.id)
    assert v.stage == "searchable" and v.summary == "A fixed summary." and v.page_count == 1
    assert db_session.get(Document, v.document_id).title == "A Study of Retrieval"
    assert s3.exists(f"derived/{v.id}/docling.json")
    hits = _chunks(es, cfg, v)
    assert hits and hits[0]["_id"] == f"{v.id}:0"
    src = hits[0]["_source"]
    assert src["owner_id"] == str(user.id) and src["visibility"] == "shared"
    assert src["share_users"] == [] and src["org_unit"] == "/" and src["is_latest"] is True
    assert src["title"] == "A Study of Retrieval" and src["pages"] == [1] and len(src["embedding"]) == DIMS
    jobs = db_session.scalars(select(Job).where(Job.kind == "citations")).all()  # R6
    assert [j.payload for j in jobs] == [{"version_id": str(v.id)}]


@pytest.mark.integration
def test_crash_before_searchable_rerun_has_no_duplicates(
    db_session, session_factory, cfg, store, es, user, monkeypatch
):
    monkeypatch.setattr(pipeline, "convert", fake_convert(docling_doc(paras=30)))
    v = make_version(db_session, store, user)
    real = pipeline.search_index.mark_previous_versions

    def boom(*a, **k):
        raise ConnectionError("es down")

    monkeypatch.setattr(pipeline.search_index, "mark_previous_versions", boom)
    _run(db_session, session_factory, cfg, store, es, v)  # retryable failure, job requeued
    assert _stage(db_session, v) == "embedded"
    first = len(_chunks(es, cfg, v))
    es.index(  # stale chunk from an earlier, longer run
        index=index_name(cfg.embed_model), id=f"{v.id}:99", document={"version_id": str(v.id), "doc_id": "x"}
    )
    monkeypatch.setattr(pipeline.search_index, "mark_previous_versions", real)
    with session_factory() as s:  # run_after backoff: make the job due again
        s.execute(Job.__table__.update().values(run_after=Job.__table__.c.created_at))
        s.commit()
    assert run_once(session_factory, {"ingest": handler(cfg, store, es, FakeLLM(DIMS), session_factory)})
    assert _stage(db_session, v) == "searchable"
    hits = _chunks(es, cfg, v)
    assert first >= 1 and len(hits) == first and len({h["_id"] for h in hits}) == first


@pytest.mark.integration
def test_second_version_flips_is_latest(db_session, session_factory, cfg, store, es, user, monkeypatch):
    monkeypatch.setattr(pipeline, "convert", fake_convert(docling_doc()))
    v1 = make_version(db_session, store, user)
    _run(db_session, session_factory, cfg, store, es, v1)
    doc = db_session.get(Document, v1.document_id)
    v2 = make_version(db_session, store, user, doc=doc, version=2)
    _run(db_session, session_factory, cfg, store, es, v2)
    assert {h["_source"]["is_latest"] for h in _chunks(es, cfg, v1)} == {False}
    assert {h["_source"]["is_latest"] for h in _chunks(es, cfg, v2)} == {True}
    db_session.expire_all()
    assert db_session.get(DocumentVersion, v1.id).is_latest is False
    assert db_session.get(DocumentVersion, v2.id).is_latest is True


def test_malware_rejected_and_job_failed(db_session, session_factory, cfg, store, user, monkeypatch):
    cfg = cfg.model_copy(update={"clamav_host": "clam"})
    monkeypatch.setattr(pipeline, "scan", lambda *a, **k: "Eicar-Test")
    v = make_version(db_session, store, user)
    job = _run(db_session, session_factory, cfg, store, MagicMock(), v, expect_failed=True)
    assert _stage(db_session, v) == "rejected"
    assert job.last_error == "user:malware detected: Eicar-Test"


def test_scanner_limit_is_rejected_with_accurate_message(
    db_session, session_factory, cfg, store, user, monkeypatch
):
    cfg = cfg.model_copy(update={"clamav_host": "clam"})
    monkeypatch.setattr(pipeline, "scan", lambda *a, **k: "Heuristics.Limits.Exceeded.MaxScanSize")
    v = make_version(db_session, store, user)
    job = _run(db_session, session_factory, cfg, store, MagicMock(), v, expect_failed=True)
    assert _stage(db_session, v) == "rejected"
    assert job.last_error == "user:file exceeds the malware scanner limits"


def test_soft_deleted_document_does_no_work(db_session, session_factory, cfg, store, user, monkeypatch):
    monkeypatch.setattr(pipeline, "convert", lambda *a, **k: pytest.fail("converted"))
    v = make_version(db_session, store, user)
    db_session.query(Document).update({"deleted_at": datetime.now(UTC)})
    db_session.commit()
    _run(db_session, session_factory, cfg, store, MagicMock(), v)
    assert _stage(db_session, v) == "uploaded"


def test_sixth_start_fails(db_session, session_factory, cfg, store, user):
    v = make_version(db_session, store, user)
    db_session.query(DocumentVersion).update({"ingest_attempts": 5})
    db_session.commit()
    job = _run(db_session, session_factory, cfg, store, MagicMock(), v, expect_failed=True)
    assert job.last_error == "user:ingest crashed repeatedly"


def test_terminal_stage_does_no_work(db_session, session_factory, cfg, store, user, monkeypatch):
    monkeypatch.setattr(pipeline, "convert", lambda *a, **k: pytest.fail("converted"))
    v = make_version(db_session, store, user)
    db_session.query(DocumentVersion).update({"stage": "cancelled"})
    db_session.commit()
    _run(db_session, session_factory, cfg, store, MagicMock(), v)
    assert _stage(db_session, v) == "cancelled"


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


def test_no_title_goes_to_needs_title(db_session, session_factory, cfg, store, user, monkeypatch):
    monkeypatch.setattr(pipeline, "convert", fake_convert(docling_doc(title=None)))
    v = make_version(db_session, store, user)
    _run(db_session, session_factory, cfg, store, MagicMock(), v)
    assert _stage(db_session, v) == "needs_title"
    assert db_session.get(Document, v.document_id).title is None


def test_near_dup_goes_to_review_and_decision_does_not_recheck(
    db_session, session_factory, cfg, store, user, monkeypatch
):
    doc = docling_doc()
    monkeypatch.setattr(pipeline, "convert", fake_convert(doc))
    monkeypatch.setattr(pipeline, "_index", lambda *a, **k: None)
    first = make_version(db_session, store, user)
    text = pipeline.document_text(doc)
    db_session.query(DocumentVersion).filter_by(id=first.id).update(
        {"stage": "searchable", "minhash": minhash(text)}
    )
    db_session.commit()
    monkeypatch.setattr(pipeline, "convert", lambda *a, **k: json.loads(json.dumps(doc)))
    second = make_version(db_session, store, user)
    _run(db_session, session_factory, cfg, store, MagicMock(), second)
    db_session.expire_all()
    s2 = db_session.get(DocumentVersion, second.id)
    assert (s2.stage, s2.near_dup_of) == ("review", first.id)
    # user decided "separate": stage deduped; re-run must not look for near-dups again
    db_session.query(DocumentVersion).filter_by(id=second.id).update({"stage": "deduped"})
    db_session.commit()
    monkeypatch.setattr(pipeline, "find_near_duplicate", lambda *a, **k: pytest.fail("rechecked"))
    _run(db_session, session_factory, cfg, store, MagicMock(), second)


@pytest.mark.integration
def test_older_version_finishing_late_does_not_demote_newer(
    db_session, session_factory, cfg, store, es, user, monkeypatch
):
    monkeypatch.setattr(pipeline, "convert", fake_convert(docling_doc()))
    v1 = make_version(db_session, store, user)
    doc = db_session.get(Document, v1.document_id)
    v2 = make_version(db_session, store, user, doc=doc, version=2)
    db_session.query(DocumentVersion).filter_by(id=v1.id).update({"is_latest": False})
    db_session.commit()
    _run(db_session, session_factory, cfg, store, es, v2)
    _run(db_session, session_factory, cfg, store, es, v1)
    assert {h["_source"]["is_latest"] for h in _chunks(es, cfg, v2)} == {True}
    assert {h["_source"]["is_latest"] for h in _chunks(es, cfg, v1)} == {False}
    db_session.expire_all()
    assert db_session.get(DocumentVersion, v2.id).is_latest is True


@pytest.mark.integration
def test_newer_version_moved_in_mid_run_stays_latest(
    db_session, session_factory, cfg, store, es, user, monkeypatch
):
    monkeypatch.setattr(pipeline, "convert", fake_convert(docling_doc()))
    v1 = make_version(db_session, store, user)
    doc = db_session.get(Document, v1.document_id)
    v2 = make_version(db_session, store, user, doc=doc, version=2)
    made = {}
    real = pipeline.search_index.index_chunks

    def index_then_v3_arrives(*a, **k):
        out = real(*a, **k)
        made["v3"] = make_version(db_session, store, user, doc=doc, version=3)  # near-dup new_version
        return out

    monkeypatch.setattr(pipeline.search_index, "index_chunks", index_then_v3_arrives)
    _run(db_session, session_factory, cfg, store, es, v2)
    monkeypatch.setattr(pipeline.search_index, "index_chunks", real)
    v3 = made["v3"]
    _run(db_session, session_factory, cfg, store, es, v3)
    _run(db_session, session_factory, cfg, store, es, v1)
    db_session.expire_all()
    assert [db_session.get(DocumentVersion, v.id).is_latest for v in (v1, v2, v3)] == [False, False, True]
    assert {h["_source"]["is_latest"] for h in _chunks(es, cfg, v3)} == {True}
    assert {h["_source"]["is_latest"] for h in _chunks(es, cfg, v2)} == {False}
    assert {h["_source"]["is_latest"] for h in _chunks(es, cfg, v1)} == {False}


@pytest.mark.integration
def test_crash_budget_reset_allows_resume_after_title(
    db_session, session_factory, cfg, store, es, user, monkeypatch
):
    monkeypatch.setattr(pipeline, "convert", fake_convert(docling_doc(title=None)))
    v = make_version(db_session, store, user)
    _run(db_session, session_factory, cfg, store, es, v)
    assert _stage(db_session, v) == "needs_title"
    db_session.query(Document).filter_by(id=v.document_id).update({"title": "T"})
    db_session.query(DocumentVersion).filter_by(id=v.id).update({"stage": "parsed", "ingest_attempts": 0})
    db_session.commit()
    _run(db_session, session_factory, cfg, store, es, v)
    assert _stage(db_session, v) == "searchable"


@pytest.mark.integration
def test_chunk_acl_with_shares_and_org_unit(db_session, session_factory, cfg, store, es, user, monkeypatch):
    monkeypatch.setattr(pipeline, "convert", fake_convert(docling_doc()))
    v = make_version(db_session, store, user, visibility="org_unit", org_unit="/Math/Algebra")
    db_session.add_all(
        [
            DocumentShare(document_id=v.document_id, principal_kind="user", principal="u-1"),
            DocumentShare(document_id=v.document_id, principal_kind="group", principal="/Physics"),
        ]
    )
    db_session.commit()
    _run(db_session, session_factory, cfg, store, es, v)
    src = _chunks(es, cfg, v)[0]["_source"]
    assert (src["org_unit"], src["visibility"]) == ("/Math/Algebra", "org_unit")
    assert src["share_users"] == ["u-1"] and src["share_groups"] == ["/Physics"]


def test_llm_down_is_retryable_and_stage_unchanged(
    db_session, session_factory, cfg, store, user, monkeypatch
):
    import httpx
    import openai

    monkeypatch.setattr(pipeline, "convert", fake_convert(docling_doc()))
    llm = FakeLLM(DIMS)

    def down(**kw):
        raise openai.APIConnectionError(request=httpx.Request("POST", "http://x"))

    llm.embeddings.create = down
    v = make_version(db_session, store, user)
    with session_factory() as s:
        enqueue(s, "ingest", {"version_id": str(v.id)})
        s.commit()
    h = handler(cfg, store, MagicMock(), llm, session_factory)
    assert run_once(session_factory, {"ingest": h})
    db_session.expire_all()
    job = db_session.scalars(select(Job)).first()
    assert job.status == "queued" and not job.last_error.startswith("user:")
    assert _stage(db_session, v) == "chunked"


@pytest.mark.integration
def test_tags_indexed_and_new_version_clears_outdated_reasons(
    db_session, session_factory, cfg, store, es, user, monkeypatch
):
    from roracle.models import Tag, Vote

    monkeypatch.setattr(pipeline, "convert", fake_convert(docling_doc()))
    v1 = make_version(db_session, store, user)
    db_session.add_all([Tag(document_id=v1.document_id, tag="rag"), Tag(document_id=v1.document_id, tag="a")])
    db_session.add(Vote(user_id=user.id, document_id=v1.document_id, value=-1, reason="outdated"))
    other = make_version(db_session, store, user)
    db_session.add(Vote(user_id=user.id, document_id=other.document_id, value=-1, reason="outdated"))
    db_session.commit()
    _run(db_session, session_factory, cfg, store, es, v1)
    assert {tuple(h["_source"]["tags"]) for h in _chunks(es, cfg, v1)} == {("a", "rag")}
    db_session.expire_all()
    assert db_session.get(Vote, (user.id, v1.document_id)).reason == "outdated"  # v1 keeps it
    v2 = make_version(db_session, store, user, doc=db_session.get(Document, v1.document_id), version=2)
    _run(db_session, session_factory, cfg, store, es, v2)
    db_session.expire_all()
    mine = db_session.get(Vote, (user.id, v1.document_id))
    assert (mine.value, mine.reason) == (-1, None)
    assert db_session.get(Vote, (user.id, other.document_id)).reason == "outdated"


@pytest.mark.integration
def test_reingest_keeps_votes_cast_after_new_version(
    db_session, session_factory, cfg, store, es, user, monkeypatch
):
    from roracle.models import Vote

    monkeypatch.setattr(pipeline, "convert", fake_convert(docling_doc()))
    v1 = make_version(db_session, store, user)
    doc = db_session.get(Document, v1.document_id)
    v2 = make_version(db_session, store, user, doc=doc, version=2)
    db_session.add(Vote(user_id=user.id, document_id=doc.id, value=-1, reason="outdated"))
    db_session.commit()
    db_session.query(DocumentVersion).filter_by(id=v2.id).update(
        {"created_at": datetime.now(UTC) + timedelta(hours=1)}
    )
    db_session.commit()
    _run(db_session, session_factory, cfg, store, es, v2)
    db_session.expire_all()
    assert db_session.get(Vote, (user.id, doc.id)).reason is None
    db_session.get(Vote, (user.id, doc.id)).reason = "outdated"  # voted after v2 appeared
    db_session.query(DocumentVersion).filter_by(id=v2.id).update(
        {"created_at": datetime.now(UTC) - timedelta(hours=1), "stage": "uploaded"}
    )
    db_session.commit()
    _run(db_session, session_factory, cfg, store, es, v2)
    db_session.expire_all()
    assert db_session.get(Vote, (user.id, doc.id)).reason == "outdated"


@pytest.mark.integration
def test_acl_changed_between_read_and_index_converges(
    db_session, session_factory, cfg, store, es, user, monkeypatch
):
    monkeypatch.setattr(pipeline, "convert", fake_convert(docling_doc()))
    v = make_version(db_session, store, user, visibility="everyone")
    real = pipeline.search_index.index_chunks

    def patch_lands_first(*a, **k):  # PATCH commits after ingest read the ACL but before the bulk index
        with session_factory() as s:
            s.query(Document).filter_by(id=v.document_id).update({"visibility": "private"})
            s.commit()
        return real(*a, **k)

    monkeypatch.setattr(pipeline.search_index, "index_chunks", patch_lands_first)
    _run(db_session, session_factory, cfg, store, es, v)
    hits = _chunks(es, cfg, v)
    assert hits and {h["_source"]["visibility"] for h in hits} == {"private"}
