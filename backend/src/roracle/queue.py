"""Postgres job queue (ADR-0003 + lease amendment): a `jobs` table claimed with FOR UPDATE SKIP LOCKED.

Lifecycle: `claim` marks the job running with a lease and COMMITS (the row lock is held for milliseconds); the handler
runs in its own session/transaction while a heartbeat thread extends the lease; success completes the job in the
handler's own transaction (handler DB writes and completion are atomic); a worker that dies stops heartbeating and
`requeue_expired` puts the job back. See docs/adr/0003-postgres-job-queue.md for the per-handler guarantees."""

import logging
import signal
import threading
import time
from collections.abc import Callable, Collection
from datetime import timedelta
from uuid import UUID

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.sql.elements import ClauseElement

from roracle import worker_health
from roracle.metrics import JOB_RUNS
from roracle.models import BULK_KINDS, Job, bulk_sql

log = logging.getLogger(__name__)

MAX_ATTEMPTS = 5
MAX_ERROR_LEN = 2000
LEASE_S = 300.0  # ROR_JOB_LEASE_S (set by the worker at start); heartbeat every LEASE_S / 3
REQUEUE_EVERY_S = 30.0  # idle workers sweep expired leases this often
# Handlers run inside a savepoint of THEIR OWN transaction (not the claim's) and must NOT call session.commit():
# that transaction also completes the job.
Handler = Callable[[Session, dict], None]


USER_PREFIX = "user:"  # marks a last_error that is safe to show to the uploader


class NonRetryable(Exception):
    """Raised by a handler to fail its job permanently; its message is user-safe (shown in My uploads)."""


def payload_uuid(payload: dict, key: str) -> UUID:
    """UUID field of a job payload; a malformed payload can never succeed, so fail it without retries."""
    try:
        return UUID(str(payload[key]))
    except (KeyError, ValueError, TypeError) as e:
        raise NonRetryable(f"malformed job payload: {key}") from e


def enqueue(session: Session, kind: str, payload: dict, run_after_s: float = 0) -> int:
    """Insert a job in the caller's transaction (no commit)."""
    job = Job(kind=kind, payload=payload)
    if run_after_s:
        job.run_after = func.now() + timedelta(seconds=run_after_s)
    session.add(job)
    session.flush()
    return job.id


# The claim SQL repeats the predicates of ix_jobs_due_user / ix_jobs_due_bulk (migration 0014) literally:
# a partial index is used only when the query's WHERE implies the index predicate.
_USER_ONLY = text(bulk_sql(bulk=False))
_BULK_ONLY = text(bulk_sql(bulk=True))


def claim_stmt(kinds: list[str], only: ClauseElement):
    return (
        select(Job)
        .where(text("status = 'queued'"), only, Job.run_after <= text("now()"), Job.kind.in_(kinds))
        .order_by(Job.run_after, Job.id)
        .limit(1)
        .with_for_update(skip_locked=True)
    )


def _claim_step(session: Session, kinds: list[str], only: ClauseElement) -> Job | None:
    return session.scalar(claim_stmt(kinds, only))


def set_lease(seconds: float) -> None:
    global LEASE_S
    LEASE_S = float(seconds)


def claim(session: Session, kinds: Collection[str], lease_s: float | None = None) -> Job | None:
    """Claim one due job: running + attempts + lease, in the caller's transaction (no commit: `run_once` commits at
    once, so the row lock is held for milliseconds and no transaction stays open while the job runs).

    Two steps, each an index-ordered `LIMIT 1` (no Sort): user kinds by (run_after, id), then bulk kinds
    (same order), so bulk backfills never starve user work and a bulk backlog is never scanned past.
    """
    user = [k for k in kinds if k not in BULK_KINDS]
    bulk = [k for k in kinds if k in BULK_KINDS]
    job = _claim_step(session, user, _USER_ONLY) if user else None
    if job is None and bulk:
        job = _claim_step(session, bulk, _BULK_ONLY)
    if job is not None:
        job.status = "running"
        job.attempts += 1
        job.lease_until = func.now() + timedelta(seconds=LEASE_S if lease_s is None else lease_s)
        session.flush()
    return job


