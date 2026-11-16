"""Submit a draft and the admin review queue (P9 task 6)."""

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import AfterValidator, BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from roracle.access import group_path
from roracle.api.routes.admin_deps import Admin
from roracle.api.routes.documents import get_blob_store
from roracle.auth import Principal, current_principal, get_app_settings
from roracle.config import Settings
from roracle.db import get_session
from roracle.models import Document
from roracle.models_writing import Draft
from roracle.storage import BlobStore
from roracle.writing import drafts, submit

router = APIRouter()
Who = Annotated[Principal, Depends(current_principal)]
Db = Annotated[Session, Depends(get_session)]
Cfg = Annotated[Settings, Depends(get_app_settings)]
Store = Annotated[BlobStore, Depends(get_blob_store)]


class SubmitIn(BaseModel):
    visibility: Literal["private", "shared", "org_unit", "everyone"]
    share_users: Annotated[list[UUID], Field(max_length=100)] = []
    share_groups: Annotated[
        list[Annotated[str, Field(max_length=500), AfterValidator(group_path)]], Field(max_length=100)
    ] = []


class ReviewIn(BaseModel):
    decision: Literal["approve", "reject"]
    note: Annotated[str, Field(max_length=2000)] | None = None


@router.post("/drafts/{doc_id}/submit")
def submit_draft(doc_id: UUID, body: SubmitIn, p: Who, session: Db, cfg: Cfg, store: Store) -> dict:
    out = submit.submit(session, store, cfg, p, doc_id, body.model_dump())
    session.commit()
    return out


@router.get("/admin/drafts/reviews")
def reviews(p: Admin, session: Db) -> dict:
    rows = session.execute(
        select(Draft, Document)
        .join(Document, Document.id == Draft.document_id)
        .where(Draft.status == "in_review", Document.deleted_at.is_(None))
        .order_by(Draft.updated_at, Draft.document_id)
        .limit(200)
    ).all()
    return {"items": [drafts.summary(session, d, doc) for d, doc in rows]}


@router.post("/admin/drafts/{doc_id}/review")
def review_draft(doc_id: UUID, body: ReviewIn, p: Admin, session: Db, cfg: Cfg, store: Store) -> dict:
    out = submit.review(session, store, cfg, p, doc_id, body.decision, body.note)
    session.commit()
    return out
