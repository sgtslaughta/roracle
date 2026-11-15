from pathlib import Path
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from roracle.access import org_units, visible_documents
from roracle.auth import Principal, current_principal, get_app_settings
from roracle.config import Settings
from roracle.db import get_session
from roracle.ingest.intake import add_document
from roracle.models import Document, DocumentVersion
from roracle.queue import enqueue
from roracle.storage import BlobStore, EmptyFile, TooLarge, blob_store_from, stage

router = APIRouter()


def get_blob_store(request: Request) -> BlobStore:
    app = request.app
    if not hasattr(app.state, "blob_store"):
        app.state.blob_store = blob_store_from(app.state.settings)
    return app.state.blob_store


@router.post("/documents", status_code=201)
def upload(
    p: Annotated[Principal, Depends(current_principal)],
    session: Annotated[Session, Depends(get_session)],
    store: Annotated[BlobStore, Depends(get_blob_store)],
    settings: Annotated[Settings, Depends(get_app_settings)],
    file: Annotated[UploadFile, File()],
    title: Annotated[str | None, Form(max_length=500)] = None,
    visibility: Annotated[Literal["private", "shared", "org_unit", "everyone"], Form()] = "everyone",
    force: Annotated[bool, Form()] = False,
) -> dict:
    units = org_units(list(p.groups))
    if visibility == "org_unit" and not units:
        raise HTTPException(422, "cannot determine org unit; choose another visibility")
    try:
        staged = stage(file.file, settings.max_upload_bytes)
    except EmptyFile:
        raise HTTPException(400, "empty file") from None
    except TooLarge:
        raise HTTPException(
            413, f"file exceeds {settings.max_upload_bytes // (1024 * 1024)} MB limit"
        ) from None
    try:
        if settings.upload_quota_bytes > 0:
            # serialize per user until commit so concurrent uploads cannot overshoot the quota
            session.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:u, 0))"), {"u": str(p.user_id)}
            )
            used = session.scalar(
                select(func.coalesce(func.sum(DocumentVersion.size), 0))
                .join(Document, Document.id == DocumentVersion.document_id)
                .where(
                    Document.owner_id == p.user_id, DocumentVersion.stage.not_in(("rejected", "cancelled"))
                )
            )
            if used + staged.size > settings.upload_quota_bytes:
                raise HTTPException(413, "upload quota exceeded")
        if not force:
            dup = session.scalar(
                select(DocumentVersion.document_id)
                .join(Document, Document.id == DocumentVersion.document_id)
                .where(DocumentVersion.blob_sha256 == staged.sha256, visible_documents(p))
                .limit(1)
            )
            if dup is not None:
                raise HTTPException(409, {"code": "duplicate", "document_id": str(dup)})
        doc, version = add_document(
            session,
            store,
            staged,
            owner_id=p.user_id,
            org_unit=units[0] if units else "",  # "" matches no ancestor (fail closed)
            visibility=visibility,
            filename=file.filename or "upload",
            content_type=file.content_type or "application/octet-stream",
            title=title or None,
        )
        session.commit()
        return {"document_id": str(doc.id), "version_id": str(version.id), "sha256": staged.sha256}
    finally:
        Path(staged.path).unlink(missing_ok=True)


class TitleBody(BaseModel):
    title: Annotated[str, Field(min_length=1, max_length=500)]
    model_config = {"str_strip_whitespace": True}


@router.put("/documents/{doc_id}/versions/{version_id}/title")
def set_title(
    doc_id: UUID,
    version_id: UUID,
    body: TitleBody,
    p: Annotated[Principal, Depends(current_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> dict:
    row = session.execute(
        select(DocumentVersion, Document)
        .join(Document, Document.id == DocumentVersion.document_id)
        .where(DocumentVersion.id == version_id, Document.id == doc_id, visible_documents(p))
        .with_for_update(of=DocumentVersion)
    ).first()
    if row is None:
        raise HTTPException(404, "not found")
    v, doc = row
    if not (doc.owner_id == p.user_id or p.is_admin):
        raise HTTPException(403, "only the document owner can set the title")
    if v.stage != "needs_title":
        raise HTTPException(409, "not awaiting a title")
    doc.title = body.title
    v.stage = "parsed"
    v.ingest_attempts = 0  # R17: a user decision restarts the crash budget
    enqueue(session, "ingest", {"version_id": str(v.id)})
    session.commit()
    return {"version_id": str(v.id), "document_id": str(doc.id), "stage": v.stage}