def complete(session: Session, job_id: int, attempts: int | None = None) -> bool:
    """Mark done. Guarded by `status = 'running'` and, with `attempts` (the fencing token of this claim), by the
    claim generation: False means the lease was lost (requeued / reclaimed) and the caller must roll back."""
    r = session.execute(
        text(
            "UPDATE jobs SET status = 'done', lease_until = NULL, updated_at = now()"
            " WHERE id = :id AND status = 'running' AND (CAST(:n AS int) IS NULL OR attempts = :n)"
        ),
        {"id": job_id, "n": attempts},
    )
    return r.rowcount == 1


def fail(session: Session, job: Job, error: str, *, retryable: bool = True) -> bool:
    """Requeue with 10 * 2**attempts seconds backoff; fail permanently at MAX_ATTEMPTS.

    `attempts` is the DB value (already incremented by `claim`) and is also the fencing token: a stale worker whose
    lease was reclaimed changes nothing (returns False). `retryable=False` fails at once.
    """
    r = session.execute(
        text(
            "UPDATE jobs SET status = CASE WHEN NOT :retry OR attempts >= :max THEN 'failed' ELSE 'queued' END,"
            " last_error = :err, updated_at = now(), lease_until = NULL,"
            " run_after = now() + make_interval(secs => 10 * power(2, attempts))"
            " WHERE id = :id AND status = 'running' AND attempts = :n"
        ),
        {
            "max": MAX_ATTEMPTS,
            "retry": retryable,
            "err": error[:MAX_ERROR_LEN],
            "id": job.id,
            "n": job.attempts,
        },
    )
    session.refresh(job)
    return r.rowcount == 1


def requeue_expired(session: Session) -> int:
    """Running jobs whose lease ran out (worker killed, OOM, node lost) go back to the queue with backoff, or fail
    after MAX_ATTEMPTS (the claim already counted the lost attempt: a poison job that kills workers stops).
    At-least-once is preserved: the handler may have done external work; handlers are idempotent (ADR-0003).
    Safe to call from every worker concurrently. No commit."""
    rows = session.execute(
        text(
            "UPDATE jobs SET status = CASE WHEN attempts >= :max THEN 'failed' ELSE 'queued' END,"
            " last_error = 'worker lost (lease expired) after ' || attempts || ' attempt(s)',"
            " run_after = now() + make_interval(secs => 10 * power(2, attempts)),"
            " lease_until = NULL, updated_at = now()"
            " WHERE status = 'running' AND lease_until < now() RETURNING id, status"
        ),
        {"max": MAX_ATTEMPTS},
    ).all()
    for jid, status in rows:
        job = session.get(Job, jid)
        session.refresh(job)
        JOB_RUNS.labels(job.kind, "failed" if status == "failed" else "retry").inc()
        if status == "failed":
            _notify_failed(session, job)
    return len(rows)


def _notify_failed(session: Session, job: Job) -> None:
    from roracle import notify  # lazy: keeps queue importable without the notification layer

    try:
        with session.begin_nested():
            notify.on_job_failed(session, job)
    except Exception:
        log.exception("failure notification for job %s", job.id)


class _Heartbeat(threading.Thread):
    """Extends the lease every `lease_s / 3` while a handler runs (daemon: it dies with the process, which is exactly
    what lets the lease expire when the worker is killed). `lost` is set when the claim no longer belongs to us."""

    def __init__(self, sf: sessionmaker[Session], job_id: int, attempts: int, lease_s: float):
        super().__init__(daemon=True, name=f"lease-{job_id}")
        self.sf, self.job_id, self.attempts, self.lease_s = sf, job_id, attempts, lease_s
        self.stop_ev, self.lost = threading.Event(), threading.Event()

    def run(self) -> None:
        while not self.stop_ev.wait(self.lease_s / 3):
            try:
                if not heartbeat(self.sf, self.job_id, self.attempts, self.lease_s):
                    self.lost.set()
                    log.warning(
                        "job %s: lease lost (requeued or reclaimed); its result will be discarded",
                        self.job_id,
                    )
                    return
            except Exception:  # a transient DB error: retry next tick; the lease only lapses after lease_s
                log.warning("job %s: heartbeat failed", self.job_id, exc_info=True)


