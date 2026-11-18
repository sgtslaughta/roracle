"""Per-user upload quota (`upload_quota_bytes`): live uploaded document versions plus evidence files."""

from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from roracle.config import Settings
from roracle.models import Document, DocumentVersion, Evidence


def used_bytes(session: Session, user_id: UUID) -> int:
    docs = (
        select(func.coalesce(func.sum(DocumentVersion.size), 0))
        .join(Document, Document.id == DocumentVersion.document_id)
        .where(
            Document.owner_id == user_id,
            Document.connector_id.is_(None),  # connector documents are exempt
            Document.deleted_at.is_(None),  # deleted papers free their quota
            DocumentVersion.stage.not_in(("rejected", "cancelled")),
        )
        .scalar_subquery()
    )
    files = (
        select(func.coalesce(func.sum(Evidence.size), 0))
        .where(Evidence.creator_id == user_id, Evidence.blob_sha256.is_not(None))
        .scalar_subquery()
    )
    return int(session.scalar(select(docs + files)) or 0)


def check(session: Session, settings: Settings, user_id: UUID, size: int) -> None:
    """413 when `size` more bytes would exceed the quota. Takes a per-user advisory lock held until commit, so
    concurrent uploads (documents or evidence) cannot overshoot."""
    if settings.upload_quota_bytes <= 0:
        return
    session.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:u, 0))"), {"u": str(user_id)})
    if used_bytes(session, user_id) + size > settings.upload_quota_bytes:
        raise HTTPException(413, "upload quota exceeded")
