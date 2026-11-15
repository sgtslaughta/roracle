"""Ingest handler: runs the stages after the persisted one (resumable, at-least-once).

All writes to document_versions/documents use ingest.state short sessions (R9); `session` is
read-only for those tables. chunk -> embed -> index re-run as one unit (R7).
"""

import json
import logging
from uuid import UUID

from elasticsearch import Elasticsearch
from sqlalchemy.orm import Session, sessionmaker

from roracle import search_index
from roracle.auth import principal_for_user
from roracle.config import Settings
from roracle.ingest.chunking import chunk
from roracle.ingest.embed import doc_prompt, embed
from roracle.ingest.neardup import find_near_duplicate, minhash
from roracle.ingest.parse import convert, document_text, grobid_metadata
from roracle.ingest.scan import scan
from roracle.ingest.state import (
    bump_attempts,
    demote_older_versions,
    set_stage,
    update_document,
    update_version,
)
from roracle.ingest.summary import summarize
from roracle.models import Document, DocumentVersion
from roracle.queue import NonRetryable
from roracle.storage import BlobStore

log = logging.getLogger(__name__)

MAX_STARTS = 5
TERMINAL = {"searchable", "review", "needs_title", "rejected", "cancelled"}


def docling_key(version_id: UUID) -> str:
    return f"derived/{version_id}/docling.json"


def docling_title(doc: dict) -> str | None:
    items = [t for t in doc.get("texts") or [] if isinstance(t, dict) and (t.get("text") or "").strip()]
    for labels in (("title",), ("section_header",)):
        for t in items:
            if t.get("label") in labels:
                return " ".join(t["text"].split())[:500]
    return None


def _scan(session: Session, v: DocumentVersion, settings: Settings, store: BlobStore, sf) -> None:
    if settings.clamav_host:
        sig = scan(store.open(v.blob_sha256), settings.clamav_host)
        if sig:
            set_stage(sf, v.id, "rejected")
            raise NonRetryable(f"malware detected: {sig}")
    set_stage(sf, v.id, "scanned")


def _parse(v: DocumentVersion, settings: Settings, store: BlobStore, sf) -> dict:
    data = store.open(v.blob_sha256).read()
    doc = convert(data, v.filename, settings.docling_url, timeout=settings.docling_timeout_s)
    store.put_bytes(docling_key(v.id), json.dumps(doc).encode(), "application/json")
    meta, refs = {}, []
    is_pdf = v.filename.lower().endswith(".pdf") or v.content_type == "application/pdf"
    if is_pdf and settings.grobid_url:
        try:
            meta, refs = grobid_metadata(data, settings.grobid_url, timeout=settings.grobid_timeout_s)
        except Exception:
            log.warning("grobid failed for version %s; continuing without metadata", v.id, exc_info=True)
    update_version(
        sf, v.id, page_count=len(doc.get("pages") or {}) or None, meta=meta, references=refs, stage="parsed"
    )
    return doc


def _dedupe(session: Session, v: DocumentVersion, d: Document, text: str, settings: Settings, sf) -> bool:
    """True if the version was parked for a near-dup decision."""
    update_version(sf, v.id, minhash=minhash(text))
    session.refresh(v)
    match = find_near_duplicate(
        session, principal_for_user(session, d.owner_id), v, settings.near_dup_threshold
    )
    if match is not None:
        update_version(sf, v.id, near_dup_of=match.id, stage="review")
        return True
    set_stage(sf, v.id, "deduped")
    return False


def _index(
    session: Session, v: DocumentVersion, d: Document, doc: dict, settings: Settings, es, llm, sf
) -> None:
    title = d.title or "none"
    chunks = chunk(doc, title, settings.embed_tokenizer)
    if not chunks:
        raise NonRetryable("no text extracted")
    set_stage(sf, v.id, "chunked")
    vectors = embed(
        llm, settings.embed_model, [doc_prompt(title, c.contextual) for c in chunks], settings.embed_dims
    )
    set_stage(sf, v.id, "embedded")
    search_index.ensure_index(es, settings.embed_model, settings.embed_dims)
    acl = search_index.acl_fields(session, d)
    search_index.index_chunks(es, v, d, chunks, vectors, acl, settings.embed_model, title=title)
    search_index.delete_stale_chunks(es, v.id, len(chunks))
    search_index.mark_previous_versions(es, d.id, v.id)
    demote_older_versions(sf, d.id, v.id)
    set_stage(sf, v.id, "searchable")


def handle_ingest(
    session: Session,
    payload: dict,
    *,
    settings: Settings,
    store: BlobStore,
    es: Elasticsearch,
    llm,
    session_factory: sessionmaker[Session],
) -> None:
    vid = UUID(payload["version_id"])
    sf = session_factory
    if bump_attempts(sf, vid) > MAX_STARTS:
        raise NonRetryable("ingest crashed repeatedly")
    v = session.get(DocumentVersion, vid)
    if v is None:
        raise NonRetryable("document version not found")
    session.refresh(v)
    d = session.get(Document, v.document_id)
    session.refresh(d)
    if v.stage in TERMINAL:
        return

    doc: dict | None = None
    if v.stage == "uploaded":
        _scan(session, v, settings, store, sf)
    if v.stage in ("uploaded", "scanned"):
        doc = _parse(v, settings, store, sf)
    session.refresh(v)
    if doc is None:
        doc = json.loads(store.get_bytes(docling_key(vid)))

    if v.stage == "parsed":
        title = d.title or (v.meta or {}).get("title") or docling_title(doc)
        if not title:
            set_stage(sf, vid, "needs_title")
            return
        if title != d.title:
            update_document(sf, d.id, title=title)
            session.refresh(d)
        text = document_text(doc)
        update_version(
            sf, vid, summary=summarize(llm, settings.summary_model, title, text), stage="summarized"
        )
        session.refresh(v)
    if v.stage == "summarized":
        if _dedupe(session, v, d, document_text(doc), settings, sf):
            return
        session.refresh(v)
    _index(session, v, d, doc, settings, es, llm, sf)
