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

from roracle import notify, runtime, search_index
from roracle.acl_sync import read_acl
from roracle.auth import principal_for_user
from roracle.config import Settings
from roracle.ingest.chunking import chunk
from roracle.ingest.context import notes_for
from roracle.ingest.embed import chunk_prompt, doc_prompt, embed, from_bytes, prompt_sha, to_bytes
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
from roracle.labels.policy import Policy
from roracle.metrics import INGEST_STAGE_FAILURES
from roracle.models import Document, DocumentVersion, Tag
from roracle.queue import NonRetryable, payload_uuid
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
        sig = scan(store.open(v.blob_sha256), settings.clamav_host, port=settings.clamav_port)
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
    settings = runtime.effective(session, settings)
    match = find_near_duplicate(
        session,
        principal_for_user(session, d.owner_id),
        v,
        settings.near_dup_threshold,
        settings.near_dup_summary_threshold,
    )
    if match is not None:
        update_version(sf, v.id, near_dup_of=match.id, stage="review")
        if d.connector_id is None:
            notify.emit(
                session,
                d.owner_id,
                "upload.near_dup",
                {
                    "document_id": str(d.id),
                    "version_id": str(v.id),
                    "filename": v.filename,
                    "match_document_id": str(match.document_id),
                },
            )
        return True
    set_stage(sf, v.id, "deduped")
    return False


def _index(
    session: Session, v: DocumentVersion, d: Document, doc: dict, settings: Settings, es, llm, sf,
    store=None, *, into: str | None = None, rebuild: bool = False,
) -> None:  # fmt: skip
    """`into`: write another index (shadow / new generation) instead of the active one. `rebuild`: the
    version is already searchable, so no stage moves and no citations job."""
    title = d.title or "none"
    chunks = chunk(doc, title, settings.embed_tokenizer)
    if not chunks:
        raise NonRetryable("no text extracted")
    if not rebuild:
        set_stage(sf, v.id, "chunked")
    notes = None
    if settings.context_notes and settings.context_model:
        notes = notes_for(session, llm, settings, store, v, d, document_text(doc), chunks)
    prompts = [
        chunk_prompt(title, c.contextual, v.summary, notes[i] if notes else None)
        for i, c in enumerate(chunks)
    ]
    shas = [prompt_sha(p) for p in prompts]
    index = into or search_index.active_index(settings)
    cache = search_index.cached_vectors(es, settings.embed_model, d.id, shas, settings.embed_dims, index)
    miss = [p for p, h in zip(prompts, shas, strict=True) if h not in cache]
    fresh_vecs = iter(embed(llm, settings.embed_model, miss, settings.embed_dims) if miss else [])
    vectors = [cache[h] if h in cache else next(fresh_vecs) for h in shas]
    if not rebuild:
        set_stage(sf, v.id, "embedded")
    acl = search_index.acl_fields(session, d)
    session.refresh(v)
    newest = session.scalar(
        select(func.max(DocumentVersion.version)).where(
            DocumentVersion.document_id == d.id, DocumentVersion.stage == "searchable"
        )
    )
    tags = list(session.scalars(select(Tag.tag).where(Tag.document_id == d.id).order_by(Tag.tag)))
    latest = v.version >= (newest or 0)  # a newer version may have finished first: never demote it
    sv = from_bytes(v.summary_embedding).tolist() if v.summary_embedding else None
    backfill = False
    runtime.assert_embedding_unchanged(
        session, settings
    )  # before any write: a cutover cleared summary vectors
    if (sv is None or len(sv) != settings.embed_dims) and (v.summary or "").strip():
        sv = embed(llm, settings.embed_model, [doc_prompt(title, v.summary)], settings.embed_dims)[0]
        backfill = True
    elif sv is not None and len(sv) != settings.embed_dims:
        sv = None
    runtime.assert_embedding_unchanged(session, settings)  # cutover during the backfill embed: stale
    if backfill:  # persisted only after the guard: a retry never reuses an old-model vector (R2-m1)
        update_version(sf, v.id, summary_embedding=to_bytes(sv))  # lazy backfill (R9)
    search_index.ensure_index(
        es, settings.embed_model, settings.embed_dims, settings.read_alias, index,
        session_factory=sf, settings=settings,
    )  # fmt: skip
    search_index.index_chunks(
        es, v, d, chunks, vectors, acl, settings.embed_model, title=title, is_latest=latest, tags=tags,
        summary_vector=sv, prompt_shas=shas, index=index, contexts=notes,
    )  # fmt: skip
    # The handler session may have read the ACL minutes ago: if a PATCH landed meanwhile its
    # update_by_query (ACL or tags) may have run before these chunks existed. Re-read, converge.
    fresh = read_acl(sf, d.id)
    if fresh is None:  # deleted while we indexed: DELETE's chunk purge may have run before our bulk
        es.delete_by_query(
            index=search_index.MUTATE_ALIAS,
            query={"term": {"doc_id": str(d.id)}},
            refresh=True,
            conflicts="proceed",
        )
        return
    if fresh[0] != {**acl, "tags": tags}:
        search_index.sync_acl(es, d.id, lambda: read_acl(sf, d.id))
    search_index.delete_stale_chunks(es, v.id, len(chunks))
    if rebuild:
        return
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
    vid = payload_uuid(payload, "version_id")
    sf = session_factory
    settings = runtime.effective(session, settings)  # active model/index persisted by re-embed cutover (R4)
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
    try:
        _advance(session, v, d, settings=settings, store=store, es=es, llm=llm, sf=sf)
    except Exception:
        INGEST_STAGE_FAILURES.labels(v.stage).inc()  # stage being advanced (last refreshed value)
        raise


def _advance(session, v, d, *, settings, store, es, llm, sf) -> None:
    vid = v.id
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
        session.refresh(d, ["label_id"])  # a raise during a long parse must still be honoured
        llm_ok = not Policy.load(session).has(d.label_id, "exclude_llm")  # gate (a), label at ingest time
        summary = summarize(llm, settings.summary_model, title, text) if llm_ok else ""
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
    _index(session, v, d, doc, settings, es, llm, sf, store)
