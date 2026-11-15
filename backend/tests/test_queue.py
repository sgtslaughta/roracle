from datetime import timedelta

from sqlalchemy import func, select, text

from roracle.models import Job
from roracle.queue import claim, complete, enqueue, fail, run_once


def _job(session_factory, kind="ingest") -> int:
    with session_factory() as s:
        jid = enqueue(s, kind, {"v": 1})
        s.commit()
    return jid


def test_enqueue_is_part_of_callers_transaction(session_factory, db_session):
    with session_factory() as s:
        enqueue(s, "ingest", {"v": 1})
        s.rollback()
    with session_factory() as s:
        assert s.scalar(select(func.count()).select_from(Job)) == 0


def test_two_workers_never_claim_the_same_job(session_factory, db_session):
    _job(session_factory)
    a, b = session_factory(), session_factory()
    try:
        assert claim(a, ["ingest"]) is not None
        assert claim(b, ["ingest"]) is None  # a still holds the row lock
    finally:
        a.rollback()
        b.rollback()
        a.close()
        b.close()


def test_claim_ignores_kinds_without_handler(session_factory, db_session):
    jid = _job(session_factory, "other")
    with session_factory() as s:
        assert claim(s, ["ingest"]) is None
        assert s.get(Job, jid).status == "queued"


def test_claim_marks_running_and_counts_attempt(session_factory, db_session):
    _job(session_factory)
    with session_factory() as s:
        job = claim(s, ["ingest"])
        assert (job.status, job.attempts) == ("running", 1)


def test_failure_backs_off_then_fails_after_five_attempts(session_factory, db_session):
    jid = _job(session_factory)
    for attempt in range(1, 6):
        with session_factory() as s:
            s.execute(text("UPDATE jobs SET run_after = now() WHERE id = :i"), {"i": jid})
            job = claim(s, ["ingest"])
            assert job.attempts == attempt
            fail(s, job, "boom" * 1000 if attempt == 5 else "boom")
            s.commit()
            s.refresh(job)
            if attempt == 1:
                assert job.status == "queued"
                delta = job.run_after - s.scalar(select(func.now()))
                assert timedelta(seconds=15) < delta <= timedelta(seconds=20)
    assert job.status == "failed"
    assert len(job.last_error) == 2000


def test_complete_marks_done(session_factory, db_session):
    jid = _job(session_factory)
    with session_factory() as s:
        claim(s, ["ingest"])
        complete(s, jid)
        s.commit()
        assert s.get(Job, jid).status == "done"


def test_run_once_marks_success_done(session_factory, db_session):
    jid = _job(session_factory)
    seen = []
    assert run_once(session_factory, {"ingest": lambda s, p: seen.append(p)}) is True
    assert seen == [{"v": 1}]
    with session_factory() as s:
        assert s.get(Job, jid).status == "done"
    assert run_once(session_factory, {"ingest": lambda s, p: None}) is False


def test_run_once_handler_error_persists_attempt_and_backoff(session_factory, db_session):
    jid = _job(session_factory)

    def boom(s, p):
        raise RuntimeError("kaput")

    assert run_once(session_factory, {"ingest": boom}) is True
    with session_factory() as s:
        job = s.get(Job, jid)
        assert (job.status, job.attempts, job.last_error) == ("queued", 1, "kaput")
