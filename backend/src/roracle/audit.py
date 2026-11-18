"""Append-only audit trail (R4). Adds a row to the caller's transaction; the caller commits.

`durable=True` rows also survive a rollback: `get_session` calls `replay_durable` after rolling a failed
request back, and `audit_detached` writes from its own session (no request transaction to lose)."""

import contextlib
import logging
from typing import TYPE_CHECKING

from sqlalchemy import event
from sqlalchemy.orm import Session, sessionmaker

from roracle.metrics import AUDIT_WRITE_FAILURES
from roracle.models import AuditLog

if TYPE_CHECKING:
    from roracle.auth import Principal

log = logging.getLogger(__name__)
KEY = "durable_audit"


def audit(
    session: Session,
    p: "Principal | None",
    action: str,
    target: str = "",
    detail: dict | None = None,
    *,
    durable: bool = False,
) -> None:
    row = {
        "user_id": p.user_id if p else None,
        "action": action,
        "target": target,
        "client": p.client if p else "",
        "detail": detail or {},
    }
    session.add(AuditLog(**row))
    if durable:
        session.info.setdefault(KEY, []).append(row)


@event.listens_for(Session, "after_commit")
def _committed(session: Session) -> None:
    session.info.pop(KEY, None)  # the rows just committed with the request


def replay_durable(session: Session, status: int) -> None:
    """After a rollback: re-add the request's durable rows (user_id=None: the user row may be gone too)."""
    rows = session.info.pop(KEY, [])
    if not rows:
        return
    try:
        for r in rows:
            detail = {**r["detail"], "failed": status}
            if r["user_id"] is not None:
                detail["user_id"] = str(r["user_id"])
            session.add(AuditLog(**{**r, "user_id": None, "detail": detail}))
        session.commit()
    except Exception:
        with contextlib.suppress(Exception):
            session.rollback()
        log.exception("durable audit replay failed")
        AUDIT_WRITE_FAILURES.labels("replay").inc()


def audit_detached(
    sf: sessionmaker,
    p: "Principal | None",
    action: str,
    target: str = "",
    detail: dict | None = None,
) -> None:
    """Write one row from its own session. Never raises."""
    try:
        with sf() as s:
            audit(s, p, action, target, detail)
            s.commit()
    except Exception:
        log.exception("detached audit write failed")
        AUDIT_WRITE_FAILURES.labels("detached").inc()
