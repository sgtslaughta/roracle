"""Label read + document labelling."""

import logging
from typing import Annotated
from uuid import UUID

from elasticsearch import Elasticsearch
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from roracle import search_index
from roracle.acl_sync import durable_job as _durable_job
from roracle.api.routes.documents_read import Session_, _doc, _privileged
from roracle.api.routes.documents_write import ACL_JOB_DELAY_S, _recover
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
    doc = _doc(session, p, doc_id)
    session.refresh(doc, with_for_update=True)
    if not _privileged(doc, p) and not session.scalar(
        select(DocumentVersion.id).where(
            DocumentVersion.document_id == doc_id, DocumentVersion.stage == "searchable"
        )
    ):
        raise HTTPException(404, "not found")  # R16
    old = doc.label_id
    new = service.set_document_label(session, p, doc, body.label_id, body.reason)
    if doc.label_id == old:
        return {"label": label_json(new)}
    _durable_job(sf, doc_id, ACL_JOB_DELAY_S)
    try:
        search_index.sync_acl(es, doc_id, lambda: (search_index.acl_fields(session, doc), None))
        enqueue(session, "acl_sync", {"doc_id": str(doc_id)})
        session.commit()
    except Exception:
        log.exception("document %s label update failed; rolling back", doc_id)
        session.rollback()
        _recover(es, sf, doc_id)
        raise HTTPException(503, "update failed, please retry") from None
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
