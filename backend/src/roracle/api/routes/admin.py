from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import delete, func, literal, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, aliased

from roracle.api.routes.admin_deps import Admin, keyset_page, keyset_params, page_params
from roracle.api.routes.library import normalize_tag
from roracle.audit import audit
from roracle.db import get_session
from roracle.models import AuditLog, Document, DocumentVersion, PersonalAccessToken, Tag, User
from roracle.notes import moderation
from roracle.queue import enqueue

router = APIRouter()


@router.get("/admin/needs-title")
def needs_title(
    p: Admin,
    session: Annotated[Session, Depends(get_session)],
) -> list[dict]:
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
    p: Admin,
    session: Annotated[Session, Depends(get_session)],
) -> Response:
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
    p: Admin,
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    moderation.remove(session, p, note_id)
    session.commit()
    return Response(status_code=204)


@router.get("/admin/users")
def list_users(
    p: Admin,
    session: Annotated[Session, Depends(get_session)],
    paging: Annotated[tuple[int, int], Depends(page_params)],
    q: str = "",
) -> dict:
    offset, limit = paging
    usage = (
        select(
            Document.owner_id.label("owner_id"),
            func.count(func.distinct(Document.id)).label("documents"),
            func.coalesce(func.sum(DocumentVersion.size), 0).label("bytes"),
        )
        .join(DocumentVersion, DocumentVersion.document_id == Document.id)
        .where(Document.deleted_at.is_(None), DocumentVersion.stage.not_in(("rejected", "cancelled")))
        .group_by(Document.owner_id)
        .subquery()
    )
    where = []
    if q:
        like = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        where = [User.username.ilike(like) | User.email.ilike(like)]
    total = session.scalar(select(func.count()).select_from(User).where(*where)) or 0
    rows = session.execute(
        select(User, func.coalesce(usage.c.documents, 0), func.coalesce(usage.c.bytes, 0))
        .outerjoin(usage, usage.c.owner_id == User.id)
        .where(*where)
        .order_by(User.username, User.id)
        .offset(offset)
        .limit(limit)
    ).all()
    items = [
        {
            "id": str(u.id),
            "username": u.username,
            "email": u.email,
            "groups": u.groups,
            "is_admin": u.is_admin,
            "created_at": u.created_at.isoformat(),
            "last_login_at": u.last_login_at.isoformat() if u.last_login_at else None,
            "documents": int(n),
            "bytes": int(b),
        }
        for u, n, b in rows
    ]
    return {"items": items, "total": total}


def _like(q: str) -> str:
    return "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


