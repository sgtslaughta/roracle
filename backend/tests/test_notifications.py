import uuid

import pytest
from sqlalchemy import select

from roracle import queue
from roracle.ingest import pipeline
from roracle.ingest.neardup import minhash
from roracle.ingest.state import mark_searchable_and_enqueue
from roracle.models import Blob, Connector, Document, DocumentVersion, Job, Notification, User
from roracle.notify import emit, emit_admins
from roracle.queue import NonRetryable, enqueue, run_once


def _doc(db_session, owner, *, title="Doc", **kw):
    d = Document(owner_id=owner.id, title=title, org_unit="/", visibility="everyone", **kw)
    db_session.add(d)
    db_session.flush()
    sha = uuid.uuid4().hex + uuid.uuid4().hex
    db_session.add(Blob(sha256=sha, size=1))
    v = DocumentVersion(
        document_id=d.id, version=1, blob_sha256=sha, filename="p.pdf", content_type="application/pdf", size=1
    )
    db_session.add(v)
    db_session.commit()
    return d, v


def _notes(db_session, user=None):
    db_session.expire_all()
    q = select(Notification).order_by(Notification.id)
    if user is not None:
        q = q.where(Notification.user_id == user.id)
    return list(db_session.scalars(q))


def _fail_job(session_factory, kind, payload, exc):
    def boom(s, p):
        raise exc

    with session_factory() as s:
        enqueue(s, kind, payload)
        s.commit()
    assert run_once(session_factory, {kind: boom})


def test_searchable_emits_once(db_session, session_factory, user):
    d, v = _doc(db_session, user)
    mark_searchable_and_enqueue(session_factory, v.id)
    (n,) = _notes(db_session)
    assert (n.user_id, n.kind, n.read_at) == (user.id, "upload.searchable", None)
    assert n.payload == {
        "document_id": str(d.id),
        "version_id": str(v.id),
        "filename": "p.pdf",
        "title": "Doc",
    }


def test_connector_doc_no_notifications(db_session, session_factory, user):
    c = Connector(name="c", kind="fs", owner_id=user.id, visibility="everyone")
    db_session.add(c)
    db_session.flush()
    _, v = _doc(db_session, user, connector_id=c.id)
    mark_searchable_and_enqueue(session_factory, v.id)
    _fail_job(session_factory, "ingest", {"version_id": str(v.id)}, NonRetryable("bad"))
    assert _notes(db_session) == []


def test_near_dup_emits(db_session, session_factory, cfg, user):
    text_ = "alpha beta gamma delta " * 200
    d1, v1 = _doc(db_session, user, title="Original")
    v1.minhash, v1.stage, v1.is_latest = minhash(text_), "searchable", True
    d2, v2 = _doc(db_session, user, title="Copy")
    db_session.commit()
    assert pipeline._dedupe(db_session, v2, d2, text_, cfg, session_factory) is True
    (n,) = _notes(db_session)
    assert n.kind == "upload.near_dup" and n.user_id == user.id
    assert n.payload == {
        "document_id": str(d2.id),
        "version_id": str(v2.id),
        "filename": "p.pdf",
        "match_document_id": str(d1.id),
    }


@pytest.mark.parametrize(
    ("exc", "shown"),
    [(NonRetryable("file is encrypted"), "file is encrypted"), (RuntimeError("secret"), "processing failed")],
)
def test_ingest_failure_notifies_owner(db_session, session_factory, user, exc, shown, monkeypatch):
    monkeypatch.setattr(queue, "MAX_ATTEMPTS", 1)
    d, v = _doc(db_session, user)
    _fail_job(session_factory, "ingest", {"version_id": str(v.id)}, exc)
    (n,) = _notes(db_session)
    assert (n.user_id, n.kind) == (user.id, "upload.failed")
    assert n.payload == {
        "document_id": str(d.id),
        "version_id": str(v.id),
        "filename": "p.pdf",
        "error": shown,
    }


def test_retryable_failure_is_silent(db_session, session_factory, user):
    _, v = _doc(db_session, user)
    _fail_job(session_factory, "ingest", {"version_id": str(v.id)}, RuntimeError("x"))
    assert _notes(db_session) == []


def test_other_job_failure_notifies_every_admin(db_session, session_factory, user, monkeypatch):
    monkeypatch.setattr(queue, "MAX_ATTEMPTS", 1)
    a1 = User(sub="a1", username="a1", is_admin=True)
    a2 = User(sub="a2", username="a2", is_admin=True)
    db_session.add_all([a1, a2])
    db_session.commit()
    _fail_job(session_factory, "citations", {"x": 1}, RuntimeError("secret"))
    notes = _notes(db_session)
    assert sorted(n.user_id for n in notes) == sorted([a1.id, a2.id])
    assert {n.kind for n in notes} == {"ops.job_failed"}
    assert notes[0].payload["kind"] == "citations" and notes[0].payload["error"] == "processing failed"


