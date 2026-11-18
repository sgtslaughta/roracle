"""Label read + document labelling."""

import logging
from typing import Annotated
from uuid import UUID

from elasticsearch import Elasticsearch
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from roracle.api.routes.documents_read import Session_, privileged, visible_doc
from roracle.api.routes.documents_write import commit_with_acl
from roracle.api.routes.library import get_es, get_session_factory
from roracle.auth import Principal, current_principal, require_human
from roracle.db import get_session
from roracle.labels import service
from roracle.labels.policy import Policy, admin_json, label_json
from roracle.models import DocumentVersion
from roracle.notes import service as notes_service
from roracle.notes.access import get_readable
from roracle.queue import enqueue

router = APIRouter()
log = logging.getLogger(__name__)


@router.get("/labels")
def list_labels(
    p: Annotated[Principal, Depends(current_principal)], session: Annotated[Session, Depends(get_session)]
) -> dict:
    return {"items": [admin_json(x) for x in Policy.load(session).labels]}


class LabelBody(BaseModel):
    label_id: UUID
    reason: str | None = None


@router.put("/documents/{doc_id}/label")
def set_label(
    doc_id: UUID,
    body: LabelBody,
    p: Annotated[Principal, Depends(require_human)],
    session: Session_,
    es: Annotated[Elasticsearch, Depends(get_es)],
    sf: Annotated[object, Depends(get_session_factory)],
) -> dict:
    doc = visible_doc(session, p, doc_id)
    session.refresh(doc, with_for_update=True)
    if not privileged(doc, p) and not session.scalar(
        select(DocumentVersion.id).where(
            DocumentVersion.document_id == doc_id, DocumentVersion.stage == "searchable"
        )
    ):
        raise HTTPException(404, "not found")  # R16
    old = doc.label_id
    new = service.set_document_label(session, p, doc, body.label_id, body.reason)
    if doc.label_id == old:
        return {"label": label_json(new)}
    enqueue(session, "acl_sync", {"doc_id": str(doc_id)})
    commit_with_acl(session, es, sf, [doc])
    return {"label": label_json(new)}


class NoteLabelBody(BaseModel):
    label_id: UUID | None = None  # null = inherit the document label
    reason: str | None = None


@router.put("/annotations/{note_id}/label")
def set_note_label(
    note_id: UUID, body: NoteLabelBody, p: Annotated[Principal, Depends(require_human)], session: Session_
) -> dict:
    note = get_readable(session, p, note_id)  # unreadable -> 404
    service.set_note_label(session, p, note, body.label_id, body.reason)
    session.refresh(note)
    out = notes_service.to_json(session, p, [note])[0]
    session.commit()
    return out
