"""Votes, reports, hide and admin removal (P8 task 3)."""

from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from roracle.audit import audit
from roracle.auth import Principal
from roracle.models import Annotation, AnnotationReport, AnnotationVote, Document
from roracle.notes.access import get_readable
from roracle.retrieval import vote_ratio


def vote(session: Session, p: Principal, note_id: UUID, value: int) -> dict:
    get_readable(session, p, note_id)
    if value == 0:
        session.execute(
            delete(AnnotationVote).where(
                AnnotationVote.annotation_id == note_id, AnnotationVote.user_id == p.user_id
            )
        )
    else:
        stmt = insert(AnnotationVote).values(annotation_id=note_id, user_id=p.user_id, value=value)
        session.execute(
            stmt.on_conflict_do_update(
                index_elements=["annotation_id", "user_id"], set_={"value": stmt.excluded.value}
            )
        )
    rows = dict(
        session.execute(
            select(AnnotationVote.value, func.count())
            .where(AnnotationVote.annotation_id == note_id)
            .group_by(AnnotationVote.value)
        ).all()
    )
    up, down = rows.get(1, 0), rows.get(-1, 0)
    return {"up": up, "down": down, "mine": value, "score": vote_ratio(up, down)}


def report(session: Session, p: Principal, note_id: UUID, reason: str) -> None:
    get_readable(session, p, note_id)
    session.execute(
        insert(AnnotationReport)
        .values(annotation_id=note_id, user_id=p.user_id, reason=reason)
        .on_conflict_do_nothing()
    )


def set_hidden(session: Session, p: Principal, note_id: UUID, hidden: bool) -> Annotation:
    n = get_readable(session, p, note_id)
    owner = session.scalar(select(Document.owner_id).where(Document.id == n.document_id))
    if not (p.is_admin or owner == p.user_id):
        raise HTTPException(403, "only the document owner or an admin may hide notes")
    n.status = "hidden" if hidden else "visible"
    audit(session, p, "annotation.hide" if hidden else "annotation.unhide", str(n.id))
    session.flush()
    session.refresh(n)
    return n


def remove(session: Session, p: Principal, note_id: UUID) -> None:
    if not p.is_admin:
        raise HTTPException(403, "admin only")
    n = get_readable(session, p, note_id)
    n.status = "removed"
    audit(session, p, "annotation.remove", str(n.id), {"document_id": str(n.document_id)})
    session.flush()
