import time
from types import SimpleNamespace

import pytest
from sqlalchemy import select, update
from sqlalchemy.exc import OperationalError

from roracle.ingest.state import _demote, bump_attempts, set_stage, update_version
from roracle.models import DocumentVersion, Job
from roracle.queue import claim, enqueue


def _version(db_session, document, blob):
    v = DocumentVersion(
        document_id=document.id, version=1, blob_sha256=blob.sha256, filename="a", content_type="x", size=1
    )
    db_session.add(v)
    db_session.commit()
    return v.id


def demote_older_versions(session_factory, doc_id, version_id):  # test harness over the internal helper
    with session_factory() as s:
        _demote(s, doc_id, version_id)
        s.commit()


def test_set_stage_and_bump_attempts(session_factory, db_session, document, blob):
    vid = _version(db_session, document, blob)
    set_stage(session_factory, vid, "scanned")
    assert bump_attempts(session_factory, vid) == 1
    assert bump_attempts(session_factory, vid) == 2
    with session_factory() as s:
        v = s.get(DocumentVersion, vid)
        assert (v.stage, v.ingest_attempts) == ("scanned", 2)


def test_crash_keeps_stage_and_job_reclaimable(session_factory, db_session, document, blob):
    vid = _version(db_session, document, blob)
    with session_factory() as s:
        enqueue(s, "ingest", {"version_id": str(vid)})
        s.commit()
    crashed = session_factory()
    assert claim(crashed, ["ingest"]) is not None
    set_stage(session_factory, vid, "parsed")  # separate session: commits while claim lock is held
    crashed.close()  # no commit: worker died
    with session_factory() as s:
        assert s.get(DocumentVersion, vid).stage == "parsed"
        again = claim(s, ["ingest"])
        assert again is not None and again.attempts == 1
        s.rollback()
        assert s.scalar(select(Job.status)) == "queued"


def test_update_version_missing_raises(session_factory):
    import uuid

    with pytest.raises(LookupError):
        update_version(session_factory, uuid.uuid4(), stage="parsed")


def test_lock_conflict_fails_fast_not_hangs(session_factory, db_session, document, blob):
    vid = _version(db_session, document, blob)
    holder = session_factory()
    holder.execute(update(DocumentVersion).where(DocumentVersion.id == vid).values(summary="x"))
    try:
        t = time.monotonic()
        with pytest.raises(OperationalError):
            set_stage(session_factory, vid, "parsed", lock_timeout_s=0.5)
        with pytest.raises(OperationalError):
            bump_attempts(session_factory, vid, lock_timeout_s=0.5)
        assert time.monotonic() - t < 5
    finally:
        holder.rollback()
        holder.close()


def test_get_session_uses_app_state_factory(settings):
    from roracle.api.app import create_app
    from roracle.db import get_session, make_session_factory

    app = create_app(settings)
    factory = make_session_factory(settings.database_url)
    app.state.session_factory = factory
    gen = get_session(SimpleNamespace(app=app))
    s = next(gen)
    assert s.get_bind() is factory.kw["bind"]
    gen.close()


def _versions(db_session, document, blob, flags, stages=None):
    ids = []
    for n, latest in enumerate(flags, 1):
        v = DocumentVersion(
            document_id=document.id, version=n, blob_sha256=blob.sha256, filename="a",
            content_type="x", size=1, is_latest=latest,
            stage=(stages[n - 1] if stages else "searchable"),
        )  # fmt: skip
        db_session.add(v)
        db_session.flush()
        ids.append(v.id)
    db_session.commit()
    return ids


def _flags(db_session, ids):
    db_session.expire_all()
    return [db_session.get(DocumentVersion, i).is_latest for i in ids]


def test_demote_only_older_and_restores_own_flag(session_factory, db_session, document, blob):
    ids = _versions(db_session, document, blob, [True, True, False])  # v3 wrongly cleared
    demote_older_versions(session_factory, document.id, ids[2])
    assert _flags(db_session, ids) == [False, False, True]


def test_demote_by_older_version_never_touches_newer(session_factory, db_session, document, blob):
    ids = _versions(db_session, document, blob, [True, True, True])
    demote_older_versions(session_factory, document.id, ids[1])
    assert _flags(db_session, ids) == [False, False, True]


def test_promotion_demotes_and_marks_searchable_atomically(
    session_factory, db_session, document, blob, monkeypatch
):
    from roracle.ingest import state

    ids = _versions(db_session, document, blob, [True, False])
    db_session.execute(
        DocumentVersion.__table__.update().where(DocumentVersion.id == ids[1]).values(stage="embedded")
    )  # the promoted version is mid-ingest (an already-searchable one is a no-op by design)
    db_session.commit()
    monkeypatch.setattr(state, "enqueue", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    with pytest.raises(RuntimeError):
        state.mark_searchable_and_enqueue(session_factory, ids[1], demote_doc=document.id)
    assert _flags(db_session, ids) == [True, False]  # all or nothing: the old version is still live
    monkeypatch.undo()
    state.mark_searchable_and_enqueue(session_factory, ids[1], demote_doc=document.id)
    db_session.expire_all()
    assert _flags(db_session, ids) == [False, True]
    assert db_session.get(DocumentVersion, ids[1]).stage == "searchable"


def test_demote_ignores_newer_unfinished_version(session_factory, db_session, document, blob):
    # v1 live, v3 only uploaded, v2 finishes: v2 must become the latest (v3 has no flag to keep)
    ids = _versions(db_session, document, blob, [True, False, False], ["searchable", "chunked", "uploaded"])
    demote_older_versions(session_factory, document.id, ids[1])
    assert _flags(db_session, ids) == [False, True, False]


def test_demote_then_newer_fails_keeps_latest(session_factory, db_session, document, blob):
    ids = _versions(db_session, document, blob, [True, False, False], ["searchable", "chunked", "uploaded"])
    demote_older_versions(session_factory, document.id, ids[1])
    db_session.execute(update(DocumentVersion).where(DocumentVersion.id == ids[2]).values(stage="rejected"))
    db_session.commit()
    assert _flags(db_session, ids) == [False, True, False]