def heartbeat(sf: sessionmaker[Session], job_id: int, attempts: int, lease_s: float) -> bool:
    """Extend the lease of this claim. False when the job is no longer running under this claim generation."""
    with sf() as s:
        r = s.execute(
            text(
                "UPDATE jobs SET lease_until = now() + make_interval(secs => :l)"
                " WHERE id = :id AND status = 'running' AND attempts = :n"
            ),
            {"l": lease_s, "id": job_id, "n": attempts},
        )
        s.commit()
        return r.rowcount == 1


def _failure_message(exc: Exception) -> tuple[str, bool]:
    msg = str(exc) or type(exc).__name__
    user_safe = isinstance(exc, NonRetryable)
    if user_safe:
        msg = USER_PREFIX + msg
    elif msg.startswith(USER_PREFIX):
        msg = "err:" + msg  # only NonRetryable may produce a leading "user:"
    return msg, user_safe


def run_once(
    session_factory: sessionmaker[Session],
    handlers: dict[str, Handler],
    lease_s: float | None = None,
    heartbeat_s: float | None = None,
) -> bool:
    """Run at most one job; return True if one was claimed.

    1. Claim in a short transaction and COMMIT (running + attempts + lease): no transaction is open while the job runs.
    2. The handler runs in its OWN session, inside a SAVEPOINT; a heartbeat thread renews the lease every lease/3.
    3. Success: `complete` in the handler's transaction, guarded by (status running, attempts = our claim), then
       commit: the handler's DB writes and the completion are atomic. If the lease was lost the guard matches nothing
       and the whole transaction is rolled back: a reclaimed job never has its DB effects applied twice.
    4. Failure: the handler transaction is rolled back (handler work only); `fail` records the error/backoff in a
       fresh short transaction, guarded the same way.
    `attempts` counts claims, so a worker that dies mid-job also spends an attempt (bounded retries).
    `heartbeat_s` overrides the heartbeat period (tests)."""
    lease = LEASE_S if lease_s is None else lease_s
    with session_factory() as cs:
        job = claim(cs, handlers.keys(), lease)
        if job is None:
            return False
        jid, kind, payload, attempts = job.id, job.kind, dict(job.payload), job.attempts
        cs.commit()
    worker_health.working = True
    hb = _Heartbeat(session_factory, jid, attempts, lease if heartbeat_s is None else heartbeat_s * 3)
    hb.start()
    try:
        _execute(session_factory, handlers[kind], jid, kind, payload, attempts, hb)
    finally:
        hb.stop_ev.set()
        worker_health.working = False
    return True


def _execute(sf, handler: Handler, jid: int, kind: str, payload: dict, attempts: int, hb: _Heartbeat) -> None:
    error: Exception | None = None
    with sf() as hs:
        try:
            with hs.begin_nested():
                handler(hs, payload)
        except Exception as exc:
            log.exception("job %s (%s) failed", jid, kind)
            hs.rollback()
            error = exc
        else:
            if complete(hs, jid, attempts):
                hs.commit()
                JOB_RUNS.labels(kind, "done").inc()
            else:  # lease lost: someone else owns the job now; discard our DB effects
                hs.rollback()
                log.warning("job %s (%s): lease lost, result discarded", jid, kind)
    if error is None:
        return
    msg, user_safe = _failure_message(error)
    with sf() as fs:
        job = fs.get(Job, jid)
        if job is not None and fail(fs, job, msg, retryable=not user_safe):
            JOB_RUNS.labels(kind, "failed" if job.status == "failed" else "retry").inc()
            if job.status == "failed":
                _notify_failed(fs, job)
        fs.commit()


def run_forever(
    session_factory: sessionmaker[Session], handlers: dict[str, Handler], idle_sleep: float = 1.0
) -> None:
    """Loop until SIGTERM/SIGINT; the current job always finishes first. When idle (and at most every
    REQUEUE_EVERY_S) a worker sweeps expired leases, so a crashed worker's job comes back within lease + 30 s."""
    stop = False
    last_sweep = 0.0

    def _stop(*_):
        nonlocal stop
        stop = True

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    while not stop:
        worker_health.beat()
        try:
            if time.monotonic() - last_sweep >= REQUEUE_EVERY_S:
                last_sweep = time.monotonic()
                with session_factory() as s:
                    n = requeue_expired(s)
                    s.commit()
                if n:
                    log.warning("requeued %d job(s) with an expired lease", n)
            if run_once(session_factory, handlers):
                continue
        except Exception:
            log.exception("worker iteration failed")
        time.sleep(idle_sleep)
