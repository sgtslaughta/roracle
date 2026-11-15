from sqlalchemy import select

from roracle.ingest.state import bump_attempts, set_stage
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
