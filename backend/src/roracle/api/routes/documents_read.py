"""Document detail, original-file download and compact docling (reader) endpoints."""

import json
import mimetypes
import re
from typing import Annotated
from urllib.parse import quote
from uuid import UUID

from botocore.exceptions import ClientError
from elasticsearch import Elasticsearch, NotFoundError
from fastapi import APIRouter, Depends, HTTPException, Path
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from roracle.access import visible_documents
from roracle.api.routes.documents import get_blob_store
from roracle.api.routes.library import _tags, get_es, vote_summary
from roracle.auth import Principal, current_principal
from roracle.db import get_session
from roracle.models import Document, DocumentVersion, User
from roracle.search_index import ALIAS, file_type
from roracle.storage import BlobStore

router = APIRouter()
Who = Annotated[Principal, Depends(current_principal)]
Session_ = Annotated[Session, Depends(get_session)]
HIDDEN_STAGES = {"rejected", "cancelled"}  # R5
MAX_CHUNKS = 10_000


def _doc(session: Session, p: Principal, doc_id: UUID) -> Document:
    doc = session.scalar(select(Document).where(Document.id == doc_id, visible_documents(p)))
    if doc is None:
        raise HTTPException(404, "not found")
    return doc


def _version(session: Session, p: Principal, doc_id: UUID, n: int) -> DocumentVersion:
    """Downloadable version: visible doc, version exists, not rejected/cancelled (R5)."""
    _doc(session, p, doc_id)
    v = session.scalar(
        select(DocumentVersion).where(DocumentVersion.document_id == doc_id, DocumentVersion.version == n)
    )
    if v is None or v.stage in HIDDEN_STAGES:
        raise HTTPException(404, "not found")
    return v


@router.get("/documents/{doc_id}")
def detail(doc_id: UUID, p: Who, session: Session_) -> dict:
    doc = _doc(session, p, doc_id)
    versions = list(
        session.scalars(
            select(DocumentVersion)
            .where(DocumentVersion.document_id == doc_id)
            .order_by(DocumentVersion.version.desc())
        )
    )
    latest = next((v for v in versions if v.is_latest), versions[0] if versions else None)
    meta = (latest.meta if latest else None) or {}
    owner = session.get(User, doc.owner_id)
    return {
        "id": str(doc.id),
        "title": doc.title or meta.get("title") or (latest.filename if latest else ""),
        "authors": list(meta.get("authors") or []),
        "year": meta.get("year"),
        "venue": meta.get("venue"),
        "org_unit": doc.org_unit,
        "visibility": doc.visibility,
        "owner": {"id": str(doc.owner_id), "username": owner.username if owner else ""},
        "summary": latest.summary if latest else None,
        "stage": latest.stage if latest else "uploaded",
        "tags": _tags(session, doc_id),
        "votes": vote_summary(session, doc_id, p.user_id),
        "file_type": file_type(latest.filename) if latest else "other",
        "versions": [
            {
                "id": str(v.id),
                "version": v.version,
                "filename": v.filename,
                "uploaded_at": v.created_at.isoformat(),
                "is_latest": v.is_latest,
            }
            for v in versions
            if v.stage not in HIDDEN_STAGES
        ],
        "page_count": (latest.page_count if latest else None) or 0,
        "flag": None,
    }


def _disposition(kind: str, filename: str) -> str:
    fallback = re.sub(r'["\\]', "", filename.encode("ascii", "ignore").decode())
    fallback = "".join(c for c in fallback if c.isprintable()).strip() or "download"
    return f"{kind}; filename=\"{fallback}\"; filename*=UTF-8''{quote(filename, safe='')}"


@router.get("/documents/{doc_id}/versions/{n}/file")
def download(
    doc_id: UUID,
    n: Annotated[int, Path(le=2**31 - 1)],
    p: Who,
    session: Session_,
    store: Annotated[BlobStore, Depends(get_blob_store)],
    inline: bool = False,
) -> StreamingResponse:
    v = _version(session, p, doc_id, n)
    ctype = v.content_type or mimetypes.guess_type(v.filename)[0] or "application/octet-stream"
    # inline only for PDFs: uploads are user-controlled, never render e.g. HTML in our origin
    kind = "inline" if inline and ctype.split(";")[0].strip().lower() == "application/pdf" else "attachment"
    try:
        body = store.open(v.blob_sha256)
    except ClientError:
        raise HTTPException(404, "not found") from None
    return StreamingResponse(
        body.iter_chunks(1 << 16),
        media_type=ctype,
        headers={
            "Content-Disposition": _disposition(kind, v.filename),
            "Content-Length": str(v.size),
            "X-Content-Type-Options": "nosniff",
        },
    )


def _chunk_n(chunk_id: str) -> int:
    tail = chunk_id.rpartition(":")[2]
    return int(tail) if tail.isdigit() else 0


@router.get("/documents/{doc_id}/versions/{n}/docling")
def docling(
    doc_id: UUID,
    n: Annotated[int, Path(le=2**31 - 1)],
    p: Who,
    session: Session_,
    store: Annotated[BlobStore, Depends(get_blob_store)],
    es: Annotated[Elasticsearch, Depends(get_es)],
) -> dict:
    v = _version(session, p, doc_id, n)
    try:
        raw = json.loads(store.get_bytes(f"derived/{v.id}/docling.json"))
    except ClientError:
        raise HTTPException(404, "not found") from None  # not parsed yet
    pages = {
        str(k): {"size": {"width": pg["size"]["width"], "height": pg["size"]["height"]}}
        for k, pg in (raw.get("pages") or {}).items()
        if isinstance(pg, dict) and pg.get("size")
    }
    try:
        hits = es.search(
            index=ALIAS,
            size=MAX_CHUNKS,
            source=["chunk_id", "bboxes"],
            query={"term": {"version_id": str(v.id)}},
        )["hits"]["hits"]
    except NotFoundError:
        hits = []
    chunks = []
    for h in hits:
        s = h["_source"]
        if s.get("bboxes"):
            first = s["bboxes"][0]
            chunks.append(
                {
                    "chunk_id": s["chunk_id"],
                    "page_no": first["page"],
                    "bbox": {k: first[k] for k in ("l", "t", "r", "b", "coord_origin")},
                }
            )
    chunks.sort(key=lambda c: _chunk_n(c["chunk_id"]))
    return {"pages": pages, "chunks": chunks}
