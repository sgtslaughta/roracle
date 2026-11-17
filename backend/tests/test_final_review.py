"""Whole-branch review wave: stale passages, superseded demotion, drain cap, ..."""

from types import SimpleNamespace

import pytest
from api_seed import FakeES, seed
from sqlalchemy import select
from test_search_api import FakeLLM, hit

from roracle.api.routes.library import get_es
from roracle.api.routes.search import get_llm
from roracle.ingest import pipeline
from roracle.models import DocumentVersion

pytestmark = pytest.mark.usefixtures("_blob_store_override")


def test_search_filters_stale_passages_but_keeps_the_document(client, as_ana, ana, db_session):
    d, v2 = seed(db_session, ana, title="Doc", versions=2)
    old = db_session.scalar(
        select(DocumentVersion).where(DocumentVersion.document_id == d.id, DocumentVersion.version == 1)
    )
    fes = FakeES([hit(d, old, text="stale alpha", chunk=0), hit(d, v2, text="fresh alpha", chunk=1)])
    client.app.dependency_overrides[get_es] = lambda: fes
    client.app.dependency_overrides[get_llm] = lambda: FakeLLM()
    res = client.get("/search", headers=as_ana, params={"q": "alpha"}).json()["results"]
    assert len(res) == 1  # not dropped
    texts = " ".join(p["text"] for p in res[0]["passages"])
    assert "fresh" in texts and "stale" not in texts


def test_superseded_version_is_demoted_after_the_refresh(db_session, session_factory, ana):
    d, v2 = seed(db_session, ana, title="Doc", versions=2)
    v1 = db_session.scalar(
        select(DocumentVersion).where(DocumentVersion.document_id == d.id, DocumentVersion.version == 1)
    )
    db_session.commit()
    calls = []
    es = SimpleNamespace(update_by_query=lambda **k: calls.append(k))
    pipeline._demote_if_superseded(session_factory, es, v2, d, "chunks-x", True)
    assert calls == []  # still the latest
    pipeline._demote_if_superseded(session_factory, es, v1, d, "chunks-x", False)
    assert calls == []  # was not latest to begin with
    pipeline._demote_if_superseded(session_factory, es, v1, d, "chunks-x", True)
    (c,) = calls
    assert c["index"] == "chunks-x" and {"term": {"version_id": str(v1.id)}} in c["query"]["bool"]["filter"]


def test_shutdown_release_is_capped_then_fails_with_a_clear_error(session_factory, db_session):
    from sqlalchemy import text

    from roracle import queue
    from roracle.models import Job
    from roracle.queue import enqueue, run_once

    with session_factory() as s:
        jid = enqueue(s, "ingest", {"version_id": "x"})
        s.execute(text("UPDATE jobs SET released = :n WHERE id = :i"), {"n": queue.MAX_REFUNDS, "i": jid})
        s.commit()

    def handler(session, payload):
        assert queue.release_active()

    assert run_once(session_factory, {"ingest": handler}, lease_s=60, heartbeat_s=0.05)
    with session_factory() as s:
        j = s.get(Job, jid)
        assert j.status == "failed" and "drain window" in j.last_error


def test_reconcile_tags_is_claimed_before_a_rebuild_backlog(session_factory):
    from roracle.queue import claim, enqueue

    with session_factory() as s:
        for _ in range(3):
            enqueue(s, "rebuild_units", {"version_ids": []})
        enqueue(s, "reconcile_tags", {})
        s.commit()
    with session_factory() as s:
        job = claim(s, ["rebuild_units", "reconcile_tags", "ingest"])
        assert job.kind == "reconcile_tags"
        s.commit()
