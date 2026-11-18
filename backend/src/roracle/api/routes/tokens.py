"""Personal access token management. JWT only (a leaked PAT must not mint tokens, R1)."""

from datetime import UTC, datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from roracle import pats
from roracle.audit import audit
from roracle.auth import Principal, require_jwt
from roracle.db import get_session
from roracle.models import PersonalAccessToken

router = APIRouter()
Who = Annotated[Principal, Depends(require_jwt)]
Session_ = Annotated[Session, Depends(get_session)]


class TokenBody(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    scopes: list[Literal["read", "write", "manage"]] = Field(default=["read"], min_length=1)
    expires_in_days: int = Field(default=30, ge=1, le=365)


def _view(t: PersonalAccessToken) -> dict:
    iso = lambda d: d.isoformat() if d else None
    return {
        "id": str(t.id),
        "name": t.name,
        "prefix": t.prefix,
        "scopes": t.scopes,
        "expires_at": iso(t.expires_at),
        "revoked_at": iso(t.revoked_at),
        "last_used_at": iso(t.last_used_at),
        "created_at": iso(t.created_at),
    }


@router.get("/me/tokens")
def list_tokens(p: Who, session: Session_) -> list[dict]:
    q = (
        select(PersonalAccessToken)
        .where(PersonalAccessToken.user_id == p.user_id)
        .order_by(PersonalAccessToken.created_at.desc(), PersonalAccessToken.id.desc())
    )
    return [_view(t) for t in session.scalars(q)]


@router.post("/me/tokens", status_code=201)
def create_token(body: TokenBody, p: Who, session: Session_) -> dict:
    scopes = (
        ["manage", "read", "write"]
        if "manage" in body.scopes
        else ["read", "write"]
        if "write" in body.scopes
        else ["read"]
    )
    row, token = pats.create(session, p.user_id, body.name, scopes, body.expires_in_days)
    audit(session, p, "pat.create", str(row.id), {"scopes": scopes})
    session.commit()
    session.refresh(row)
    return {**_view(row), "token": token}


@router.delete("/me/tokens/{token_id}", status_code=204)
def revoke_token(token_id: str, p: Who, session: Session_) -> Response:
    try:
        tid = UUID(token_id)
    except ValueError:
        raise HTTPException(404, "not found") from None
    row = session.scalar(
        select(PersonalAccessToken).where(
            PersonalAccessToken.id == tid, PersonalAccessToken.user_id == p.user_id
        )
    )
    if row is None:
        raise HTTPException(404, "not found")
    if row.revoked_at is None:
        row.revoked_at = datetime.now(UTC)
        audit(session, p, "pat.revoke", str(row.id))
        session.commit()
    return Response(status_code=204)
