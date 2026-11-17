"""Annotation REST (P8 task 2). Writes are JWT-only (R3)."""

from typing import Annotated, Literal
from uuid import UUID

from elasticsearch import Elasticsearch
from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from roracle.api.routes.library import get_es
from roracle.auth import Principal, current_principal, require_human
from roracle.db import get_session
from roracle.models import Annotation
from roracle.notes import anchor, moderation, service
from roracle.notes.access import doc_readable, get_readable, readable_notes
from roracle.notes.schemas import NoteIn, NotePatch, ReplyIn

router = APIRouter()
Session_ = Annotated[Session, Depends(get_session)]
Who = Annotated[Principal, Depends(current_principal)]
Human = Annotated[Principal, Depends(require_human)]
ES = Annotated[Elasticsearch, Depends(get_es)]
LIST_MAX = 500


def _one(session: Session, p: Principal, n: Annotation) -> dict:
    return service.to_json(session, p, [n])[0]


def page_document_notes(
    session: Session, es, p: Principal, doc_id: UUID, limit: int = LIST_MAX, offset: int = 0
) -> tuple[list[dict], int] | None:
    """One page of readable notes (oldest first, re-anchored) and the readable total; None if unreadable."""
    if not doc_readable(session, p, doc_id):
        return None
    where = (Annotation.document_id == doc_id, readable_notes(p))
    total = session.scalar(select(func.count()).select_from(Annotation).where(*where)) or 0
    q = select(Annotation).where(*where).order_by(Annotation.created_at, Annotation.id)
    notes = list(session.scalars(q.limit(limit).offset(offset)))
    latest = service.latest_searchable(session, doc_id)
    if latest is not None:
        anchor.reanchor(session, es, latest, notes)
    return service.to_json(session, p, notes), total


def list_document_notes(session: Session, es, p: Principal, doc_id: UUID) -> list[dict] | None:
    """Readable notes of a document (oldest first, up to LIST_MAX); None if the document is not readable."""
    page = page_document_notes(session, es, p, doc_id)
    return None if page is None else page[0]


@router.get("/documents/{doc_id}/annotations")
def list_notes(
    doc_id: UUID,
    session: Session_,
    es: ES,
    p: Who,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict:
    page = page_document_notes(session, es, p, doc_id, limit, offset)
    if page is None:
        raise service.NOT_FOUND
    return {"items": page[0], "total": page[1]}


@router.post("/documents/{doc_id}/annotations", status_code=201)
def create_note(doc_id: UUID, body: NoteIn, session: Session_, p: Human) -> dict:
    n = service.create(session, p, doc_id, body)
    out = _one(session, p, n)
    session.commit()
    return out


@router.get("/annotations/{note_id}")
def get_note(note_id: UUID, session: Session_, p: Who) -> dict:
    return _one(session, p, get_readable(session, p, note_id))


@router.patch("/annotations/{note_id}")
def patch_note(note_id: UUID, body: NotePatch, session: Session_, p: Human) -> dict:
    out = _one(session, p, service.patch(session, p, note_id, body))
    session.commit()
    return out


@router.delete("/annotations/{note_id}", status_code=204)
def delete_note(note_id: UUID, session: Session_, p: Human) -> Response:
    service.delete(session, p, note_id)
    session.commit()
    return Response(status_code=204)


@router.post("/annotations/{note_id}/replies", status_code=201)
def reply_note(note_id: UUID, body: ReplyIn, session: Session_, p: Human) -> dict:
    out = _one(session, p, service.reply(session, p, note_id, body))
    session.commit()
    return out


class VoteIn(BaseModel):
    value: Literal[-1, 0, 1]


class ReportIn(BaseModel):
    reason: Literal["spam", "abusive", "wrong", "other"]


class HideIn(BaseModel):
    hidden: bool


@router.put("/annotations/{note_id}/vote")
def vote_note(note_id: UUID, body: VoteIn, session: Session_, p: Human) -> dict:
    out = moderation.vote(session, p, note_id, body.value)
    session.commit()
    return out


@router.post("/annotations/{note_id}/report", status_code=204)
def report_note(note_id: UUID, body: ReportIn, session: Session_, p: Human) -> Response:
    moderation.report(session, p, note_id, body.reason)
    session.commit()
    return Response(status_code=204)


@router.post("/annotations/{note_id}/hide")
def hide_note(note_id: UUID, body: HideIn, session: Session_, p: Human) -> dict:
    out = _one(session, p, moderation.set_hidden(session, p, note_id, body.hidden))
    session.commit()
    return out
