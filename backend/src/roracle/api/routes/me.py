from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import String, cast, func, select, true
from sqlalchemy.orm import Session

from roracle.auth import Principal, current_principal
from roracle.db import get_session
from roracle.models import Document, DocumentVersion, Job
from roracle.queue import USER_PREFIX

router = APIRouter()


def _public_error(status: str | None, last_error: str | None) -> str | None:
    """Never expose raw last_error: only NonRetryable (user:-prefixed) text passes through."""
    if last_error is None or status in ("done", "running"):
        return None
    if last_error.startswith(USER_PREFIX):
        return last_error[len(USER_PREFIX) :]
    return "Processing failed" if status == "failed" else "Processing failed \u2014 retrying"


@router.get("/me")
def me(p: Annotated[Principal, Depends(current_principal)]) -> dict:
    return {"id": str(p.user_id), "username": p.username, "groups": list(p.groups), "is_admin": p.is_admin}


@router.get("/me/uploads")
def my_uploads(
    p: Annotated[Principal, Depends(current_principal)],
    session: Annotated[Session, Depends(get_session)],
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict:
    owned = (Document.owner_id == p.user_id, Document.deleted_at.is_(None))
    total = session.scalar(select(func.count()).select_from(Document).where(*owned))
    # latest version per document, then its most recent job (both lateral: no N+1)
    ver = (
        select(DocumentVersion)
        .where(DocumentVersion.document_id == Document.id)
        .order_by(DocumentVersion.version.desc())
        .limit(1)
        .lateral("ver")
    )
    job = (
        select(Job.status, Job.last_error)
        .where(Job.payload["version_id"].astext == cast(ver.c.id, String))
        .order_by(Job.created_at.desc(), Job.id.desc())
        .limit(1)
        .lateral("job")
    )
    rows = session.execute(
        select(
            Document.id,
            Document.title,
            Document.visibility,
            ver.c.id.label("version_id"),
            ver.c.filename,
            ver.c.size,
            ver.c.stage,
            ver.c.created_at,
            job.c.status,
            job.c.last_error,
        )
        .select_from(Document)
        .join(ver, true())
        .outerjoin(job, true())
        .where(*owned)
        .order_by(ver.c.created_at.desc(), Document.id.desc())
        .limit(limit)
        .offset(offset)
    ).all()
    items = [
        {
            "document_id": str(r.id),
            "version_id": str(r.version_id),
            "title": r.title,
            "filename": r.filename,
            "size": r.size,
            "visibility": r.visibility,
            "status": r.status or "queued",
            "stage": r.stage,
            "error": _public_error(r.status, r.last_error),
            "uploaded_at": r.created_at.isoformat(),
        }
        for r in rows
    ]
    return {"items": items, "total": total}
