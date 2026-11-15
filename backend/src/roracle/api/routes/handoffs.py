"""Handoff REST: create/list/load/patch/revoke/notes. Logic lives in roracle.handoff."""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from roracle import handoff
from roracle.auth import Principal, current_principal
from roracle.db import get_session

router = APIRouter()
Session_ = Annotated[Session, Depends(get_session)]
Who = Annotated[Principal, Depends(current_principal)]


class Share(BaseModel):
    kind: Literal["user", "group"]
    principal: str = Field(min_length=1)


class CreateBody(BaseModel):
    kind: Literal["chat", "collection", "search", "document", "agent"]
    source_id: str | None = None
    title: str = ""
    goal: str = ""
    brief: str = ""
    doc_ids: list[str] = []
    audience: Literal["creator", "shared", "authenticated"] = "creator"
    shares: list[Share] = []
    expires_in_days: int = 7


class PatchBody(BaseModel):
    title: str | None = None
    goal: str | None = None
    brief: str | None = None
    audience: Literal["creator", "shared", "authenticated"] | None = None
    shares: list[Share] | None = None
    expires_in_days: int | None = None


class NoteBody(BaseModel):
    text: str


@router.post("/handoffs", status_code=201)
def create(body: CreateBody, p: Who, session: Session_) -> dict:
    h = handoff.create(
        session, p, body.kind, source_id=body.source_id, title=body.title, goal=body.goal,
        brief=body.brief, doc_ids=body.doc_ids, audience=body.audience,
        shares=[s.model_dump() for s in body.shares], expires_in_days=body.expires_in_days,
        via="mcp" if p.client.startswith("pat:") else "ui",
    )  # fmt: skip
    session.commit()
    return handoff.view(session, p, h)


@router.get("/handoffs")
def list_mine(p: Who, session: Session_) -> list[dict]:
    return handoff.list_mine(session, p)


@router.get("/handoffs/{code}")
def load(code: str, p: Who, session: Session_) -> dict:
    out = handoff.load(session, p, code)
    session.commit()
    return out


@router.get("/handoffs/{code}/parts/{part}")
def get_part(code: str, part: str, p: Who, session: Session_, page: Annotated[int, Query(ge=1)] = 1) -> dict:
    out = handoff.get_part(session, p, code, part, page)
    session.commit()
    return out


@router.patch("/handoffs/{code}")
def patch(code: str, body: PatchBody, p: Who, session: Session_) -> dict:
    f = body.model_dump(exclude_unset=True)
    h = handoff.update(session, p, code, **f)
    session.commit()
    return handoff.view(session, p, h)


@router.delete("/handoffs/{code}", status_code=204)
def revoke(code: str, p: Who, session: Session_) -> Response:
    handoff.revoke(session, p, code)
    session.commit()
    return Response(status_code=204)


@router.post("/handoffs/{code}/notes", status_code=201)
def note(code: str, body: NoteBody, p: Who, session: Session_) -> dict:
    out = handoff.append_note(session, p, code, body.text)
    session.commit()
    return out
