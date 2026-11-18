from datetime import UTC, datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import delete, func, literal, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from roracle.access import agent_visible
from roracle.api.routes.library import MAX_TAGS
from roracle.auth import Principal, current_principal
from roracle.db import get_session
from roracle.labels.service import raise_document_label
from roracle.models import (
    Annotation,
    Blob,
    CollectionItem,
    ConnectorItem,
    Document,
    DocumentShare,
    DocumentVersion,
    Tag,
    Vote,
)
from roracle.models_writing import Draft
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
        session.flush()
        if not _has_live_version(session, doc.id) and session.get(Draft, doc.id) is None:
            doc.deleted_at = datetime.now(UTC)  # only the doc's last live version: never a draft or a v1
        session.execute(
            update(Blob)
            .where(Blob.sha256 == v.blob_sha256)
            .values(refcount=func.greatest(Blob.refcount - 1, 0))
        )
    else:
        if body.action == "new_version":
            _move_under_match(session, p, v, doc)
        v.stage = "deduped"
        v.ingest_attempts = 0  # R17
        enqueue(session, "ingest", {"version_id": str(v.id)})
    session.commit()
    return {"version_id": str(v.id), "document_id": str(v.document_id), "stage": v.stage}


def _has_live_version(session: Session, doc_id: UUID) -> bool:
    return (
        session.scalar(
            select(DocumentVersion.id)
            .where(DocumentVersion.document_id == doc_id, DocumentVersion.stage != "cancelled")
            .limit(1)
        )
        is not None
    )


def _move_under_match(session: Session, p: Principal, v: DocumentVersion, doc: Document) -> None:
    match = session.get(DocumentVersion, v.near_dup_of) if v.near_dup_of else None
    target = session.get(Document, match.document_id, with_for_update=True) if match else None
    if (  # first: a 403 below would tell an agent whose the hidden match is
        p.is_agent
        and target is not None
        and session.scalar(select(Document.id).where(Document.id == target.id, agent_visible())) is None
    ):
        raise HTTPException(409, "matched document no longer exists")  # hidden from agents: say nothing more
    if target is not None and not (target.owner_id == p.user_id or p.is_admin):
        raise HTTPException(403, "only the document owner can add versions")
    if target is None or target.deleted_at is not None:
        raise HTTPException(409, "matched document no longer exists")
    if session.get(Draft, doc.id) is not None:
        raise HTTPException(409, "a published draft cannot be merged into another document")
    top = session.scalar(
        select(func.max(DocumentVersion.version)).where(DocumentVersion.document_id == target.id)
    )
    old_doc_id = v.document_id
    if raise_document_label(session, p, target, doc.label_id, via="near_dup"):
        enqueue(session, "acl_sync", {"doc_id": str(target.id)})
    v.document_id, v.version, v.is_latest = target.id, (top or 0) + 1, False
    session.flush()
    others = session.scalar(
        select(func.count()).select_from(DocumentVersion).where(DocumentVersion.document_id == old_doc_id)
    )
    if others:  # a re-version: the old document keeps its other versions (and its library rows)
        return
    _merge_library(session, old_doc_id, target.id)
    # The uploader's document is now empty: hard-delete it (shares first; library rows moved above).
    session.execute(delete(DocumentShare).where(DocumentShare.document_id == old_doc_id))
    session.execute(delete(Document).where(Document.id == old_doc_id))


def _merge_library(session: Session, old: UUID, new: UUID) -> None:
    """Other users may have tagged/voted/collected the doc while in review: carry tags and collections over."""
    have = select(Tag.tag).where(Tag.document_id == new)
    room = MAX_TAGS - (session.scalar(select(func.count()).select_from(have.subquery())) or 0)
    moving = (  # the target's own tags stay; only as many of the old ones as fit under the cap
        select(literal(new), Tag.tag)
        .where(Tag.document_id == old, Tag.tag.not_in(have))
        .order_by(Tag.tag)
        .limit(max(room, 0))
    )
    session.execute(insert(Tag).from_select(["document_id", "tag"], moving).on_conflict_do_nothing())
    session.execute(delete(Tag).where(Tag.document_id == old))
    session.execute(
        insert(CollectionItem)
        .from_select(
            ["collection_id", "document_id"],
            select(CollectionItem.collection_id, literal(new)).where(CollectionItem.document_id == old),
        )
        .on_conflict_do_nothing()
    )
    session.execute(delete(CollectionItem).where(CollectionItem.document_id == old))
    session.execute(update(Annotation).where(Annotation.document_id == old).values(document_id=new))
    session.execute(
        update(Annotation).where(Annotation.target_document_id == old).values(target_document_id=new)
    )
    session.execute(update(ConnectorItem).where(ConnectorItem.document_id == old).values(document_id=new))
    session.execute(delete(Vote).where(Vote.document_id == old))
    # Votes on `new` that pointed at `old` would become self-referential: clear them first.
    session.execute(
        update(Vote)
        .where(Vote.superseded_by == old, Vote.document_id == new)
        .values(superseded_by=None, reason=None)
    )
    session.execute(update(Vote).where(Vote.superseded_by == old).values(superseded_by=new))
    enqueue(session, "acl_sync", {"doc_id": str(new)})  # ES tags follow the merged set
