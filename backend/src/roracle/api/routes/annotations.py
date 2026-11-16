"""Annotation REST (P8 task 2). Writes are JWT-only (R3)."""

from typing import Annotated, Literal
from uuid import UUID

from elasticsearch import Elasticsearch
from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel
from sqlalchemy import select
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


def list_document_notes(session: Session, es, p: Principal, doc_id: UUID) -> list[dict] | None:
    """Readable notes of a document (oldest first, re-anchored first); None if the document is not readable."""
    if not doc_readable(session, p, doc_id):
        return None
    q = (
        select(Annotation)
        .where(Annotation.document_id == doc_id, readable_notes(p))
        .order_by(Annotation.created_at, Annotation.id)
        .limit(LIST_MAX)
    )
    notes = list(session.scalars(q))
    latest = service.latest_searchable(session, doc_id)
    if latest is not None:
        anchor.reanchor(session, es, latest, notes)
    return service.to_json(session, p, notes)


@router.get("/documents/{doc_id}/annotations")
def list_notes(doc_id: UUID, session: Session_, es: ES, p: Who) -> dict:
    items = list_document_notes(session, es, p, doc_id)
    if items is None:
        raise service.NOT_FOUND
    return {"items": items}


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