def test_emitter_exception_never_breaks_queue(db_session, session_factory, user, monkeypatch):
    from roracle import notify

    monkeypatch.setattr(queue, "MAX_ATTEMPTS", 1)
    monkeypatch.setattr(notify, "on_job_failed", lambda *a: 1 / 0)
    j = _fail_job_row(session_factory, db_session, "citations", {}, RuntimeError("x"))
    assert _notes(db_session) == [] and (j.status, j.last_error) == ("failed", "x")


def _fail_job_row(session_factory, db_session, kind, payload, exc):
    _fail_job(session_factory, kind, payload, exc)
    db_session.expire_all()
    return db_session.scalars(select(Job).where(Job.kind == kind)).one()


def test_hook_writing_then_raising_rolls_back(db_session, session_factory, user, monkeypatch):
    from roracle import notify

    def hook(session, job):
        emit(session, user.id, "k", {})
        raise RuntimeError("late")

    monkeypatch.setattr(queue, "MAX_ATTEMPTS", 1)
    monkeypatch.setattr(notify, "on_job_failed", hook)
    j = _fail_job_row(session_factory, db_session, "citations", {}, RuntimeError("x"))
    assert _notes(db_session) == [] and j.status == "failed"


def test_connector_ingest_failure_notifies_admins(db_session, session_factory, user, monkeypatch):
    monkeypatch.setattr(queue, "MAX_ATTEMPTS", 1)
    admin = User(sub="a1", username="a1", is_admin=True)
    c = Connector(name="c", kind="fs", owner_id=user.id, visibility="everyone")
    db_session.add_all([admin, c])
    db_session.flush()
    d, v = _doc(db_session, user, connector_id=c.id)
    _fail_job(session_factory, "ingest", {"version_id": str(v.id)}, NonRetryable("bad"))
    (n,) = _notes(db_session)
    assert (n.user_id, n.kind) == (admin.id, "ops.job_failed")
    assert n.payload["document_id"] == str(d.id) and n.payload["connector_id"] == str(c.id)
    assert n.payload["error"] == "processing failed"


def test_near_dup_connector_doc_no_notification(db_session, session_factory, cfg, user):
    text_ = "alpha beta gamma delta " * 200
    _, v1 = _doc(db_session, user, title="Original")
    v1.minhash, v1.stage, v1.is_latest = minhash(text_), "searchable", True
    c = Connector(name="c", kind="fs", owner_id=user.id, visibility="everyone")
    db_session.add(c)
    db_session.flush()
    d2, v2 = _doc(db_session, user, title="Copy", connector_id=c.id)
    assert pipeline._dedupe(db_session, v2, d2, text_, cfg, session_factory) is True
    assert _notes(db_session) == []


def test_inbox_paging_unread_and_read(db_session, client, ana, as_ana, bob, as_bob):
    for i in range(3):
        emit(db_session, ana, "k", {"i": i})
    emit(db_session, bob, "k", {"i": 99})
    db_session.commit()
    r = client.get("/me/notifications?limit=2", headers=as_ana).json()
    assert [n["payload"]["i"] for n in r["items"]] == [2, 1] and r["unread"] == 3
    r2 = client.get(f"/me/notifications?limit=2&before_id={r['next_before_id']}", headers=as_ana).json()
    assert [n["payload"]["i"] for n in r2["items"]] == [0] and r2["next_before_id"] is None
    ids = [n["id"] for n in r["items"]]
    assert client.post("/me/notifications/read", json={"ids": ids[:1]}, headers=as_ana).json() == {
        "updated": 1
    }
    r = client.get("/me/notifications?unread_only=true", headers=as_ana).json()
    assert len(r["items"]) == 2 and r["unread"] == 2
    assert client.post("/me/notifications/read", json={"all": True}, headers=as_ana).json() == {"updated": 2}


def test_cannot_mark_other_users_notification(db_session, client, ana, as_ana, bob, as_bob):
    emit(db_session, bob, "k", {})
    db_session.commit()
    bid = _notes(db_session)[0].id
    assert client.post("/me/notifications/read", json={"ids": [bid]}, headers=as_ana).json() == {"updated": 0}
    assert client.get("/me/notifications", headers=as_ana).json()["items"] == []
    assert _notes(db_session)[0].read_at is None


def test_emit_admins_skips_non_admins(db_session, user):
    admin = User(sub="ad", username="ad", is_admin=True)
    db_session.add(admin)
    db_session.flush()
    emit_admins(db_session, "k", {})
    assert [n.user_id for n in _notes(db_session)] == [admin.id]
