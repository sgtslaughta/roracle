import os
import signal
from datetime import timedelta

from sqlalchemy import func, select, text

from roracle import queue
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


def test_failed_handler_keeps_claim_lock_and_rolls_back_side_effects(session_factory, db_session):
    jid = _job(session_factory)
    during = []

    def boom(s, p):
        s.execute(text("UPDATE jobs SET payload = '{\"x\": 1}' WHERE id = :i"), {"i": jid})
        with session_factory() as other:
            during.append(claim(other, ["ingest"]))  # row must stay locked
        raise RuntimeError("kaput")

    assert run_once(session_factory, {"ingest": boom}) is True
    assert during == [None]
    with session_factory() as s:
        job = s.get(Job, jid)
        assert (job.status, job.attempts, job.last_error) == ("queued", 1, "kaput")
        assert job.payload == {"v": 1}  # handler side effect rolled back
        assert job.run_after > s.scalar(select(func.now()))


def test_five_handler_errors_end_failed(session_factory, db_session):
    jid = _job(session_factory)

    def boom(s, p):
        raise RuntimeError("kaput")

    for _ in range(5):
        with session_factory() as s:
            s.execute(text("UPDATE jobs SET run_after = now() WHERE id = :i"), {"i": jid})
            s.commit()
        assert run_once(session_factory, {"ingest": boom}) is True
    with session_factory() as s:
        job = s.get(Job, jid)
        assert (job.status, job.attempts) == ("failed", 5)


def test_run_forever_survives_infrastructure_errors(monkeypatch):
    calls = []

    def flaky(sf, handlers):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("db down")
        if len(calls) >= 3:
            os.kill(os.getpid(), signal.SIGTERM)
        return False

    monkeypatch.setattr(queue, "run_once", flaky)
    old = signal.getsignal(signal.SIGTERM), signal.getsignal(signal.SIGINT)
    try:
        queue.run_forever(None, {}, idle_sleep=0)
    finally:
        signal.signal(signal.SIGTERM, old[0])
        signal.signal(signal.SIGINT, old[1])
    assert len(calls) == 3


def test_non_retryable_fails_immediately(session_factory, db_session):
    jid = _job(session_factory)

    def bad(s, p):
        raise queue.NonRetryable("x")

    assert run_once(session_factory, {"ingest": bad}) is True
    with session_factory() as s:
        job = s.get(Job, jid)
        assert (job.status, job.attempts, job.last_error) == ("failed", 1, "x")
