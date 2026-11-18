"""DELETE /documents/{id} (soft delete) and POST /documents/{id}/transfer (owner or admin)."""

import logging
from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from elasticsearch import Elasticsearch
from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from roracle import search_index
from roracle.access import visible_documents
from roracle.acl_sync import durable_job
from roracle.api.routes.documents_read import Session_, Who, detail, privileged, visible_doc
from roracle.api.routes.documents_write import ACL_JOB_DELAY_S, recover
from roracle.api.routes.library import get_es, get_session_factory
from roracle.audit import audit
from roracle.auth import MANAGE, require_scope
from roracle.models import Document, DocumentVersion, User
from roracle.models_writing import Draft
from roracle.notify import emit
from roracle.queue import enqueue

router = APIRouter()
log = logging.getLogger(__name__)


class TransferBody(BaseModel):
    user_id: UUID


def _owned(session: Session, p, doc_id: UUID) -> Document:
    """Visible doc, row-locked FIRST, then authorized against the locked (fresh) owner."""
    doc = visible_doc(session, p, doc_id)
    session.refresh(doc, with_for_update=True)
    if doc.deleted_at is not None:
        raise HTTPException(404, "not found")
    if not privileged(doc, p):
        if not session.scalar(
            select(DocumentVersion.id).where(
                DocumentVersion.document_id == doc_id, DocumentVersion.stage == "searchable"
            )
        ):
            raise HTTPException(404, "not found")  # R16: same rule as PATCH, no existence oracle
        raise HTTPException(403, "only the document owner or an admin can do this")
    if session.get(Draft, doc_id):
        raise HTTPException(409, "use the /drafts endpoints")
    return doc


@router.delete("/documents/{doc_id}", status_code=204)
def delete_document(
    doc_id: UUID,
    p: Who,
    session: Session_,
    es: Annotated[Elasticsearch, Depends(get_es)],
) -> Response:
    """Soft delete: Postgres hides it everywhere at once (R19); chunks leave ES now (best effort), and
    the retention purge (maintenance.purge) hard-deletes it after `retention_deleted_days`."""
    require_scope(session, p, MANAGE)
    doc = _owned(session, p, doc_id)
    doc.deleted_at = datetime.now(UTC)
    audit(session, p, "document.delete", str(doc_id), {"title": doc.title, "owner_id": str(doc.owner_id)})
    if doc.owner_id != p.user_id:
        emit(session, doc.owner_id, "document.deleted", {"document_id": str(doc_id), "title": doc.title})
    enqueue(session, "acl_sync", {"doc_id": str(doc_id)})
    session.commit()
    try:
        es.delete_by_query(
            index=search_index.MUTATE_ALIAS,
            query={"term": {"doc_id": str(doc_id)}},
            refresh=True,
            conflicts="proceed",
        )
    except Exception:
        log.exception("ES chunk delete failed for %s; the retention purge will remove them", doc_id)
    return Response(status_code=204)


@router.post("/documents/{doc_id}/transfer")
def transfer_document(
    doc_id: UUID,
    body: TransferBody,
    p: Who,
    session: Session_,
    es: Annotated[Elasticsearch, Depends(get_es)],
    sf: Annotated[object, Depends(get_session_factory)],
) -> dict:
    require_scope(session, p, MANAGE)
    doc = _owned(session, p, doc_id)
    if session.get(User, body.user_id) is None:
        raise HTTPException(422, "unknown user")
    old = doc.owner_id
    if old == body.user_id:
        return detail(doc_id, p, session)
    doc.owner_id = body.user_id
    doc.connector_id = None  # the connector's next run must not add versions to the new owner's document
    session.flush()
    audit(session, p, "document.transfer", str(doc_id), {"from": str(old), "to": str(body.user_id)})
    note = {"document_id": str(doc_id), "title": doc.title, "from": str(old), "to": str(body.user_id)}
    for uid in {old, body.user_id}:
        emit(session, uid, "document.transferred", note)
    durable_job(sf, doc_id, ACL_JOB_DELAY_S)  # converges even if we die between ES and commit
    try:
        search_index.sync_acl(es, doc_id, lambda: (search_index.acl_fields(session, doc), None))
        enqueue(session, "acl_sync", {"doc_id": str(doc_id)})
        session.commit()
    except Exception:
        log.exception("document %s transfer failed; rolling back", doc_id)
        session.rollback()
        recover(es, sf, doc_id)
        raise HTTPException(503, "transfer failed, please retry") from None
    return detail(doc_id, p, session) if _can_see(session, p, doc_id) else {"id": str(doc_id)}


def _can_see(session: Session, p, doc_id: UUID) -> bool:
    return session.scalar(select(Document.id).where(Document.id == doc_id, visible_documents(p))) is not None
