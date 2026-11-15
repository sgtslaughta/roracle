"""Annotation read rule (plan Global Constraints), evaluated on the thread root."""

from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import and_, exists, false, func, or_, select, true
from sqlalchemy.orm import Session, aliased
from sqlalchemy.sql.elements import ColumnElement

from roracle.access import visible_documents
from roracle.auth import Principal
from roracle.models import Annotation, AnnotationShare, Document, DocumentVersion


def _b(v: bool) -> ColumnElement[bool]:
    return true() if v else false()


def readable_doc_ids(p: Principal):
    """Documents the caller may annotate/read; non-owner non-admins need a searchable version (library._visible)."""
    searchable = exists(
        select(1).where(DocumentVersion.document_id == Document.id, DocumentVersion.stage == "searchable")
    )
    return (
        select(Document.id)
        .where(visible_documents(p), or_(_b(p.is_admin), Document.owner_id == p.user_id, searchable))
        .correlate(None)
    )


def doc_readable(session: Session, p: Principal, doc_id: UUID) -> bool:
    return (
        session.scalar(select(Document.id).where(Document.id == doc_id, Document.id.in_(readable_doc_ids(p))))
        is not None
    )


def _status_ok(e, p: Principal):
    owned = select(Document.id).where(Document.owner_id == p.user_id).correlate(None)
    return and_(
        e.status != "removed",
        or_(e.status == "visible", e.author_id == p.user_id, _b(p.is_admin), e.document_id.in_(owned)),
    )


def readable_notes(p: Principal) -> ColumnElement[bool]:
    root = aliased(Annotation)
    ids = readable_doc_ids(p)
    shared = exists(
        select(1).where(
            AnnotationShare.annotation_id == root.id,
            or_(
                and_(AnnotationShare.principal_kind == "user", AnnotationShare.principal == str(p.user_id)),
                and_(
                    AnnotationShare.principal_kind == "group",
                    AnnotationShare.principal.in_(list(p.groups)) if p.groups else false(),
                ),
            ),
        )
    )
    root_ok = exists(
        select(1).where(
            root.id == func.coalesce(Annotation.parent_id, Annotation.id),
            _status_ok(root, p),
            root.document_id.in_(ids),
            or_(root.target_document_id.is_(None), root.target_document_id.in_(ids)),
            or_(
                _b(p.is_admin),
                root.author_id == p.user_id,
                root.visibility == "everyone",
                and_(root.visibility == "shared", shared),
            ),
        )
    )
    return and_(_status_ok(Annotation, p), root_ok)


def get_readable(session: Session, p: Principal, note_id: UUID) -> Annotation:
    n = session.scalar(select(Annotation).where(Annotation.id == note_id, readable_notes(p)))
    if n is None:
        raise HTTPException(404, "not found")
    return n