@router.get("/admin/groups")
def list_groups(
    p: Admin,
    session: Annotated[Session, Depends(get_session)],
    q: str = "",
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict:
    grp = select(func.jsonb_array_elements_text(User.groups).label("grp")).select_from(User).subquery()
    where = [grp.c.grp.ilike(_like(q), escape="\\")] if q else []
    total = session.scalar(select(func.count(func.distinct(grp.c.grp))).where(*where)) or 0
    rows = session.execute(
        select(grp.c.grp, func.count())
        .where(*where)
        .group_by(grp.c.grp)
        .order_by(grp.c.grp)
        .limit(limit)
        .offset(offset)
    ).all()
    return {"items": [{"group": g, "members": n} for g, n in rows], "total": total}


@router.get("/admin/review")
def review_queue(
    p: Admin,
    session: Annotated[Session, Depends(get_session)],
    paging: Annotated[tuple[int, int], Depends(page_params)],
    source: Annotated[Literal["all", "upload", "connector"], Query()] = "all",
) -> dict:
    """Offset-paged (`page`, `limit`), oldest first; the queue is small and versions have uuid ids."""
    offset, limit = paging
    m, md = aliased(DocumentVersion), aliased(Document)
    where = [DocumentVersion.stage == "review", Document.deleted_at.is_(None)]
    if source == "upload":
        where.append(Document.connector_id.is_(None))
    elif source == "connector":
        where.append(Document.connector_id.is_not(None))
    base = (
        select(DocumentVersion, Document, User.username, m.document_id, md.title)
        .join(Document, Document.id == DocumentVersion.document_id)
        .join(User, User.id == Document.owner_id)
        .outerjoin(m, m.id == DocumentVersion.near_dup_of)
        .outerjoin(md, md.id == m.document_id)
        .where(*where)
    )
    total = session.scalar(
        select(func.count())
        .select_from(DocumentVersion)
        .join(Document, Document.id == DocumentVersion.document_id)
        .where(*where)
    )
    rows = session.execute(
        base.order_by(DocumentVersion.created_at, DocumentVersion.id).offset(offset).limit(limit)
    ).all()
    items = [
        {
            "document_id": str(d.id),
            "version_id": str(v.id),
            "filename": v.filename,
            "owner": owner,
            "connector_id": str(d.connector_id) if d.connector_id else None,
            "uploaded_at": v.created_at.isoformat(),
            "match": {"document_id": str(mdoc), "title": mtitle} if mdoc else None,
        }
        for v, d, owner, mdoc, mtitle in rows
    ]
    return {"items": items, "total": total}


@router.get("/admin/tags")
def list_tags(
    p: Admin,
    session: Annotated[Session, Depends(get_session)],
    q: str = "",
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict:
    where = [Tag.tag.ilike(_like(q), escape="\\")] if q else []
    total = session.scalar(select(func.count(func.distinct(Tag.tag))).where(*where)) or 0
    rows = session.execute(
        select(Tag.tag, func.count())
        .where(*where)
        .group_by(Tag.tag)
        .order_by(func.count().desc(), Tag.tag)
        .limit(limit)
        .offset(offset)
    ).all()
    return {"items": [{"tag": t, "documents": n} for t, n in rows], "total": total}


class TagMerge(BaseModel):
    sources: list[str] = Field(min_length=1, max_length=50)
    target: str

    @model_validator(mode="after")
    def _norm(self):
        self.sources = sorted({normalize_tag(s) for s in self.sources})
        self.target = normalize_tag(self.target)
        return self


@router.post("/admin/tags/merge")
def merge_tags(body: TagMerge, p: Admin, session: Annotated[Session, Depends(get_session)]) -> dict:
    sources = [s for s in body.sources if s != body.target]
    docs = (
        list(session.scalars(select(Tag.document_id).where(Tag.tag.in_(sources)).distinct()))
        if sources
        else []
    )
    if docs:
        session.execute(
            insert(Tag)
            .from_select(
                ["document_id", "tag"],
                select(Tag.document_id, literal(body.target)).where(Tag.tag.in_(sources)).distinct(),
            )
            .on_conflict_do_nothing()
        )
        session.execute(delete(Tag).where(Tag.tag.in_(sources)))
        for d in docs:
            enqueue(session, "acl_sync", {"doc_id": str(d)})
    audit(
        session,
        p,
        "admin.tags_merge",
        body.target,
        {"sources": sources, "target": body.target, "documents": len(docs)},
    )
    return {"documents": len(docs), "document_ids": sorted(str(d) for d in docs), "target": body.target}


@router.get("/admin/audit")
def list_audit(
    p: Admin,
    session: Annotated[Session, Depends(get_session)],
    paging: Annotated[tuple[int | None, int], Depends(keyset_params)],
    action: str = "",
    user_id: UUID | None = None,
) -> dict:
    before_id, limit = paging
    q = select(AuditLog, User.username).outerjoin(User, User.id == AuditLog.user_id)
    if action:
        esc = action.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        q = q.where(AuditLog.action.like(esc + "%", escape="\\"))
    if user_id:
        q = q.where(AuditLog.user_id == user_id)
    if before_id:
        q = q.where(AuditLog.id < before_id)
    rows = session.execute(q.order_by(AuditLog.id.desc()).limit(limit + 1)).all()
    items = [
        {
            "id": a.id,
            "at": a.at.isoformat(),
            "user_id": str(a.user_id) if a.user_id else None,
            "username": name,
            "action": a.action,
            "target": a.target,
            "client": a.client,
            "detail": a.detail,
        }
        for a, name in rows
    ]
    return keyset_page(items, limit)
