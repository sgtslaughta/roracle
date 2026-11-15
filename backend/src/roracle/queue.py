"""Postgres job queue (ADR-0003): a `jobs` table claimed with FOR UPDATE SKIP LOCKED."""

import logging
import signal
import time
from collections.abc import Callable, Collection

from sqlalchemy import select, text
from sqlalchemy.orm import Session, sessionmaker

from roracle.models import Job

log = logging.getLogger(__name__)

MAX_ATTEMPTS = 5
MAX_ERROR_LEN = 2000
Handler = Callable[[Session, dict], None]


def enqueue(session: Session, kind: str, payload: dict) -> int:
    """Insert a job in the caller's transaction (no commit)."""
    job = Job(kind=kind, payload=payload)
    session.add(job)
    session.flush()
    return job.id


def claim(session: Session, kinds: Collection[str]) -> Job | None:
    """Claim one due job; the row lock lasts until the caller's transaction ends (no commit)."""
    job = session.scalar(
        select(Job)
        .where(Job.status == "queued", Job.run_after <= text("now()"), Job.kind.in_(list(kinds)))
        .order_by(Job.run_after, Job.id)
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    if job is not None:
        job.status = "running"
        job.attempts += 1
        session.flush()
    return job


def complete(session: Session, job_id: int) -> None:
    session.execute(
        text("UPDATE jobs SET status = 'done', updated_at = now() WHERE id = :id"), {"id": job_id}
    )


def fail(session: Session, job: Job, error: str) -> None:
    """Fail permanently after MAX_ATTEMPTS, else requeue with 10 * 2**attempts seconds backoff."""
    status = "failed" if job.attempts >= MAX_ATTEMPTS else "queued"
    session.execute(
        text(
            "UPDATE jobs SET status = :status, last_error = :err, updated_at = now(),"
            " run_after = now() + make_interval(secs => 10 * power(2, attempts))"
            " WHERE id = :id"
        ),
        {"status": status, "err": error[:MAX_ERROR_LEN], "id": job.id},
    )
    session.refresh(job)


def run_once(session_factory: sessionmaker[Session], handlers: dict[str, Handler]) -> bool:
    """Run at most one job; return True if one was claimed.

    Claim, handler and complete share ONE transaction, so a crashed worker leaves the job
    'queued' (at-least-once). If the handler raises, its work is rolled back (which also
    undoes the claim), then a NEW transaction records the attempt/backoff via `fail`.
    """
    with session_factory() as session:
        job = claim(session, handlers.keys())
        if job is None:
            return False
        job_id, attempts, kind, payload = job.id, job.attempts, job.kind, job.payload
        try:
            handlers[kind](session, payload)
            complete(session, job_id)
            session.commit()
            return True
        except Exception as exc:
            log.exception("job %s (%s) failed", job_id, kind)
            session.rollback()
            error = str(exc) or type(exc).__name__
    with session_factory() as session:
        job = session.get(Job, job_id, with_for_update=True)
        job.attempts = attempts  # the claim's increment was rolled back
        fail(session, job, error)
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
        if not run_once(session_factory, handlers):
            time.sleep(idle_sleep)
