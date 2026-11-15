"""Ingest handler: runs the stages after the persisted one (resumable, at-least-once).

All writes to document_versions/documents use ingest.state short sessions (R9); `session` is
read-only for those tables. chunk -> embed -> index re-run as one unit (R7).
"""

import json
import logging
from uuid import UUID

from elasticsearch import Elasticsearch
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from roracle import search_index
from roracle.acl_sync import read_acl
from roracle.auth import principal_for_user
from roracle.config import Settings
from roracle.ingest.chunking import chunk
from roracle.ingest.embed import doc_prompt, embed, from_bytes, prompt_sha, to_bytes
from roracle.ingest.neardup import find_near_duplicate, minhash
from roracle.ingest.office import is_office, rendition_key, to_pdf
from roracle.ingest.parse import convert, document_text, grobid_metadata
from roracle.ingest.scan import scan
from roracle.ingest.state import (
    bump_attempts,
    clear_outdated_reasons,
    mark_searchable_and_enqueue,
    set_stage,
    update_document,
    update_version,
)
from roracle.ingest.summary import summarize
from roracle.models import Document, DocumentVersion, Tag
from roracle.queue import NonRetryable
from roracle.storage import BlobStore

log = logging.getLogger(__name__)

MAX_STARTS = 5
TERMINAL = {"searchable", "review", "needs_title", "rejected", "cancelled"}


def docling_key(version_id: UUID) -> str:
    return f"derived/{version_id}/docling.json"


def _clean(t: str | None) -> str | None:
    return " ".join(t.split())[:500] if t and t.strip() else None


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
            if sig.startswith("Heuristics.Limits.Exceeded"):
                raise NonRetryable("file exceeds the malware scanner limits")
            raise NonRetryable(f"malware detected: {sig}")
    set_stage(sf, v.id, "scanned")


def _parse(v: DocumentVersion, settings: Settings, store: BlobStore, sf) -> dict:
    data = store.open(v.blob_sha256).read()
    name = v.filename
    is_pdf = name.lower().endswith(".pdf") or v.content_type == "application/pdf"
    if settings.gotenberg_url and is_office(name):
        data = to_pdf(
            data,
            name,
            settings.gotenberg_url,
            settings.max_rendition_bytes,
            timeout=settings.gotenberg_timeout_s,
        )
        store.put_bytes(rendition_key(v.id), data, "application/pdf")
        name, is_pdf = f"{name.rsplit('.', 1)[0]}.pdf", True
    doc = convert(data, name, settings.docling_url, timeout=settings.docling_timeout_s)
    store.put_bytes(docling_key(v.id), json.dumps(doc).encode(), "application/json")
    meta, refs = {}, []
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
        session,
        principal_for_user(session, d.owner_id),
        v,
        settings.near_dup_threshold,
        settings.near_dup_summary_threshold,
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
    prompts = [doc_prompt(title, c.contextual, v.summary) for c in chunks]
    shas = [prompt_sha(p) for p in prompts]
    search_index.ensure_index(es, settings.embed_model, settings.embed_dims)
    cache = search_index.cached_vectors(es, settings.embed_model, d.id, shas, settings.embed_dims)
    miss = [p for p, h in zip(prompts, shas, strict=True) if h not in cache]
    fresh_vecs = iter(embed(llm, settings.embed_model, miss, settings.embed_dims) if miss else [])
    vectors = [cache[h] if h in cache else next(fresh_vecs) for h in shas]
    set_stage(sf, v.id, "embedded")
    acl = search_index.acl_fields(session, d)
    session.refresh(v)
    newest = session.scalar(
        select(func.max(DocumentVersion.version)).where(DocumentVersion.document_id == d.id)
    )
    tags = list(session.scalars(select(Tag.tag).where(Tag.document_id == d.id).order_by(Tag.tag)))
    latest = v.version >= (newest or 0)  # a newer version may have finished first: never demote it
    sv = from_bytes(v.summary_embedding).tolist() if v.summary_embedding else None
    if (sv is None or len(sv) != settings.embed_dims) and (v.summary or "").strip():
        sv = embed(llm, settings.embed_model, [doc_prompt(title, v.summary)], settings.embed_dims)[0]
        update_version(sf, v.id, summary_embedding=to_bytes(sv))  # lazy backfill (R9)
    elif sv is not None and len(sv) != settings.embed_dims:
        sv = None
    search_index.index_chunks(
        es, v, d, chunks, vectors, acl, settings.embed_model, title=title, is_latest=latest, tags=tags,
        summary_vector=sv, prompt_shas=shas,
    )  # fmt: skip
    # The handler session may have read the ACL minutes ago: if a PATCH landed meanwhile its
    # update_by_query (ACL or tags) may have run before these chunks existed. Re-read, converge.
    fresh = read_acl(sf, d.id)
    if fresh is not None and fresh[0] != {**acl, "tags": tags}:
        search_index.sync_acl(es, d.id, lambda: read_acl(sf, d.id))
    search_index.delete_stale_chunks(es, v.id, len(chunks))
    if latest:
        search_index.mark_previous_versions(es, d.id, v.version)
        if v.version > 1:
            clear_outdated_reasons(sf, d.id, v.id)
    # R6 + demotion: stage, citations job and the is_latest flip commit atomically
    mark_searchable_and_enqueue(sf, v.id, demote_doc=d.id if latest else None)


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
    if v.stage in TERMINAL or d.deleted_at is not None:
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
        title = d.title or _clean((v.meta or {}).get("title")) or docling_title(doc)
        if not title:
            set_stage(sf, vid, "needs_title")
            return
        if title != d.title:
            update_document(sf, d.id, title=title)
            session.refresh(d)
        text = document_text(doc)
        summary = summarize(llm, settings.summary_model, title, text)
        vec = (
            embed(llm, settings.embed_model, [doc_prompt(title, summary)], settings.embed_dims)[0]
            if summary.strip()
            else None
        )
        update_version(
            sf, vid, summary=summary, summary_embedding=to_bytes(vec) if vec else None, stage="summarized"
        )
        session.refresh(v)
    if v.stage == "summarized":
        if _dedupe(session, v, d, document_text(doc), settings, sf):
            return
        session.refresh(v)
    _index(session, v, d, doc, settings, es, llm, sf)
