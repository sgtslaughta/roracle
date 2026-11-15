"""Append-only audit trail (R4). Adds a row to the caller's transaction; the caller commits."""

from sqlalchemy.orm import Session

from roracle.auth import Principal
from roracle.models import AuditLog


def audit(
    session: Session, p: Principal | None, action: str, target: str = "", detail: dict | None = None
) -> None:
    session.add(
        AuditLog(
            user_id=p.user_id if p else None,
            action=action,
            target=target,
            client=p.client if p else "",
            detail=detail or {},
        )
    )
