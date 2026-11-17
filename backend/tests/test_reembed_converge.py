"""Task 10.11 fix round: a failed slice refuses the cutover (C1); the target converges with Postgres before the swap (I1)."""
# ruff: noqa: F811

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from reembed_support import FakeLlm, base, drain, es  # noqa: F401
from sqlalchemy import select

from roracle import reembed, search_index
from roracle.models import Document, DocumentVersion, Job
from roracle.queue import NonRetryable


@pytest.fixture
def two_docs(db_session, document, blob):
    out = []
    for i, d in enumerate("ab"):
        doc = Document(owner_id=document.owner_id, title=f"t{d}", org_unit="/", visibility="everyone")
        db_session.add(doc)
        db_session.flush()
        v = DocumentVersion(
            id=uuid.UUID(f"{d}0000000-0000-4000-8000-000000000000"), document_id=doc.id, version=1,
            blob_sha256=blob.sha256, filename="a.pdf", content_type="x", size=1, stage="searchable", is_latest=True,
        )  # fmt: skip
        db_session.add(v)
        out.append((doc, str(v.id)))
    db_session.commit()
    return out


def _run_one(db, sf, es, llm, s):
    j = db.scalar(select(Job).where(Job.kind == "reembed", Job.status == "queued").order_by(Job.id))
    j.status = "done"
    db.flush()
    reembed.handle_reembed(db, j.payload, es=es, llm=llm, session_factory=sf, settings=s)
    db.commit()
    return j


def test_a_permanently_failed_slice_refuses_the_cutover_and_is_visible_in_status(
    db_session, session_factory, es, base, two_docs
):
    s = base.model_copy(update={"reembed_slices": 2})
    for doc, vid in two_docs:
        es.put(f"{vid}:0", doc.id)
    reembed.start(db_session, es, s, "new", 2)
    db_session.commit()
    jobs = list(db_session.scalars(select(Job).where(Job.kind == "reembed").order_by(Job.id)))
    jobs[0].status, jobs[0].last_error = "failed", "embedding endpoint exploded"  # slice 0 died for good
    db_session.commit()
    llm = FakeLlm(2)
    _run_one(db_session, session_factory, es, llm, s)  # slice 1 finishes: it is the last one running
    assert db_session.scalars(select(Job).where(Job.kind == "reembed", Job.status == "queued")).all() == []
    assert not db_session.scalars(select(Job).where(Job.kind == "reconcile_tags")).all()
    assert es.indices.alias_calls == []  # the read alias never moved
    st = reembed.status(db_session, es, s)
    assert st["running"] is None
    assert st["failed"]["slices"] == [{"i": 0, "error": "embedding endpoint exploded"}]
    assert st["failed"]["target"]["index"] == "chunks-new-1"


def test_cutover_itself_refuses_when_a_slice_failed_after_it_was_enqueued(
    db_session, session_factory, es, base, two_docs
):
    s = base.model_copy(update={"reembed_slices": 2})
    for doc, vid in two_docs:
        es.put(f"{vid}:0", doc.id)
    reembed.start(db_session, es, s, "new", 2)
    db_session.commit()
    j0, j1 = db_session.scalars(select(Job).where(Job.kind == "reembed").order_by(Job.id)).all()
    cut = {**j1.payload, "phase": "cutover"}
    j0.status = "failed"
    db_session.commit()
    with pytest.raises(NonRetryable, match="refused"):
        reembed.handle_reembed(
            db_session, cut, es=es, llm=FakeLlm(2), session_factory=session_factory, settings=s
        )
    assert es.indices.alias_calls == []


def test_target_is_converged_with_postgres_before_the_swap_without_clocks(
    db_session, session_factory, es, base, two_docs, monkeypatch
):
    """Mid-copy: doc a's ACL is revoked, doc b is soft-deleted (with a deleted_at far in the PAST: clock skew must not
    matter), doc a gets a newer version, and an orphan chunk of a purged document sits in the target."""
    (da, va), (db_, vb) = two_docs
    es.put(f"{va}:0", da.id)
    es.put(f"{vb}:0", db_.id)
    reembed.start(db_session, es, base, "new", 2)
    db_session.commit()
    order = []
    _run_one(db_session, session_factory, es, FakeLlm(2), base)  # the copy (both chunks land in the target)
    assert set(es.data["chunks-new-1"]) == {f"{va}:0", f"{vb}:0"}
    es.put(f"{uuid.uuid4()}:0", uuid.uuid4(), index="chunks-new-1")  # purged document's chunk (no PG row)
    # --- mutations that ran while the target was not yet refreshed, so ES never applied them to it ---
    da.visibility = "private"  # revoke
    db_.deleted_at = datetime.now(UTC) - timedelta(days=30)  # skewed / old timestamp
    v1 = db_session.get(DocumentVersion, uuid.UUID(va))
    v1.is_latest = False  # PG: version 2 superseded it
    db_session.add(
        DocumentVersion(
            document_id=da.id, version=2, blob_sha256=v1.blob_sha256, filename="a2.pdf", content_type="x",
            size=1, stage="searchable", is_latest=True, created_at=datetime.now(UTC) - timedelta(days=30),
        )
    )  # fmt: skip
    db_session.commit()
    es.drift_docs = [str(da.id)]  # what the acl_hash comparison against Postgres reports
    synced = []
    monkeypatch.setattr(
        search_index, "sync_acl", lambda es_, d, compute: (synced.append(d), order.append("acl"), compute())
    )
    real_update = es.indices.update_aliases
    es.indices.update_aliases = lambda actions: (order.append("swap"), real_update(actions))
    _run_one(db_session, session_factory, es, FakeLlm(2), base)  # the cutover
    assert synced == [str(da.id)]  # revoke pushed to the target
    assert set(es.data["chunks-new-1"]) == {f"{va}:0"}  # soft-deleted doc and the orphan are gone
    (up,) = [u for u in es.updates if u["index"] == "chunks-new-1"]
    assert up["query"]["bool"]["filter"][0] == {
        "terms": {"version_id": [va]}
    }  # v1 demoted by id, not by date
    assert order == ["acl", "swap"]  # ALL of it before readers move, one swap


def test_copy_target_refreshes_every_30s_and_keeps_replicas(db_session, session_factory, es, base, two_docs):
    for doc, vid in two_docs:
        es.put(f"{vid}:0", doc.id)
    reembed.start(db_session, es, base, "new", 2)
    db_session.commit()
    _run_one(db_session, session_factory, es, FakeLlm(2), base)
    created = es.created["chunks-new-1"]
    assert created["refresh_interval"] == "30s" and created["number_of_replicas"] != 0
