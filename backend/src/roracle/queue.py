"""Postgres job queue (ADR-0003 + lease amendment): a `jobs` table claimed with FOR UPDATE SKIP LOCKED.

Lifecycle: `claim` marks the job running with a lease and COMMITS (the row lock is held for milliseconds); the handler
runs in its own session/transaction while a heartbeat thread extends the lease; success completes the job in the
handler's own transaction (handler DB writes and completion are atomic); a worker that dies stops heartbeating and
`requeue_expired` puts the job back. See docs/adr/0003-postgres-job-queue.md for the per-handler guarantees."""

import contextvars
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
LEASE_S = 90.0  # ROR_JOB_LEASE_S (set by the worker at start); heartbeat every LEASE_S / 3
REQUEUE_EVERY_S = 30.0  # idle workers sweep expired leases this often (busy ones sweep on every heartbeat)
DRAIN_S = (
    0.0  # ROR_JOB_DRAIN_S: after SIGTERM a running job gets this long, then its lease is released (0 = wait)
)
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


class LeaseLost(Exception):
    """The running job no longer belongs to this worker (requeued, reclaimed, or released at shutdown). Raised by
    `check_lease`/`fence`; `run_once` treats it as a lost lease: roll back, record nothing, the new owner decides."""


# (job id, claim attempts, "lost" event) of the job running on THIS thread; None outside a worker job.
_current: contextvars.ContextVar[tuple[int, int, threading.Event] | None] = contextvars.ContextVar(
    "current_job", default=None
)


def check_lease() -> None:
    """Cooperative abort point: call at stage boundaries and in long loops of a handler."""
    cur = _current.get()
    if cur is not None and cur[2].is_set():
        raise LeaseLost(f"job {cur[0]}: lease lost")


def fence(session: Session) -> None:
    """Fence a write made through an OWN short session (R9 helpers, advisory-lock sessions): in that transaction,
    take a shared lock on the job row of THIS claim (`FOR SHARE` blocks a concurrent `requeue_expired` until the
    short transaction ends) and raise `LeaseLost` when the job is not running under our claim any more. No-op
    outside a worker job (API, CLI, tests)."""
    check_lease()
    cur = _current.get()
    if cur is None:
        return
    if (
        session.execute(
            text("SELECT 1 FROM jobs WHERE id = :i AND status = 'running' AND attempts = :n FOR SHARE"),
            {"i": cur[0], "n": cur[1]},
        ).first()
        is None
    ):
        raise LeaseLost(f"job {cur[0]}: lease lost")


def set_lease(seconds: float, drain_seconds: float = 0.0) -> None:
    global DRAIN_S
    global LEASE_S
    LEASE_S = float(seconds)
    DRAIN_S = float(drain_seconds)


def set_heartbeat_factory(sf: sessionmaker[Session] | None) -> None:
    """Own small engine for the heartbeat + busy-sweep, so it never competes with handler sessions for the pool."""
    global _HB_SF
    _HB_SF = sf


_HB_SF: sessionmaker[Session] | None = None


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


def complete(session: Session, job_id: int, attempts: int) -> bool:
    """Mark done. Guarded by `status = 'running'` AND `attempts` (the fencing token of this claim): False means the
    lease was lost (requeued / reclaimed) and the caller must roll back."""
    r = session.execute(
        text(
            "UPDATE jobs SET status = 'done', lease_until = NULL, updated_at = now()"
            " WHERE id = :id AND status = 'running' AND attempts = :n"
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
            " WHERE status = 'running' AND lease_until < now() RETURNING id, status, kind"
        ),
        {"max": MAX_ATTEMPTS},
    ).all()
    for jid, status, kind in rows:
        JOB_RUNS.labels(kind, "failed" if status == "failed" else "retry").inc()
        if status == "failed":
            job = session.get(Job, jid)
            session.refresh(job)
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
    """Extends the lease every `period` while a handler runs (daemon: it dies with the process, which is exactly what
    lets the lease expire when the worker is killed) and sweeps expired leases of OTHER jobs on every tick, so a
    worker that is busy with long jobs still recovers a crashed peer's job. `lost` is set when the claim no longer
    belongs to us (handlers poll it through `check_lease`/`fence`)."""

    def __init__(self, sf: sessionmaker[Session], job_id: int, attempts: int, lease_s: float, period: float):
        super().__init__(daemon=True, name=f"lease-{job_id}")
        self.sf, self.job_id, self.attempts, self.lease_s, self.period = sf, job_id, attempts, lease_s, period
        self.stop_ev, self.lost = threading.Event(), threading.Event()

    def run(self) -> None:
        while not self.stop_ev.wait(self.period):
            try:
                if not heartbeat(self.sf, self.job_id, self.attempts, self.lease_s):
                    self.lost.set()
                    if not self.stop_ev.is_set():  # a tick racing completion is not a loss
                        log.warning(
                            "job %s: lease lost; the handler aborts at its next checkpoint", self.job_id
                        )
                    return
                with self.sf() as s:
                    n = requeue_expired(s)
                    s.commit()
                if n:
                    log.warning("requeued %d job(s) with an expired lease", n)
            except Exception:  # a transient DB error: retry next tick; the lease only lapses after lease_s
                log.warning("job %s: heartbeat failed", self.job_id, exc_info=True)


