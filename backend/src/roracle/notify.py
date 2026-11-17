"""In-app notifications: rows added to the caller's transaction (never commits)."""

from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from roracle.models import Document, DocumentVersion, Notification, User
from roracle.queue import USER_PREFIX, NonRetryable, payload_uuid


def emit(session: Session, user_id: UUID, kind: str, payload: dict[str, Any]) -> None:
    session.add(Notification(user_id=user_id, kind=kind, payload=payload))
    session.flush()


def emit_admins(session: Session, kind: str, payload: dict[str, Any]) -> None:
    for uid in session.scalars(select(User.id).where(User.is_admin.is_(True))):
        emit(session, uid, kind, payload)


def on_job_failed(session: Session, job) -> None:
    """Permanent job failure: the uploader (ingest) or every admin (other kinds) hears about it."""
    err = job.last_error or ""
    if job.kind != "ingest":
        emit_admins(
            session, "ops.job_failed", {"job_id": job.id, "kind": job.kind, "error": "processing failed"}
        )
        return
    try:
        v = session.get(DocumentVersion, payload_uuid(job.payload or {}, "version_id"))
    except NonRetryable:  # malformed payload: nobody to tell
        return
    d = session.get(Document, v.document_id) if v else None
    if d is None:
        return
    if d.connector_id is not None:  # no uploader to tell: admins hear about it
        emit_admins(
            session,
            "ops.job_failed",
            {
                "job_id": job.id,
                "kind": job.kind,
                "error": "processing failed",
                "document_id": str(d.id),
                "connector_id": str(d.connector_id),
            },
        )
        return
    emit(
        session,
        d.owner_id,
        "upload.failed",
        {
            "document_id": str(d.id),
            "version_id": str(v.id),
            "filename": v.filename,
            "error": err[len(USER_PREFIX) :] if err.startswith(USER_PREFIX) else "processing failed",
        },
    )
