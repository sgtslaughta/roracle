"""Document detail, original-file download and compact docling (reader) endpoints."""

import json
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
from roracle.api.routes.library import auto_tags_of, get_es, tags_of, vote_summary
from roracle.auth import Principal, current_principal
from roracle.db import get_session
from roracle.identifiers import public_ids
from roracle.ingest.office import rendition_key
from roracle.labels.policy import Policy, audit_inline_view, label_json, require_exportable
from roracle.labels.textgate import require_text
from roracle.models import Document, DocumentCoauthor, DocumentShare, DocumentVersion, User
from roracle.models_writing import Draft
from roracle.search_index import ALIAS, file_type
from roracle.storage import BlobStore

router = APIRouter()
Who = Annotated[Principal, Depends(current_principal)]
Session_ = Annotated[Session, Depends(get_session)]
HIDDEN_STAGES = {"rejected", "cancelled"}  # R5
MAX_CHUNKS = 10_000  # ES search size cap: chunks beyond this get no passage box in the reader


def visible_doc(session: Session, p: Principal, doc_id: UUID) -> Document:
    doc = session.scalar(select(Document).where(Document.id == doc_id, visible_documents(p)))
    if doc is None:
        raise HTTPException(404, "not found")
    return doc


def privileged(doc: Document, p: Principal) -> bool:
    return p.is_admin or doc.owner_id == p.user_id


def _missing(e: ClientError) -> bool:
    return e.response.get("Error", {}).get("Code") in ("NoSuchKey", "404", "NotFound")


def _version(session: Session, p: Principal, doc_id: UUID, n: int) -> DocumentVersion:
    """Readable version: visible doc, version exists, not rejected/cancelled (R5), and for
    non-owners/non-admins only once searchable (R16)."""
    doc = visible_doc(session, p, doc_id)
    v = session.scalar(
        select(DocumentVersion).where(DocumentVersion.document_id == doc_id, DocumentVersion.version == n)
    )
    if v is None or v.stage in HIDDEN_STAGES or (not privileged(doc, p) and v.stage != "searchable"):
        raise HTTPException(404, "not found")
    return v


def _gate(session: Session, p: Principal, doc_id: UUID, route: str, *, download: bool = True) -> None:
    """Agents get no raw text of `exclude_llm` docs. Gate (c) blocks attachment downloads only: inline
    viewing (reader) stays allowed. 404 came first, so naming the label leaks nothing."""
    doc = visible_doc(session, p, doc_id)
    require_text(session, p, doc)
    if download or p.is_agent:  # inline viewing is the human reader only; agents always download
        require_exportable(session, p, doc, route)


def _sharing(session: Session, doc: Document) -> dict:
    """Owner/admin only: who the document is shared with and its published co-authors."""
    rows = session.execute(
        select(DocumentShare.principal_kind, DocumentShare.principal).where(
            DocumentShare.document_id == doc.id
        )
    ).all()
    uids = sorted(p for k, p in rows if k == "user")
    cos = sorted(
        str(u)
        for u in session.scalars(
            select(DocumentCoauthor.user_id).where(DocumentCoauthor.document_id == doc.id)
        )
    )
    ids = [UUID(i) for i in {*uids, *cos}]
    names = {u: n for u, n in session.execute(select(User.id, User.username).where(User.id.in_(ids)))}

    def who(i: str) -> dict:
        return {"id": i, "username": names.get(UUID(i), "")}

    return {
        "share_users": [who(i) for i in uids],
        "share_groups": sorted(p for k, p in rows if k == "group"),
        "coauthors": [who(i) for i in cos],
    }


@router.get("/documents/{doc_id}")
def detail(doc_id: UUID, p: Who, session: Session_) -> dict:
    doc = visible_doc(session, p, doc_id)
    versions = list(
        session.scalars(
            select(DocumentVersion)
            .where(DocumentVersion.document_id == doc_id)
            .order_by(DocumentVersion.version.desc())
        )
    )
    if not privileged(doc, p):
        versions = [v for v in versions if v.stage == "searchable"]
        if not versions:
            raise HTTPException(404, "not found")
    # a rejected/in-ingest newer version may also carry the flag: prefer the newest searchable one
    latest = next((v for v in versions if v.is_latest and v.stage == "searchable"), None) or next(
        (v for v in versions if v.is_latest), versions[0] if versions else None
    )
    meta = (latest.meta if latest else None) or {}
    owner = session.get(User, doc.owner_id)
    draft = session.get(Draft, doc_id)
    lab = Policy.load(session).get(doc.label_id)
    hide = p.is_agent and lab.exclude_llm  # agents: metadata yes, text no
    return {
        **(_sharing(session, doc) if privileged(doc, p) else {}),
        "id": str(doc.id),
        "title": doc.title or meta.get("title") or (latest.filename if latest else ""),
        "authors": list(meta.get("authors") or []),
        "year": meta.get("year"),
        "venue": meta.get("venue"),
        "ids": public_ids(meta),
        "org_unit": doc.org_unit,
        "visibility": doc.visibility,
        "label": label_json(Policy.load(session).get(doc.label_id)),
        "owner": {"id": str(doc.owner_id), "username": owner.username if owner else ""},
        "summary": None if hide else (latest.summary if latest else None),
        "abstract": None if hide or not isinstance(meta.get("abstract"), str) else meta["abstract"],
        "added_at": doc.created_at.isoformat() if doc.created_at else None,
        "references_count": len(latest.references or []) if latest else 0,
        "withheld": lab.name if hide else None,
        "stage": latest.stage if latest else "uploaded",
        "tags": tags_of(session, doc_id, user_only=hide),  # agents: no LLM tags of exclude_llm docs
        "auto_tags": [] if hide else auto_tags_of(session, doc_id),  # subset of `tags` suggested by the LLM
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
        "ai_disclosure": draft.ai_disclosure if draft else None,
    }


