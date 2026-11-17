"""Task 10.3: lease-based execution. Crash + reclaim, no double processing, fenced completion, bounded retries."""

import threading
import time

import pytest
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
    """Handler runs 3 s on a 1.5 s lease: the heartbeat keeps it alive while another worker sweeps and claims."""
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


# ---- Task 10.3 fix round: cooperative abort, fencing of own-session writes, release at shutdown


def _steal(sf):
    """Another worker takes the job: the lease runs out, a sweep requeues it, it is claimed again (attempt + 1)."""
    with sf() as s:
        s.execute(text("UPDATE jobs SET lease_until = now() - interval '1 s' WHERE status = 'running'"))
        requeue_expired(s)
        s.execute(text("UPDATE jobs SET run_after = now()"))
        s.commit()
    with sf() as s:
        job = claim(s, ["ingest"], lease_s=60)
        s.commit()
        return job.attempts


def test_a_handler_aborts_cooperatively_when_the_lease_is_lost(session_factory, db_session):
    jid = add(session_factory)
    steps = []

    def handler(session, payload):
        enqueue(session, "side", {})
        for i in range(50):
            queue.check_lease()  # the checkpoint every long loop has
            steps.append(i)
            if i == 2:
                assert _steal(session_factory) == 2
            time.sleep(0.15)  # the heartbeat (period 0.1 s) notices the loss meanwhile

    assert run_once(session_factory, {"ingest": handler}, lease_s=60, heartbeat_s=0.1)
    assert len(steps) < 10  # stopped soon after the loss, not after 50 iterations
    status, attempts, _, _ = job_row(session_factory, jid)
    assert (status, attempts) == ("running", 2)  # the new owner's claim is untouched (no fail, no complete)
    with session_factory() as s:
        assert s.scalar(select(Job.id).where(Job.kind == "side")) is None  # handler work rolled back


def test_stale_workers_own_session_writes_are_fenced_and_nothing_is_duplicated(
    session_factory, db_session, document, blob, user
):
    """Lease stolen mid-ingest: the stale worker's R9 stage writes raise LeaseLost; the new owner finishes; there is
    exactly one citations job and one notification, and the stage never goes backwards."""
    from roracle.ingest.state import mark_searchable_and_enqueue, set_stage
    from roracle.models import DocumentVersion

    v = DocumentVersion(
        document_id=document.id, version=1, blob_sha256=blob.sha256, filename="f", content_type="x", size=1,
        stage="embedded", is_latest=True,
    )  # fmt: skip
    db_session.add(v)
    db_session.commit()
    jid = add(session_factory, payload={"version_id": str(v.id)})
    stale_done = []

    def handler(session, payload):
        set_stage(session_factory, v.id, "chunked")  # fenced but still ours: fine
        assert _steal(session_factory) == 2
        # the new owner (worker B) completes the ingest through the same helpers, outside our context
        tok = queue._current.set(None)
        try:
            mark_searchable_and_enqueue(session_factory, v.id)
        finally:
            queue._current.reset(tok)
        for fn in (
            lambda: set_stage(session_factory, v.id, "parsed"),  # would DEMOTE the version
            lambda: mark_searchable_and_enqueue(session_factory, v.id),  # would double the job + notification
        ):
            with pytest.raises(queue.LeaseLost):
                fn()
            stale_done.append(1)

    assert run_once(session_factory, {"ingest": handler}, lease_s=60, heartbeat_s=1000)
    assert stale_done == [1, 1]
    with session_factory() as s:
        assert s.get(DocumentVersion, v.id).stage == "searchable"
        assert len(s.scalars(select(Job).where(Job.kind == "citations")).all()) == 1
        assert len(s.scalars(select(Notification).where(Notification.kind == "upload.searchable")).all()) == 1
    assert job_row(session_factory, jid)[:2] == ("running", 2)  # the new owner's claim, untouched


def test_mark_searchable_twice_is_a_noop_the_second_time(session_factory, db_session, document, blob):
    from roracle.ingest.state import mark_searchable_and_enqueue
    from roracle.models import DocumentVersion

    v = DocumentVersion(
        document_id=document.id, version=1, blob_sha256=blob.sha256, filename="f", content_type="x", size=1,
        stage="embedded",
    )  # fmt: skip
    db_session.add(v)
    db_session.commit()
    for _ in range(2):
        mark_searchable_and_enqueue(session_factory, v.id)
    with session_factory() as s:
        assert len(s.scalars(select(Job).where(Job.kind == "citations")).all()) == 1


def test_fence_is_a_noop_outside_a_worker_job(session_factory):
    with session_factory() as s:
        queue.fence(s)  # API / CLI / tests: no current job
        queue.check_lease()


def test_release_at_shutdown_returns_the_job_without_spending_the_attempt(session_factory, db_session):
    jid = add(session_factory)
    started, released = threading.Event(), []

    def handler(session, payload):
        enqueue(session, "side", {})
        started.set()
        for _ in range(100):
            queue.check_lease()
            time.sleep(0.05)

    def shutdown():
        started.wait(5)
        time.sleep(0.2)
        released.append(queue.release_active())

    t = threading.Thread(target=shutdown)
    t.start()
    assert run_once(session_factory, {"ingest": handler}, lease_s=60)
    t.join()
    assert released == [True]
    assert job_row(session_factory, jid) == (
        "queued",
        0,
        None,
        None,
    )  # attempt refunded, lease cleared, no error
    with session_factory() as s:
        assert s.scalar(select(Job.id).where(Job.kind == "side")) is None  # the aborted handler rolled back
    assert queue.release_active() is False  # nothing running any more


def test_a_busy_worker_still_sweeps_a_crashed_peers_job(session_factory, db_session):
    add(session_factory, "ingest")
    peer = add(session_factory, "peer")
    with session_factory() as s:
        queue.claim(s, ["peer"], lease_s=0.05)  # a worker claimed it and died
        s.commit()
    time.sleep(0.1)

    def long_job(session, payload):
        time.sleep(0.6)  # no idle loop here: only the heartbeat thread can sweep

    assert run_once(session_factory, {"ingest": long_job}, lease_s=30, heartbeat_s=0.1)
    assert job_row(session_factory, peer)[:2] == ("queued", 1)


def test_heartbeat_thread_is_joined_at_completion_and_makes_no_false_loss_warning(
    session_factory, db_session, caplog
):
    add(session_factory)
    with caplog.at_level("WARNING"):
        assert run_once(
            session_factory, {"ingest": lambda s, p: time.sleep(0.35)}, lease_s=30, heartbeat_s=0.1
        )
    assert not [t for t in threading.enumerate() if t.name.startswith("lease-")]
    assert "lease lost" not in caplog.text


def test_complete_requires_the_claim_generation(session_factory, db_session):
    jid = add(session_factory)
    with session_factory() as s:
        queue.claim(s, ["ingest"], lease_s=60)
        s.commit()
    with session_factory() as s:
        with pytest.raises(TypeError):
            queue.complete(s, jid)  # type: ignore[call-arg]
        assert queue.complete(s, jid, 99) is False and queue.complete(s, jid, 1) is True
