"""Ingest handler: runs the stages after the persisted one (resumable, at-least-once).

All writes to document_versions/documents use ingest.state short sessions (R9); `session` is
read-only for those tables. chunk -> embed -> index re-run as one unit (R7).
"""

import json
import logging
import time
from datetime import timedelta
from uuid import UUID

from elasticsearch import Elasticsearch
from sqlalchemy import func, select, update
from sqlalchemy import text as text_
from sqlalchemy.orm import Session, sessionmaker

from roracle import notify, runtime, search_index
from roracle.acl_sync import read_acl
from roracle.auth import principal_for_user
from roracle.config import Settings
from roracle.ingest.chunking import chunk
from roracle.ingest.context import notes_for
from roracle.ingest.embed import chunk_prompt, doc_prompt, embed, from_bytes, prompt_sha, to_bytes
from roracle.ingest.neardup import find_near_duplicate, find_sibling_duplicate, minhash, write_bands
from roracle.ingest.office import is_office, rendition_key, to_pdf
from roracle.ingest.parse import convert, document_text, grobid_metadata
from roracle.ingest.raptor import build_units
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
from roracle.queue import NonRetryable, check_lease, fence, payload_uuid
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


class DedupeBusy(Exception):
    """The owner's dedupe lock stayed busy beyond the bounded backoff: retry the job later (the lock is held for
    milliseconds, so this only happens when something is badly wrong)."""


LOCK_TRIES, LOCK_SLEEP_S = 40, 0.05  # ~ up to 40 tries with a growing sleep, a few seconds in total


def _owner_lock(ls: Session, owner_id) -> None:
    """Transaction-scoped (PgBouncer-safe) per-owner advisory lock, TRY + bounded backoff: a bulk import by one owner
    queues for milliseconds, never blocks a worker connection indefinitely."""
    key = {"k": f"dedupe:{owner_id}"}
    for i in range(LOCK_TRIES):
        if ls.execute(text_("SELECT pg_try_advisory_xact_lock(hashtextextended(:k, 0))"), key).scalar():
            return
        time.sleep(min(LOCK_SLEEP_S * (1 + i / 4), 0.5))
    raise DedupeBusy(f"dedupe lock of owner {owner_id} busy")


def _dedupe(
    session: Session, v: DocumentVersion, d: Document, text: str, settings: Settings, sf, es=None
) -> bool:
    """True if the version was parked for a near-dup decision.

    1. Lock-free: search everything visible (LSH bands, ES kNN, or the exhaustive scan: can take long).
    2. In ONE short transaction under the per-owner lock, fenced to the running job: compare-and-set minhash on
       `stage = 'summarized'`, write the bands, check the owner's own racing siblings (cheap), then compare-and-set
       the decision (`review` + near_dup_of, or `deduped`) and commit. Bands and decision are atomic: a crash leaves
       the version at `summarized` with nothing written (the retry redoes it), and a stale run changes nothing.
    Of two simultaneous uploads of one paper the second to take the lock sees the first's bands: exactly one parks."""
    mh = minhash(text)
    settings = runtime.effective(session, settings)
    principal = principal_for_user(session, d.owner_id)
    with sf() as ps:  # phase 1: no lock, no write
        since = ps.scalar(select(func.now())) - timedelta(seconds=1)
        probe = ps.get(DocumentVersion, v.id)
        ps.expunge(probe)
        probe.minhash = mh  # detached: never flushed
        match = find_near_duplicate(
            ps, principal, probe, settings.near_dup_threshold, settings.near_dup_summary_threshold,
            es=es, settings=settings,
        )  # fmt: skip
    with sf() as ls:  # phase 2
        _owner_lock(ls, d.owner_id)
        fence(ls)
        if (  # compare-and-set: only the run that still sees `summarized` decides
            ls.execute(
                update(DocumentVersion)
                .where(DocumentVersion.id == v.id, DocumentVersion.stage == "summarized")
                .values(minhash=mh)
            ).rowcount
            == 0
        ):
            stage = ls.scalar(select(DocumentVersion.stage).where(DocumentVersion.id == v.id))
            ls.rollback()
            session.refresh(v)
            return stage == "review"  # already decided by another run
        write_bands(ls, v.id, mh)
        lv = ls.get(DocumentVersion, v.id)
        if match is None:
            match = find_sibling_duplicate(
                ls, principal, lv, settings.near_dup_threshold, settings.near_dup_summary_threshold, since
            )
        ls.execute(
            update(DocumentVersion)
            .where(DocumentVersion.id == v.id, DocumentVersion.stage == "summarized")
            .values(
                stage="review" if match is not None else "deduped", near_dup_of=match.id if match else None
            )
        )
        ls.commit()
    session.refresh(v)
    if match is not None:
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
    # first time this version reaches indexing: a crashed earlier run would have left "chunked"/"embedded".
    # (A future "reprocess" feature that resets a version to a pre-index stage must delete its stale chunks itself.)
    first_index = not rebuild and v.stage in ("summarized", "deduped")
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
    tags = list(session.scalars(select(Tag.tag).where(Tag.document_id == d.id).order_by(Tag.tag)))
    latest = _is_latest(session, v, d)
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
        session_factory=sf, settings=settings, create=into is None,
    )  # fmt: skip
    search_index.index_chunks(
        es, v, d, chunks, vectors, acl, settings.embed_model, title=title, is_latest=latest, tags=tags,
        summary_vector=sv, prompt_shas=shas, index=index, contexts=notes, refresh=False,
    )  # fmt: skip
    keep_units = None
    if (
        settings.raptor
    ):  # None = units skipped (LLM outage): existing ones stay, `rebuild --what raptor` retries
        keep_units = _index_units(
            session, v, d, chunks, settings, es, llm, store, acl, title, latest, tags, index, soft=not rebuild
        )
    # The ONE refresh of this ingest, right after the bulk and BEFORE the ACL re-read: any ACL change, relabel or
    # DELETE that commits after our read then runs its update_by_query/delete_by_query on a searcher that already
    # contains these chunks (they act on the last refreshed state), and one that committed before is seen by the read.
    es.indices.refresh(index=search_index.MUTATE_ALIAS)
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
    if not first_index:  # a first index of this version has no earlier chunks to prune
        search_index.delete_stale_chunks(es, v.id, len(chunks), keep_units=keep_units, refresh=False)
    if latest and v.version > 1 and not rebuild:  # version 1 has no older versions to demote
        search_index.mark_previous_versions(es, d.id, v.version, refresh=False)
        clear_outdated_reasons(sf, d.id, v.id)
    if rebuild:
        return
    # R6 + demotion: stage, citations job and the is_latest flip commit atomically
    mark_searchable_and_enqueue(sf, v.id, demote_doc=d.id if latest else None)


