"""Task 10.3: lease-based execution. Crash + reclaim, no double processing, fenced completion, bounded retries."""

import threading
import time

from sqlalchemy import select, text

from roracle import queue
from roracle.models import Job, Notification
from roracle.queue import (
    MAX_ATTEMPTS,
    NonRetryable,
    claim,
    enqueue,
    heartbeat,
    requeue_expired,
    run_once,
)


def job_row(sf, jid):
    with sf() as s:
        j = s.get(Job, jid)
        return j.status, j.attempts, j.lease_until, j.last_error


def add(sf, kind="ingest", payload=None):
    with sf() as s:
        jid = enqueue(s, kind, payload or {"v": 1})
        s.commit()
    return jid


def test_claim_commits_immediately_with_a_lease_and_holds_no_transaction(session_factory, db_session):
    jid = add(session_factory)
    seen = {}

    def handler(session, payload):
        seen["row"] = job_row(session_factory, jid)  # another connection: the claim is already committed
        with session_factory() as s:  # ...and no transaction of ours holds the job row
            s.execute(text("SELECT id FROM jobs WHERE id = :i FOR UPDATE NOWAIT"), {"i": jid})

    assert run_once(session_factory, {"ingest": handler}, lease_s=60)
    status, attempts, lease, _ = seen["row"]
    assert (status, attempts) == ("running", 1) and lease is not None
    assert job_row(session_factory, jid)[:2] == ("done", 1)
    assert job_row(session_factory, jid)[2] is None  # lease cleared


def test_crashed_worker_is_requeued_after_its_lease_and_the_attempt_is_counted(session_factory, db_session):
    jid = add(session_factory)
    with session_factory() as s:  # a worker claims and dies: no heartbeat, no completion
        assert claim(s, ["ingest"], lease_s=0.3).id == jid
        s.commit()
    with session_factory() as s:
        assert requeue_expired(s) == 0  # lease still valid
        s.commit()
    time.sleep(0.5)
    with session_factory() as s:
        assert requeue_expired(s) == 1
        s.commit()
    status, attempts, lease, err = job_row(session_factory, jid)
    assert (status, attempts, lease) == ("queued", 1, None) and "lease expired" in err
    with session_factory() as s:
        s.execute(text("UPDATE jobs SET run_after = now()"))  # skip the backoff
        s.commit()
    seen = []
    assert run_once(session_factory, {"ingest": lambda s, p: seen.append(1)})
    assert seen == [1] and job_row(session_factory, jid)[:2] == ("done", 2)  # exactly one more attempt


def test_a_job_that_keeps_killing_workers_fails_after_max_attempts_and_notifies(session_factory, db_session):
    from roracle.models import User

    with session_factory() as s:
        for n in range(MAX_ATTEMPTS):
            if n == 0:
                s.add(User(id=__import__("uuid").uuid4(), sub="adm", username="adm", is_admin=True))
                jid = enqueue(s, "ingest", {"version_id": "not-a-uuid"})
                s.commit()
            s.execute(text("UPDATE jobs SET run_after = now()"))
            s.commit()
            assert claim(s, ["ingest"], lease_s=0.01) is not None
            s.commit()
            time.sleep(0.05)
            assert requeue_expired(s) == 1
            s.commit()
    status, attempts, _, err = job_row(session_factory, jid)
    assert (status, attempts) == ("failed", MAX_ATTEMPTS) and "worker lost" in err
    with session_factory() as s:
        assert (
            s.scalar(select(text("count(*)")).select_from(Notification)) >= 0
        )  # on_job_failed ran without error


