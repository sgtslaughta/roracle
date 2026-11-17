"""Drafts REST (P9 task 2). Access is `writing.drafts.readable_draft`, never `visible_documents`."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path
from pydantic import AfterValidator, BaseModel, Field, StringConstraints
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from roracle.access import agent_visible, group_path, org_units
from roracle.api.routes.search import get_llm
from roracle.auth import Principal, current_principal, get_app_settings
from roracle.config import Settings
from roracle.db import get_session
from roracle.labels.policy import Policy
from roracle.labels.textgate import require_text
from roracle.models import Document, User
from roracle.models_writing import Draft, DraftVersion
from roracle.writing import drafts as svc
from roracle.writing import validate as validator

router = APIRouter()
Who = Annotated[Principal, Depends(current_principal)]
Db = Annotated[Session, Depends(get_session)]
Cfg = Annotated[Settings, Depends(get_app_settings)]
Ver = Annotated[int, Path(ge=1, le=2**31 - 1)]
LIMIT = 100


class CreateIn(BaseModel):
    title: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]
    content: str | None = None
    profile: Annotated[str, Field(max_length=100)] | None = None


class SaveIn(BaseModel):
    content: str
    base_version: Annotated[int, Field(ge=0, le=2**31 - 1)] | None = None


class CoauthorsIn(BaseModel):
    users: Annotated[list[UUID], Field(max_length=100)] = []
    groups: Annotated[
        list[Annotated[str, Field(max_length=500), AfterValidator(group_path)]], Field(max_length=100)
    ] = []


def _author(session: Session, uid: UUID) -> dict:
    return svc.who(session, uid)


@router.post("/drafts", status_code=201)
def create_draft(body: CreateIn, p: Who, session: Db, cfg: Cfg) -> dict:
    d = svc.create(session, p, body.title, body.content, body.profile, cfg.draft_max_bytes)
    return svc.summary(session, d, session.get(Document, d.document_id))


@router.get("/drafts")
def list_drafts(p: Who, session: Db) -> dict:
    mine = [Document.owner_id == p.user_id, Draft.coauthors["users"].contains([str(p.user_id)])]
    mine += [Draft.coauthors["groups"].contains([g]) for g in org_units(list(p.groups))]
    rows = session.execute(
        select(Draft, Document)
        .join(Document, Document.id == Draft.document_id)
        .where(
            Document.deleted_at.is_(None), or_(*mine), *((agent_visible(),) if p.via == "pat" else ())
        )  # gate (b)
        .order_by(Draft.updated_at.desc(), Draft.document_id)
        .limit(LIMIT)
    ).all()
    tops = dict(
        session.execute(
            select(DraftVersion.document_id, func.max(DraftVersion.version))
            .where(DraftVersion.document_id.in_([doc.id for _, doc in rows]))
            .group_by(DraftVersion.document_id)
        ).all()
    )
    session.scalars(
        select(User).where(User.id.in_({doc.owner_id for _, doc in rows}))
    )  # fills the identity map
    policy = Policy.load(session)
    return {"items": [svc.summary(session, d, doc, tops.get(doc.id, 0), policy) for d, doc in rows]}


@router.get("/drafts/{doc_id}")
def get_draft(doc_id: UUID, p: Who, session: Db) -> dict:
    """Version metadata only; content of older versions lives at `/drafts/{id}/versions/{n}`."""
    doc, d = svc.readable_draft(session, p, doc_id)
    require_text(session, p, doc)  # agents never get the text of exclude_llm documents
    vs = session.execute(
        select(DraftVersion.version, DraftVersion.author_id, DraftVersion.created_at, User.username)
        .join(User, User.id == DraftVersion.author_id, isouter=True)
        .where(DraftVersion.document_id == doc_id)
        .order_by(DraftVersion.version.desc())
    ).all()
    content = ""
    if vs:
        content = session.scalar(
            select(DraftVersion.content).where(
                DraftVersion.document_id == doc_id, DraftVersion.version == vs[0].version
            )
        )
    return {
        **svc.summary(session, d, doc, vs[0].version if vs else 0),
        "content": content,
        "versions": [
            {
                "version": v.version,
                "author": {"id": str(v.author_id), "username": v.username},
                "created_at": v.created_at,
            }
            for v in vs
        ],
    }


@router.get("/drafts/{doc_id}/versions/{n}")
def get_version(doc_id: UUID, n: Ver, p: Who, session: Db) -> dict:
    doc, _ = svc.readable_draft(session, p, doc_id)
    require_text(session, p, doc)
    v = session.scalar(
        select(DraftVersion).where(DraftVersion.document_id == doc_id, DraftVersion.version == n)
    )
    if v is None:
        raise HTTPException(404, "not found")
    return {
        "version": v.version,
        "content": v.content,
        "author": _author(session, v.author_id),
        "created_at": v.created_at,
    }


@router.get("/drafts/{doc_id}/checks")
def get_checks(doc_id: UUID, p: Who, session: Db) -> dict:
    """The caller's own stored reports for the latest version (never another co-author's)."""
    doc, _ = svc.readable_draft(session, p, doc_id)
    require_text(session, p, doc)  # stored reports quote the draft
    v = svc.latest_version(session, doc_id)

    def mine(rep: dict | None) -> dict | None:
        return rep if rep and rep.get("checked_by") == str(p.user_id) else None

    return {
        "version": v.version if v else 0,
        "validation": mine(v.validation) if v else None,
        "citations": mine(v.citations) if v else None,
    }


@router.put("/drafts/{doc_id}/content")
def save_content(doc_id: UUID, body: SaveIn, p: Who, session: Db, cfg: Cfg) -> dict:
    v = svc.save(session, p, doc_id, body.content, body.base_version, cfg.draft_max_bytes)
    return {"version": v.version, "created_at": v.created_at}


@router.put("/drafts/{doc_id}/coauthors")
def put_coauthors(doc_id: UUID, body: CoauthorsIn, p: Who, session: Db) -> dict:
    d = svc.set_coauthors(session, p, doc_id, body.users, body.groups)
    return {"coauthors": d.coauthors}


@router.delete("/drafts/{doc_id}", status_code=204)
def delete_draft(doc_id: UUID, p: Who, session: Db) -> None:
    svc.delete_draft(session, p, doc_id)


@router.post("/drafts/{doc_id}/validate")
def validate_draft(
    doc_id: UUID, p: Who, session: Db, cfg: Cfg, llm: Annotated[object, Depends(get_llm)]
) -> dict:
    return validator.validate(session, llm, cfg, p, doc_id)
