"""User/group lookup for sharing and co-author pickers (any authenticated caller; id + username only)."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from roracle.access import org_units
from roracle.auth import Principal, current_principal
from roracle.db import get_session
from roracle.models import User

router = APIRouter()
Who = Annotated[Principal, Depends(current_principal)]
Db = Annotated[Session, Depends(get_session)]
Q = Annotated[str, Query(min_length=2, max_length=100)]
LIMIT = 10


@router.get("/directory/users")
def users(q: Q, p: Who, session: Db) -> dict:
    esc = q.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    rows = session.execute(
        select(User.id, User.username)
        .where(User.username.ilike(f"%{esc}%", escape="\\"))
        .order_by(User.username, User.id)
        .limit(LIMIT)
    ).all()
    return {"items": [{"id": str(i), "username": n} for i, n in rows]}


@router.get("/directory/groups")
def groups(q: Q, p: Who, session: Db) -> dict:
    raw = session.scalars(select(func.jsonb_array_elements_text(User.groups)).distinct())
    needle = q.strip().lower()
    found = sorted({g for g in org_units(list(raw)) if needle in g.lower()})
    return {"items": found[:LIMIT]}
