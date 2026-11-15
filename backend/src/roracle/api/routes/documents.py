from pathlib import Path
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from roracle.access import normalize_path, visible_documents
from roracle.auth import Principal, current_principal, get_app_settings
from roracle.config import Settings
from roracle.db import get_session
from roracle.models import Blob, Document, DocumentVersion
from roracle.queue import enqueue
from roracle.storage import BlobStore, EmptyFile, TooLarge, blob_store_from, stage

router = APIRouter()


def get_blob_store(request: Request) -> BlobStore:
    app = request.app
    if not hasattr(app.state, "blob_store"):
        app.state.blob_store = blob_store_from(app.state.settings)
    return app.state.blob_store


def _org_unit(p: Principal) -> str:
    if not p.groups:
        return "/"
    # A malformed token group ("Math") is rooted ("/Math"), never widened to "/" (fail closed).
    g = p.groups[0].strip()
    return normalize_path(g if g.startswith("/") else f"/{g}")


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
    try:
        staged = stage(file.file, settings.max_upload_bytes)
    except EmptyFile:
        raise HTTPException(400, "empty file") from None
    except TooLarge:
        raise HTTPException(
            413, f"file exceeds {settings.max_upload_bytes // (1024 * 1024)} MB limit"
        ) from None
    try:
        if not force:
            dup = session.scalar(
                select(DocumentVersion.document_id)
                .join(Document, Document.id == DocumentVersion.document_id)
                .where(DocumentVersion.blob_sha256 == staged.sha256, visible_documents(p))
                .limit(1)
            )
            if dup is not None:
                raise HTTPException(409, {"code": "duplicate", "document_id": str(dup)})
        filename = file.filename or "upload"
        content_type = file.content_type or "application/octet-stream"
        # ponytail: object written before DB commit can orphan on DB failure; a P2 GC job deletes blob objects without a blobs row
        store.put(staged, content_type)
        session.execute(
            insert(Blob)
            .values(sha256=staged.sha256, size=staged.size)
            .on_conflict_do_update(index_elements=[Blob.sha256], set_={"refcount": Blob.refcount + 1})
        )
        doc = Document(
            owner_id=p.user_id,
            title=title or None,
            org_unit=_org_unit(p),
            visibility=visibility,
        )
        session.add(doc)
        session.flush()
        version = DocumentVersion(
            document_id=doc.id,
            version=1,
            blob_sha256=staged.sha256,
            filename=filename,
            content_type=content_type,
            size=staged.size,
        )
        session.add(version)
        session.flush()
        enqueue(session, "ingest", {"version_id": str(version.id)})
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
