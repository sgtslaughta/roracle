from pathlib import Path
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import false, func, or_, select, text, true
from sqlalchemy.orm import Session

from roracle.access import org_unit_error, org_units, readable_documents
from roracle.audit import audit
from roracle.auth import MANAGE, Principal, current_principal, get_app_settings, require_scope
from roracle.config import Settings
from roracle.db import get_session
from roracle.ingest.intake import add_document
from roracle.labels import service
from roracle.models import Document, DocumentVersion
from roracle.onboarding import mark_human
from roracle.queue import enqueue
from roracle.storage import BlobStore, EmptyFile, StagedBlob, StagedFile, TooLarge, blob_store_from, stage
from roracle.upload_stream import Received, multipart_body, receive

router = APIRouter()
HIDDEN = ("rejected", "cancelled")  # R5


def get_blob_store(request: Request) -> BlobStore:
    app = request.app
    if not hasattr(app.state, "blob_store"):
        app.state.blob_store = blob_store_from(app.state.settings)
    return app.state.blob_store


class UploadForm(BaseModel):
    title: Annotated[str | None, Field(max_length=500)] = None
    visibility: Literal["private", "shared", "org_unit", "everyone"] | None = (
        None  # None: "everyone", or "private" without manage
    )
    force: bool = False
    label_id: UUID | None = None


async def _received(
    request: Request,
    p: Annotated[Principal, Depends(current_principal)],
    session: Annotated[Session, Depends(get_session)],
    store: Annotated[BlobStore, Depends(get_blob_store)],
    settings: Annotated[Settings, Depends(get_app_settings)],
) -> Received:
    session.commit()  # release the user-upsert row lock / pooled tx before the (long) body stream
    try:
        return await receive(request, store, settings.max_upload_bytes)
    except TooLarge:
        raise HTTPException(
            413, f"file exceeds {settings.max_upload_bytes // (1024 * 1024)} MB limit"
        ) from None
    except EmptyFile:
        raise HTTPException(400, "empty file") from None


@router.post(
    "/documents",
    status_code=201,
    openapi_extra=multipart_body(
        {
            "file": {"type": "string", "format": "binary"},
            "title": {"type": "string", "maxLength": 500},
            "visibility": {
                "type": "string",
                "enum": ["private", "shared", "org_unit", "everyone"],
                "default": "everyone",
            },
            "force": {"type": "boolean", "default": False},
            "label_id": {"type": "string", "format": "uuid"},
        },
        ["file"],
    ),
)
def upload(
    p: Annotated[Principal, Depends(current_principal)],
    session: Annotated[Session, Depends(get_session)],
    store: Annotated[BlobStore, Depends(get_blob_store)],
    settings: Annotated[Settings, Depends(get_app_settings)],
    rec: Annotated[Received, Depends(_received)],
) -> dict:
    """Streams the multipart body to object storage; fields may come before or after `file`."""
    staged = rec.staged
    try:
        try:
            form = UploadForm.model_validate(rec.fields)
        except ValidationError as e:
            raise HTTPException(
                422, e.errors(include_url=False, include_context=False, include_input=False)
            ) from None
        if staged is None:
            raise HTTPException(422, "file is required")
        return accept(
            p, session, store, settings, staged, form,
            filename=staged.filename or "upload", content_type=staged.content_type,
        )  # fmt: skip
    finally:
        if staged is not None:
            staged.upload.abort()


def upload_file(
    p: Principal,
    session: Session,
    store: BlobStore,
    settings: Settings,
    file: UploadFile,
    form: UploadForm,
) -> dict:
    """Same as `upload` for an already-buffered file (MCP base64 uploads)."""
    try:
        staged = stage(file.file, settings.max_upload_bytes)
    except EmptyFile:
        raise HTTPException(400, "empty file") from None
    except TooLarge:
        raise HTTPException(
            413, f"file exceeds {settings.max_upload_bytes // (1024 * 1024)} MB limit"
        ) from None
    try:
        return accept(
            p, session, store, settings, staged, form,
            filename=file.filename or "upload",
            content_type=file.content_type or "application/octet-stream",
        )  # fmt: skip
    finally:
        Path(staged.path).unlink(missing_ok=True)


def accept(
    p: Principal,
    session: Session,
    store: BlobStore,
    settings: Settings,
    staged: StagedFile | StagedBlob,
    form: UploadForm,
    *,
    filename: str,
    content_type: str,
) -> dict:
    vis = form.visibility
    if vis is None:  # omitted: callers without manage get a private document
        vis = "everyone" if MANAGE in p.scopes else "private"
    elif vis != "private":  # asking for sharing at creation is a manage action
        require_scope(session, p, MANAGE)
    units = org_units(list(p.groups))
    if err := org_unit_error(vis, units[0] if units else ""):
        raise HTTPException(422, err)
    if settings.upload_quota_bytes > 0:
        # serialize per user until commit so concurrent uploads cannot overshoot the quota
        session.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:u, 0))"), {"u": str(p.user_id)})
        used = session.scalar(
            select(func.coalesce(func.sum(DocumentVersion.size), 0))
            .join(Document, Document.id == DocumentVersion.document_id)
            .where(
                Document.owner_id == p.user_id,
                Document.connector_id.is_(None),  # connector documents are exempt
                Document.deleted_at.is_(None),  # deleted papers free their quota
                DocumentVersion.stage.not_in(("rejected", "cancelled")),
            )
        )
        if used + staged.size > settings.upload_quota_bytes:
            raise HTTPException(413, "upload quota exceeded")
    if not form.force:
        dup = session.scalar(
            select(DocumentVersion.document_id)
            .join(Document, Document.id == DocumentVersion.document_id)
            .where(
                DocumentVersion.blob_sha256 == staged.sha256,
                DocumentVersion.stage.notin_(HIDDEN),
                readable_documents(p),
                # per matched version: non-owners may only learn of searchable ones (R16)
                or_(
                    true() if p.is_admin else false(),
                    Document.owner_id == p.user_id,
                    DocumentVersion.stage == "searchable",
                ),
            )
            .limit(1)
        )
        if dup is not None:
            raise HTTPException(409, {"code": "duplicate", "document_id": str(dup)})
    label_id = service.initial_label(session, form.label_id, p=p)
    doc, version = add_document(
        session,
        store,
        staged,
        owner_id=p.user_id,
        org_unit=units[0] if units else "",  # "" matches no ancestor (fail closed)
        visibility=vis,
        filename=filename,
        content_type=content_type,
        title=form.title or None,
        label_id=label_id,
    )
    audit(session, p, "document.upload", str(doc.id), {"version_id": str(version.id), "size": staged.size})
    mark_human(session, p, "upload")
    session.commit()
    return {"document_id": str(doc.id), "version_id": str(version.id), "sha256": staged.sha256}


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
        .where(DocumentVersion.id == version_id, Document.id == doc_id, readable_documents(p))
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