def _index_units(
    session, v, d, chunks, settings, es, llm, store, acl, title, latest, tags, index, *, soft: bool = True
) -> list[str] | None:
    """Per-document summary units (retrieval aids; citations resolve to the source chunks). Written before the
    ACL convergence so a racing PATCH reaches them too. Returns the unit ids to keep (the caller's single
    delete_by_query removes the rest), or None when generation failed and `soft` (a summary-LLM outage must not
    block searchability; `roracle rebuild --what raptor` fills the gap later)."""
    try:
        units = build_units(session, llm, settings, store, v, d, chunks)
    except Exception:
        if not soft:
            raise
        log.warning("summary units skipped for version %s", v.id, exc_info=True)
        return None
    if units:
        prompts = [doc_prompt(title, u.text) for u in units]
        shas = [prompt_sha(p) for p in prompts]
        cache = search_index.cached_vectors(es, settings.embed_model, d.id, shas, settings.embed_dims, index)
        miss = [p for p, h in zip(prompts, shas, strict=True) if h not in cache]
        fresh = iter(embed(llm, settings.embed_model, miss, settings.embed_dims) if miss else [])
        vecs = [cache[h] if h in cache else next(fresh) for h in shas]
        runtime.assert_embedding_unchanged(session, settings)
        search_index.index_units(
            es, v, d, units, vecs, acl, settings.embed_model, title=title, is_latest=latest, tags=tags,
            index=index, prompt_shas=shas, refresh=False,
        )  # fmt: skip
    return [f"{v.id}:{u.key}" for u in units]


def index_units_only(
    session: Session, v: DocumentVersion, d: Document, doc: dict, settings: Settings, es, llm, sf, store
) -> None:
    """In-place `rebuild --what raptor`: write the summary units only. Chunks and their vectors are NOT
    re-bulked (no HNSW re-insert); ACL/tags are converged like a normal index run."""
    title = d.title or "none"
    chunks = chunk(doc, title, settings.embed_tokenizer)
    if not chunks:
        raise NonRetryable("no text extracted")
    acl = search_index.acl_fields(session, d)
    session.refresh(v)
    latest = _is_latest(session, v, d)
    tags = list(session.scalars(select(Tag.tag).where(Tag.document_id == d.id).order_by(Tag.tag)))
    index = search_index.active_index(settings)
    runtime.assert_embedding_unchanged(session, settings)
    keep = _index_units(
        session, v, d, chunks, settings, es, llm, store, acl, title, latest, tags, index, soft=False
    )
    es.indices.refresh(index=search_index.MUTATE_ALIAS)  # before the ACL re-read, as in `_index`
    fresh = read_acl(sf, d.id)
    if fresh is None:
        es.delete_by_query(
            index=search_index.MUTATE_ALIAS, query={"term": {"doc_id": str(d.id)}}, refresh=True,
            conflicts="proceed",
        )  # fmt: skip
        return
    if fresh[0] != {**acl, "tags": tags}:
        search_index.sync_acl(es, d.id, lambda: read_acl(sf, d.id))
    search_index.delete_stale_units(es, v.id, keep or [], refresh=False)


def _is_latest(session: Session, v: DocumentVersion, d: Document) -> bool:
    newest = session.scalar(
        select(func.max(DocumentVersion.version)).where(
            DocumentVersion.document_id == d.id, DocumentVersion.stage == "searchable"
        )
    )
    return v.version >= (newest or 0)  # a newer version may have finished first: never demote it


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
    check_lease()
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
        if _dedupe(session, v, d, document_text(doc), settings, sf, es):
            return
        session.refresh(v)
    check_lease()  # before the expensive embed + index phase
    _index(session, v, d, doc, settings, es, llm, sf, store)
