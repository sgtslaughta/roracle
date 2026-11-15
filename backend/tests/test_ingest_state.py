import time
from types import SimpleNamespace

import pytest
from sqlalchemy import select, update
from sqlalchemy.exc import OperationalError

from roracle.ingest.state import bump_attempts, set_stage, update_version
from roracle.models import DocumentVersion, Job
from roracle.queue import claim, enqueue


def _version(db_session, document, blob):
    v = DocumentVersion(
        document_id=document.id, version=1, blob_sha256=blob.sha256, filename="a", content_type="x", size=1
    )
    db_session.add(v)
    db_session.commit()
    return v.id


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
