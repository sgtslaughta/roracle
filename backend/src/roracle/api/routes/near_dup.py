from datetime import UTC, datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from roracle.auth import Principal, current_principal
from roracle.db import get_session
from roracle.models import Blob, Document, DocumentShare, DocumentVersion
from roracle.queue import enqueue

router = APIRouter()


class Decision(BaseModel):
    action: Literal["new_version", "separate", "cancel"]


@router.post("/documents/{doc_id}/versions/{version_id}/near-dup")
def decide_near_dup(
    doc_id: UUID,
    version_id: UUID,
    body: Decision,
    p: Annotated[Principal, Depends(current_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> dict:
    row = session.execute(
        select(DocumentVersion, Document)
        .join(Document, Document.id == DocumentVersion.document_id)
        .where(DocumentVersion.id == version_id, Document.id == doc_id, Document.deleted_at.is_(None))
        .with_for_update(of=DocumentVersion)
    ).first()
    if row is None or not (row[1].owner_id == p.user_id or p.is_admin):
        raise HTTPException(404, "not found")
    v, doc = row
    if v.stage != "review":
        raise HTTPException(409, "not awaiting a decision")
    if body.action == "cancel":
        v.stage = "cancelled"
        doc.deleted_at = datetime.now(UTC)
        session.execute(
            update(Blob)
            .where(Blob.sha256 == v.blob_sha256)
            .values(refcount=func.greatest(Blob.refcount - 1, 0))
        )
    else:
        if body.action == "new_version":
            _move_under_match(session, p, v, doc)
        v.stage = "deduped"
        enqueue(session, "ingest", {"version_id": str(v.id)})
    session.commit()
    return {"version_id": str(v.id), "document_id": str(v.document_id), "stage": v.stage}


def _move_under_match(session: Session, p: Principal, v: DocumentVersion, doc: Document) -> None:
    match = session.get(DocumentVersion, v.near_dup_of) if v.near_dup_of else None
    target = session.get(Document, match.document_id, with_for_update=True) if match else None
    if target is None or target.deleted_at is not None:
        raise HTTPException(409, "matched document no longer exists")
    if not (target.owner_id == p.user_id or p.is_admin):
        raise HTTPException(403, "only the document owner can add versions")
    top = session.scalar(
        select(func.max(DocumentVersion.version)).where(DocumentVersion.document_id == target.id)
    )
    session.execute(
        update(DocumentVersion).where(DocumentVersion.document_id == target.id).values(is_latest=False)
    )
    old_doc_id = v.document_id
    v.document_id, v.version, v.is_latest = target.id, (top or 0) + 1, True
    session.flush()
    # The uploader's document is now empty: hard-delete it (its shares first; no other FKs point at it).
    session.execute(delete(DocumentShare).where(DocumentShare.document_id == old_doc_id))
    session.execute(delete(Document).where(Document.id == old_doc_id))
