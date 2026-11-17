"""Postgres job queue (ADR-0003): a `jobs` table claimed with FOR UPDATE SKIP LOCKED."""

import logging
import signal
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
# Handlers run inside a savepoint of the job transaction and must NOT call session.commit().
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


def claim(session: Session, kinds: Collection[str]) -> Job | None:
    """Claim one due job; the row lock lasts until the caller's transaction ends (no commit).

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
        session.flush()
    return job


def complete(session: Session, job_id: int) -> None:
    session.execute(
        text("UPDATE jobs SET status = 'done', updated_at = now() WHERE id = :id"), {"id": job_id}
    )


def fail(session: Session, job: Job, error: str, *, retryable: bool = True) -> None:
    """Requeue with 10 * 2**attempts seconds backoff; fail permanently at MAX_ATTEMPTS.

    `attempts` is the DB value (already incremented by `claim`). `retryable=False` fails at once.
    """
    session.execute(
        text(
            "UPDATE jobs SET status = CASE WHEN NOT :retry OR attempts >= :max THEN 'failed' ELSE 'queued' END,"
            " last_error = :err, updated_at = now(),"
            " run_after = now() + make_interval(secs => 10 * power(2, attempts))"
            " WHERE id = :id"
        ),
        {"max": MAX_ATTEMPTS, "retry": retryable, "err": error[:MAX_ERROR_LEN], "id": job.id},
    )
    session.refresh(job)


def _notify_failed(session: Session, job: Job) -> None:
    from roracle import notify  # lazy: keeps queue importable without the notification layer

    try:
        with session.begin_nested():
            notify.on_job_failed(session, job)
    except Exception:
        log.exception("failure notification for job %s", job.id)


def run_once(session_factory: sessionmaker[Session], handlers: dict[str, Handler]) -> bool:
    """Run at most one job; return True if one was claimed.

    Claim, handler and complete/fail share ONE transaction, so the claim's row lock is never
    released mid-flight and a crashed worker leaves the job 'queued' (at-least-once). The
    handler runs in a SAVEPOINT: on error only its work is rolled back, the claim (attempts
    increment) survives and `fail` records the error/backoff.
    """
    with session_factory() as session:
        job = claim(session, handlers.keys())
        if job is None:
            return False
        worker_health.working = True
        try:
            with session.begin_nested():
                handlers[job.kind](session, job.payload)
        except Exception as exc:
            log.exception("job %s (%s) failed", job.id, job.kind)
            msg = str(exc) or type(exc).__name__
            user_safe = isinstance(exc, NonRetryable)
            if user_safe:
                msg = USER_PREFIX + msg
            elif msg.startswith(USER_PREFIX):
                msg = "err:" + msg  # only NonRetryable may produce a leading "user:"
            fail(session, job, msg, retryable=not user_safe)
            JOB_RUNS.labels(job.kind, "failed" if job.status == "failed" else "retry").inc()
            if job.status == "failed":
                _notify_failed(session, job)
        else:
            complete(session, job.id)
            JOB_RUNS.labels(job.kind, "done").inc()
        finally:
            worker_health.working = False
        session.commit()
    return True


def run_forever(
    session_factory: sessionmaker[Session], handlers: dict[str, Handler], idle_sleep: float = 1.0
) -> None:
    """Loop until SIGTERM/SIGINT; the current job always finishes first."""
    stop = False

    def _stop(*_):
        nonlocal stop
        stop = True

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    while not stop:
        worker_health.beat()
        try:
            if run_once(session_factory, handlers):
                continue
        except Exception:
            log.exception("worker iteration failed")
        time.sleep(idle_sleep)
