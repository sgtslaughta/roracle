from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from roracle.auth import Principal, current_principal
from roracle.db import get_session
from roracle.models import Document, DocumentVersion, User

router = APIRouter()


@router.get("/admin/needs-title")
def needs_title(
    p: Annotated[Principal, Depends(current_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> list[dict]:
    if not p.is_admin:
        raise HTTPException(403, "admin only")
    rows = session.execute(
        select(DocumentVersion, Document, User.username)
        .join(Document, Document.id == DocumentVersion.document_id)
        .join(User, User.id == Document.owner_id)
        .where(DocumentVersion.stage == "needs_title", Document.deleted_at.is_(None))
        .order_by(DocumentVersion.created_at)
    ).all()
    return [
        {
            "document_id": str(d.id),
            "version_id": str(v.id),
            "filename": v.filename,
            "owner": username,
            "uploaded_at": v.created_at.isoformat(),
        }
        for v, d, username in rows
    ]
