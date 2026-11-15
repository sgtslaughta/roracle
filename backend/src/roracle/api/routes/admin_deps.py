"""Admin guard: admins only (never PATs), one audit row per admin request (R11)."""

import logging
from typing import Annotated

from fastapi import Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from roracle.audit import audit
from roracle.auth import Principal, current_principal
from roracle.db import get_session
from roracle.models import AuditLog

log = logging.getLogger(__name__)


def require_admin(
    request: Request,
    p: Annotated[Principal, Depends(current_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> Principal:
    if not p.is_admin or p.via != "jwt":
        try:
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
        except Exception:
            log.exception("admin.denied audit write failed")
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


def keyset_params(
    before_id: Annotated[int | None, Query(ge=1)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> tuple[int | None, int]:
    return before_id, limit


def keyset_page(rows: list, limit: int) -> dict:
    """`rows` are dicts with `id`, fetched with limit+1 in id-descending order."""
    items = rows[:limit]
    return {"items": items, "next_before_id": items[-1]["id"] if len(rows) > limit else None}