def heartbeat(sf: sessionmaker[Session], job_id: int, attempts: int, lease_s: float) -> bool:
    """Extend the lease of this claim. False when the job is no longer running under this claim generation.
    Bounded by statement/lock timeouts (a blackholed connection must not hang the thread past a few ticks)."""
    with sf() as s:
        bound = f"{max(1, int(lease_s / 6 * 1000))}ms"
        s.execute(
            text("SELECT set_config('statement_timeout', :v, true), set_config('lock_timeout', :v, true)"),
            {"v": bound},
        )
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


_active: dict | None = (
    None  # the job this process runs now: {sf, id, attempts, hb} (for the shutdown release)
)


def release_active() -> bool:
    """Shutdown deadline: hand the running job back. The handler is told to abort (LeaseLost at its next
    checkpoint, its transaction rolls back) and the job returns to the queue WITHOUT spending the attempt (the stop
    was ours, not the job's fault; handlers are idempotent, ADR-0003). Returns True if a job was released."""
    cur = _active
    if cur is None:
        return False
    cur["hb"].lost.set()
    with cur["sf"]() as s:
        r = s.execute(
            text(
                "UPDATE jobs SET status = 'queued', attempts = attempts - 1, lease_until = NULL, run_after = now(),"
                " updated_at = now() WHERE id = :id AND status = 'running' AND attempts = :n"
            ),
            {"id": cur["id"], "n": cur["attempts"]},
        )
        s.commit()
        return r.rowcount == 1


def run_once(
    session_factory: sessionmaker[Session],
    handlers: dict[str, Handler],
    lease_s: float | None = None,
    heartbeat_s: float | None = None,
) -> bool:
    """Run at most one job; return True if one was claimed.

    1. Claim in a short transaction and COMMIT (running + attempts + lease): no transaction is open while the job runs.
    2. The handler runs in its OWN session, inside a SAVEPOINT; a heartbeat thread renews the lease every lease/3
       and sets `lost` when it cannot (handlers abort cooperatively; their own-session writes are fenced).
    3. Success: `complete` in the handler's transaction, guarded by (status running, attempts = our claim), then
       commit: the handler's DB writes and the completion are atomic. If the lease was lost the guard matches nothing
       and the whole transaction is rolled back: a reclaimed job never has its DB effects applied twice.
    4. Failure: the handler transaction is rolled back (handler work only); `fail` records the error/backoff in a
       fresh short transaction, guarded the same way.
    `attempts` counts claims, so a worker that dies mid-job also spends an attempt (bounded retries).
    `heartbeat_s` overrides the heartbeat PERIOD (tests); the lease length stays `lease_s`."""
    global _active
    lease = LEASE_S if lease_s is None else lease_s
    with session_factory() as cs:
        job = claim(cs, handlers.keys(), lease)
        if job is None:
            return False
        jid, kind, payload, attempts = job.id, job.kind, dict(job.payload), job.attempts
        cs.commit()
    worker_health.working = True
    hb = _Heartbeat(
        _HB_SF or session_factory, jid, attempts, lease, lease / 3 if heartbeat_s is None else heartbeat_s
    )
    hb.start()
    _active = {"sf": _HB_SF or session_factory, "id": jid, "attempts": attempts, "hb": hb}
    token = _current.set((jid, attempts, hb.lost))
    try:
        _execute(session_factory, handlers[kind], jid, kind, payload, attempts, hb)
    finally:
        _current.reset(token)
        _active = None
        hb.stop_ev.set()
        hb.join(timeout=5)  # a tick racing completion must not log a false "lease lost"
        worker_health.working = False
    return True


def _execute(sf, handler: Handler, jid: int, kind: str, payload: dict, attempts: int, hb: _Heartbeat) -> None:
    error: Exception | None = None
    with sf() as hs:
        try:
            with hs.begin_nested():
                handler(hs, payload)
        except LeaseLost:
            hs.rollback()
            log.warning("job %s (%s): lease lost, aborted; the new owner (or the queue) decides", jid, kind)
            return
        except Exception as exc:
            log.exception("job %s (%s) failed", jid, kind)
            hs.rollback()
            error = exc
        else:
            if hb.lost.is_set() or not complete(hs, jid, attempts):
                hs.rollback()  # lease lost: someone else owns the job now; discard our DB effects
                log.warning("job %s (%s): lease lost, result discarded", jid, kind)
                return
            hs.commit()
            JOB_RUNS.labels(kind, "done").inc()
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
    """Loop until SIGTERM/SIGINT. SIGTERM stops claiming; a running job gets `DRAIN_S` (ROR_JOB_DRAIN_S, below the pod
    grace period) to finish, then it is released back to the queue (`release_active`, attempt refunded). When idle
    (at most every REQUEUE_EVERY_S) a worker sweeps expired leases; busy workers sweep on each heartbeat."""
    stop = False
    last_sweep = 0.0

    def _stop(*_):
        nonlocal stop
        stop = True
        if DRAIN_S > 0:
            t = threading.Timer(DRAIN_S, release_active)
            t.daemon = True
            t.start()

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
