from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from roracle.audit import audit
from roracle.auth import Principal, current_principal, require_human
from roracle.db import get_session
from roracle.models import Document, DocumentVersion, PersonalAccessToken, User
from roracle.notes import moderation

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


@router.delete("/admin/users/{user_id}/pats", status_code=204)
def revoke_user_pats(
    user_id: UUID,
    p: Annotated[Principal, Depends(current_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    if not p.is_admin:
        raise HTTPException(403, "admin only")
    session.execute(
        update(PersonalAccessToken)
        .where(PersonalAccessToken.user_id == user_id, PersonalAccessToken.revoked_at.is_(None))
        .values(revoked_at=func.now())
    )
    audit(session, p, "admin.revoke_pats", str(user_id))
    return Response(status_code=204)


@router.delete("/admin/annotations/{note_id}", status_code=204)
def remove_annotation(
    note_id: UUID,
    p: Annotated[Principal, Depends(require_human)],
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    moderation.remove(session, p, note_id)
    session.commit()
    return Response(status_code=204)
