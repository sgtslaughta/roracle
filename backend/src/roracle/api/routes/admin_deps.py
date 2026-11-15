"""Admin guard: admins only (never PATs), one audit row per admin request (R11)."""

from typing import Annotated

from fastapi import Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from roracle.audit import audit
from roracle.auth import Principal, current_principal
from roracle.db import get_session
from roracle.models import AuditLog


def require_admin(
    request: Request,
    p: Annotated[Principal, Depends(current_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> Principal:
    if not p.is_admin or p.via != "jwt":
        with request.app.state.session_factory() as s:  # own session: the 403 rolls back the request's
            # user_id=None: the principal's user row may not be committed yet (first login)
            s.add(
                AuditLog(
                    action="admin.denied",
                    target=request.url.path,
                    client=p.client,
                    detail={"user_id": str(p.user_id), "username": p.username},
                )
            )
            s.commit()
        raise HTTPException(403, "admin only")
    name = getattr(request.scope.get("endpoint"), "__name__", "unknown")
    audit(session, p, f"admin.{name}", request.url.path, {"query": dict(request.query_params)})
    return p


Admin = Annotated[Principal, Depends(require_admin)]


def page_params(
    page: Annotated[int, Query(ge=1)] = 1, limit: Annotated[int, Query(ge=1, le=200)] = 50
) -> tuple[int, int]:
    """(offset, limit)."""
    return (page - 1) * limit, limit