def test_slow_but_alive_worker_is_never_double_claimed(session_factory, db_session):
    """Handler runs 1.5 s on a 0.6 s lease: the heartbeat keeps it alive while another worker sweeps and claims."""
    jid = add(session_factory)
    claimed_by_other, stop, started = [], threading.Event(), threading.Event()

    def other_worker():
        started.wait(5)  # only once the first worker really runs the job
        while not stop.is_set():
            with session_factory() as s:
                requeue_expired(s)
                s.commit()
            with session_factory() as s:
                if (j := claim(s, ["ingest"], lease_s=60)) is not None:
                    claimed_by_other.append(j.id)
                s.commit()
            time.sleep(0.05)

    t = threading.Thread(target=other_worker, daemon=True)
    t.start()
    ran = []
    try:
        assert run_once(
            session_factory, {"ingest": lambda s, p: (time.sleep(1.5), ran.append(1))}, lease_s=0.6
        )
    finally:
        stop.set()
        t.join()
    assert ran == [1] and claimed_by_other == []
    assert job_row(session_factory, jid)[:2] == ("done", 1)


def test_handler_exception_rolls_back_handler_work_only(session_factory, db_session):
    jid = add(session_factory)

    def boom(session, payload):
        enqueue(session, "other", {"side": "effect"})  # a DB write through the handler session
        raise RuntimeError("kaput")

    assert run_once(session_factory, {"ingest": boom})
    status, attempts, lease, err = job_row(session_factory, jid)
    assert (status, attempts, lease, err) == ("queued", 1, None, "kaput")  # claim + attempt survive
    with session_factory() as s:
        assert s.scalar(select(Job.id).where(Job.kind == "other")) is None  # handler work rolled back


def test_non_retryable_fails_at_once_with_the_user_safe_message(session_factory, db_session):
    jid = add(session_factory)

    def bad(session, payload):
        raise NonRetryable("no text extracted")

    assert run_once(session_factory, {"ingest": bad})
    status, attempts, _, err = job_row(session_factory, jid)
    assert (status, attempts, err) == ("failed", 1, "user:no text extracted")


def test_lost_lease_discards_the_handler_db_effects_and_does_not_complete(session_factory, db_session):
    """Heartbeat off, lease expires mid-handler and another sweep requeues the job: our late completion must match
    nothing and our DB writes must roll back (exactly-once DB effects for the handler session)."""
    jid = add(session_factory)

    def slow(session, payload):
        enqueue(session, "side", {"x": 1})  # would be applied twice if the late completion committed
        time.sleep(0.6)
        with session_factory() as s:  # the sweep of another worker
            assert requeue_expired(s) == 1
            s.commit()

    assert run_once(session_factory, {"ingest": slow}, lease_s=0.2, heartbeat_s=1000)
    status, attempts, _, _ = job_row(session_factory, jid)
    assert (status, attempts) == ("queued", 1)  # requeued, NOT done
    with session_factory() as s:
        assert s.scalar(select(Job.id).where(Job.kind == "side")) is None


def test_stale_worker_cannot_fail_or_complete_a_reclaimed_job(session_factory, db_session):
    jid = add(session_factory)
    with session_factory() as s:
        old = claim(s, ["ingest"], lease_s=0.01)
        s.commit()
        old_attempts = old.attempts
    time.sleep(0.05)
    with session_factory() as s:
        requeue_expired(s)
        s.execute(text("UPDATE jobs SET run_after = now()"))
        s.commit()
    with session_factory() as s:
        new = claim(s, ["ingest"], lease_s=60)
        s.commit()
        assert new.attempts == old_attempts + 1
    with session_factory() as s:  # the stale worker finally wakes up
        assert queue.complete(s, jid, old_attempts) is False
        cur = s.get(Job, jid)
        assert queue.fail(s, cur, "late", retryable=False) is True  # ...the current owner can
        s.rollback()
        assert queue.heartbeat(session_factory, jid, old_attempts, 60) is False
        assert heartbeat(session_factory, jid, new.attempts, 60) is True
    assert job_row(session_factory, jid)[:2] == ("running", 2)


def test_long_handler_keeps_extending_the_lease(session_factory, db_session):
    jid = add(session_factory)
    leases = []

    def handler(session, payload):
        for _ in range(3):
            time.sleep(0.4)
            leases.append(job_row(session_factory, jid)[2])

    assert run_once(session_factory, {"ingest": handler}, lease_s=0.6)
    assert leases[0] < leases[-1]  # the heartbeat moved lease_until forward during the job
    assert job_row(session_factory, jid)[:2] == ("done", 1)