def _disposition(kind: str, filename: str) -> str:
    fallback = re.sub(r'["\\]', "", filename.encode("ascii", "ignore").decode())
    fallback = "".join(c for c in fallback if c.isprintable()).strip() or "download"
    return f"{kind}; filename=\"{fallback}\"; filename*=UTF-8''{quote(filename, safe='')}"


_TYPES = {
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "doc": "application/msword",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "md": "text/markdown",
    "txt": "text/plain",
}


_INLINE_TEXT = {"md", "txt"}
# reader <img>: extension -> (content type, magic prefix); only when the bytes really are that image
_INLINE_IMAGE = {
    "png": ("image/png", b"\x89PNG"), "jpg": ("image/jpeg", b"\xff\xd8\xff"), "jpeg": ("image/jpeg", b"\xff\xd8\xff"),
    "webp": ("image/webp", b"RIFF"), "gif": ("image/gif", b"GIF8"), "bmp": ("image/bmp", b"BM"),
}  # fmt: skip


def _stream(body, head: bytes):
    try:
        yield head
        yield from body.iter_chunks(1 << 16)
    finally:
        body.close()


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
    _gate(session, p, doc_id, "document.file", download=False)
    try:
        body = store.open(v.blob_sha256)
    except ClientError as e:
        if _missing(e):
            raise HTTPException(404, "not found") from None
        raise
    # Never trust the client-supplied Content-Type: PDFs are sniffed, everything else is an attachment.
    head = body.read(5)
    ext = file_type(v.filename)
    stored_pdf = (v.content_type or "").split(";")[0].strip().lower() == "application/pdf"
    is_pdf = head == b"%PDF-" and (stored_pdf or ext == "pdf")
    ctype = "application/pdf" if is_pdf else _TYPES.get(ext, "application/octet-stream")
    as_text = inline and p.via == "jwt" and ext in _INLINE_TEXT  # reader view; agents always download
    img = _INLINE_IMAGE.get(ext) if inline and p.via == "jwt" else None
    as_image = img is not None and head.startswith(img[1])
    kind = "inline" if inline and (is_pdf and stored_pdf or as_text or as_image) else "attachment"
    if kind == "inline" and as_text:
        ctype = "text/plain; charset=utf-8"  # never rendered as markup
    if kind == "inline" and as_image and img:
        ctype = img[0]
    if kind == "inline":
        audit_inline_view(session, p, visible_doc(session, p, doc_id), "document.file")
    if kind == "attachment":  # gate (c): only downloads are blocked, not in-app viewing
        try:
            _gate(session, p, doc_id, "document.file")
        except BaseException:
            body.close()
            raise
    return StreamingResponse(
        _stream(body, head),
        media_type=ctype,
        headers={
            "Content-Disposition": _disposition(kind, v.filename),
            "Content-Length": str(v.size),
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "sandbox; default-src 'none'",
        },
    )


def _open_pdf(store: BlobStore, v: DocumentVersion):
    """(body, head, size) of the rendition, else of the original if it is a real PDF; 404 otherwise.
    Closes the body on every path that does not return it."""
    original = False
    try:
        body, size = store.open_key(rendition_key(v.id))
    except ClientError as e:
        if not _missing(e):
            raise
        original = True
    if original:
        try:
            body, size = store.open_key(store.key(v.blob_sha256))
        except ClientError as e:
            if _missing(e):
                raise HTTPException(404, "not found") from None
            raise
    try:
        head = body.read(5)
        stored_pdf = (v.content_type or "").split(";")[0].strip().lower() == "application/pdf"
        if head != b"%PDF-" or (original and not (stored_pdf or file_type(v.filename) == "pdf")):
            raise HTTPException(404, "not found")
    except BaseException:
        body.close()
        raise
    return body, head, size


@router.get("/documents/{doc_id}/versions/{n}/pdf")
def pdf(
    doc_id: UUID,
    n: Annotated[int, Path(le=2**31 - 1)],
    p: Who,
    session: Session_,
    store: Annotated[BlobStore, Depends(get_blob_store)],
) -> StreamingResponse:
    v = _version(session, p, doc_id, n)
    _gate(session, p, doc_id, "document.pdf", download=False)
    audit_inline_view(session, p, visible_doc(session, p, doc_id), "document.pdf")
    body, head, size = _open_pdf(store, v)
    stem = v.filename.rsplit(".", 1)[0] if "." in v.filename else v.filename
    return StreamingResponse(
        _stream(body, head),
        media_type="application/pdf",
        headers={
            "Content-Disposition": _disposition("inline", f"{stem}.pdf"),
            "Content-Length": str(size),
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "sandbox; default-src 'none'",
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
    _gate(session, p, doc_id, "document.docling", download=False)
    audit_inline_view(session, p, visible_doc(session, p, doc_id), "document.docling")
    try:
        raw = json.loads(store.get_bytes(f"derived/{v.id}/docling.json"))
    except ClientError as e:
        if _missing(e):
            raise HTTPException(404, "not found") from None  # not parsed yet
        raise
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
            query={
                "bool": {
                    "filter": [{"term": {"version_id": str(v.id)}}],
                    "must_not": [{"exists": {"field": "unit"}}],  # units must not eat the cap
                }
            },
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
