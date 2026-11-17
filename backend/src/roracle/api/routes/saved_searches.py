"""Owner-only saved library searches (R14: URL query string; server checks length + ownership)."""

from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from roracle.auth import Principal, current_principal
from roracle.db import get_session
from roracle.models_saved import SavedSearch

router = APIRouter()
MAX_SAVED = 200
Session_ = Annotated[Session, Depends(get_session)]
Who = Annotated[Principal, Depends(current_principal)]
Name = Annotated[str, Field(min_length=1, max_length=100)]


def _check_query(v: str) -> str:
    if v.startswith("?") or "\n" in v or "\r" in v:
        raise ValueError("query must be a query string without '?' or newlines")
    return v


class CreateBody(BaseModel):
    name: Name
    query: Annotated[str, Field(max_length=4000)]
    model_config = {"str_strip_whitespace": True}
    _q = field_validator("query")(_check_query)


class PatchBody(BaseModel):
    name: Name | None = None
    query: Annotated[str, Field(max_length=4000)] | None = None
    model_config = {"str_strip_whitespace": True}
    _q = field_validator("query")(lambda v: v if v is None else _check_query(v))


def _out(s: SavedSearch) -> dict:
    return {
        "id": str(s.id),
        "name": s.name,
        "query": s.query,
        "created_at": s.created_at.isoformat(),
        "updated_at": s.updated_at.isoformat(),
    }


def _own(session: Session, p: Principal, sid: UUID) -> SavedSearch:
    s = session.get(SavedSearch, sid)
    if s is None or s.owner_id != p.user_id:
        raise HTTPException(404, "not found")
    return s


def _dup() -> HTTPException:
    return HTTPException(409, "a saved search with this name exists")


@router.get("/me/saved-searches")
def list_saved(p: Who, session: Session_) -> list[dict]:
    rows = session.scalars(
        select(SavedSearch)
        .where(SavedSearch.owner_id == p.user_id)
        .order_by(SavedSearch.updated_at.desc(), SavedSearch.id)
    )
    return [_out(s) for s in rows]


@router.post("/me/saved-searches", status_code=201)
def create_saved(body: CreateBody, p: Who, session: Session_) -> dict:
    n = session.scalar(select(func.count()).where(SavedSearch.owner_id == p.user_id))
    if n >= MAX_SAVED:
        raise HTTPException(422, f"at most {MAX_SAVED} saved searches")
    s = SavedSearch(owner_id=p.user_id, name=body.name, query=body.query)
    try:
        with session.begin_nested():
            session.add(s)
    except IntegrityError:
        raise _dup() from None
    session.commit()
    session.refresh(s)
    return _out(s)


@router.patch("/me/saved-searches/{sid}")
def patch_saved(sid: UUID, body: PatchBody, p: Who, session: Session_) -> dict:
    s = _own(session, p, sid)
    try:
        with session.begin_nested():  # savepoint first: it flushes on entry
            if body.name is not None:
                s.name = body.name
            if body.query is not None:
                s.query = body.query
            s.updated_at = datetime.now(UTC)
    except IntegrityError:
        raise _dup() from None
    session.commit()
    return _out(s)


@router.delete("/me/saved-searches/{sid}", status_code=204)
def delete_saved(sid: UUID, p: Who, session: Session_) -> Response:
    session.delete(_own(session, p, sid))
    session.commit()
    return Response(status_code=204)
