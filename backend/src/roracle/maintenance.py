"""Maintenance jobs: retention purge, blob GC, ES tag reconcile (R5). Handlers never commit.

Blob GC safety (ADR-0008 dedup is DB-only; an upload upserts the `blobs` row (row lock) *first*, then
always copies the object to `blobs/`, then commits):
- candidates are locked `FOR UPDATE SKIP LOCKED`: an in-flight upsert/FK insert holds or needs the row
  lock, so a blob being re-referenced is skipped or blocks until we commit (its FK then fails, no dangling ref);
- reference check (versions + evidence) is repeated under the lock, right before the S3 delete;
- S3 `LastModified` must be older than GC_MIN_AGE, so an object an upload just re-copied is spared;
- only `blobs` rows are considered: `tmp/` (in-flight uploads) is never listed or touched (MinIO lifecycle).
"""

import logging
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from botocore.exceptions import ClientError
from elasticsearch import Elasticsearch, NotFoundError
from sqlalchemy import delete, exists, func, select, text, update
from sqlalchemy.orm import Session, sessionmaker

from roracle.models import Annotation, Blob, Document, DocumentShare, DocumentVersion, Evidence, Job
from roracle.queue import enqueue
from roracle.search_index import ALIAS
from roracle.storage import BlobStore

log = logging.getLogger(__name__)

GC_MIN_AGE = timedelta(days=1)
PURGE_BATCH = 100
GC_BATCH = 500
RECONCILE_BATCH = 500
_MISSING = {"404", "NoSuchKey", "NotFound"}


def purge(session: Session, es: Elasticsearch, settings: Any) -> int:
    """Hard-delete up to PURGE_BATCH documents soft-deleted more than N days ago; 0 days = off."""
    if settings.retention_deleted_days <= 0:
        return 0
    cutoff = func.now() - timedelta(days=settings.retention_deleted_days)
    docs = session.scalars(
        select(Document.id)
        .where(Document.deleted_at < cutoff)
        .order_by(Document.deleted_at)
        .limit(PURGE_BATCH)
        .with_for_update(skip_locked=True)
    ).all()
    for doc_id in docs:
        try:
            with session.begin_nested():  # one poison document must not stall the rest
                _purge_doc(session, es, doc_id)
        except Exception:
            log.exception("purge of document %s failed; skipped", doc_id)
    return len(docs)


def _purge_doc(session: Session, es: Elasticsearch, doc_id: UUID) -> None:
    try:  # chunks first: a retry after a later failure finds nothing left to delete
        r = es.delete_by_query(
            index=ALIAS, query={"term": {"doc_id": str(doc_id)}}, refresh=True, conflicts="proceed"
        )
        if r and r.get("failures"):
            raise RuntimeError(f"delete_by_query failures: {r['failures'][:1]}")
    except NotFoundError:
        pass  # nothing indexed yet
    versions = session.execute(
        select(DocumentVersion.id, DocumentVersion.blob_sha256).where(DocumentVersion.document_id == doc_id)
    ).all()
    vids = [v for v, _ in versions]
    shas = [s for _, s in versions] + list(
        session.scalars(
            select(Evidence.blob_sha256)
            .join(Annotation, Annotation.id == Evidence.annotation_id)
            .where(Annotation.document_id == doc_id, Evidence.blob_sha256.is_not(None))
        )
    )
    for sha in set(shas):
        n = shas.count(sha)
        session.execute(
            update(Blob).where(Blob.sha256 == sha).values(refcount=func.greatest(Blob.refcount - n, 0))
        )
    session.execute(
        update(DocumentVersion).where(DocumentVersion.near_dup_of.in_(vids)).values(near_dup_of=None)
    )
    session.execute(delete(DocumentShare).where(DocumentShare.document_id == doc_id))
    session.execute(delete(DocumentVersion).where(DocumentVersion.document_id == doc_id))
    session.execute(delete(Document).where(Document.id == doc_id))  # notes, tags, votes, citations cascade


def _referenced(session: Session, sha: str) -> bool:
    return bool(
        session.scalar(
            select(
                exists().where(DocumentVersion.blob_sha256 == sha)
                | exists().where(Evidence.blob_sha256 == sha)
            )
        )
    )


def _s3_idle(store: BlobStore, key: str) -> bool | None:
    """True if the object has not been written for GC_MIN_AGE, False if it is fresh, None if missing."""
    try:
        head = store.client.head_object(Bucket=store.bucket, Key=key)
    except ClientError as e:
        if str(e.response.get("Error", {}).get("Code")) in _MISSING:
            return None
        raise
    return datetime.now(UTC) - head["LastModified"] >= GC_MIN_AGE


def gc_blobs(session: Session, store: BlobStore) -> int:
    """Delete up to GC_BATCH unreferenced blobs (object, then row); returns how many were removed."""
    shas = session.scalars(
        select(Blob.sha256)
        .where(
            Blob.created_at < func.now() - GC_MIN_AGE,
            ~exists().where(DocumentVersion.blob_sha256 == Blob.sha256),
            ~exists().where(Evidence.blob_sha256 == Blob.sha256),
        )
        .order_by(Blob.sha256)
        .limit(GC_BATCH)
        .with_for_update(skip_locked=True)
    ).all()
    removed = 0
    for sha in shas:  # row locks held until the job transaction ends
        if _referenced(session, sha):  # re-check under the lock, before touching S3
            continue
        idle = _s3_idle(store, store.key(sha))
        if idle is False:
            continue
        if idle:
            store.client.delete_object(Bucket=store.bucket, Key=store.key(sha))
        session.execute(delete(Blob).where(Blob.sha256 == sha))
        removed += 1
    return removed


def handle_reconcile_tags(session: Session, payload: dict) -> None:
    """Enqueue `acl_sync` for the next batch of live documents after the id cursor; chain while full."""
    q = select(Document.id).where(Document.deleted_at.is_(None))
    if payload.get("after"):
        q = q.where(Document.id > UUID(payload["after"]))
    ids = session.scalars(q.order_by(Document.id).limit(RECONCILE_BATCH)).all()
    for doc_id in ids:
        enqueue(session, "acl_sync", {"doc_id": str(doc_id)})
    if len(ids) == RECONCILE_BATCH:
        enqueue(session, "reconcile_tags", {"after": str(ids[-1])})


def _scheduled(session: Session, *statuses: str) -> bool:
    return bool(
        session.scalar(
            select(Job.id)
            .where(Job.kind == "maintenance", Job.payload["op"].astext == "all", Job.status.in_(statuses))
            .limit(1)
        )
    )


def handle_maintenance(
    session: Session, payload: dict, *, settings: Any, store: BlobStore, es: Elasticsearch
) -> None:
    """`op` = purge | gc | reconcile (manual) or all (periodic: runs all three, then reschedules itself)."""
    op = payload.get("op", "all")
    periodic = op == "all"
    if periodic and not _scheduled(session, "queued"):
        enqueue(session, "maintenance", {"op": "all"}, run_after_s=settings.maintenance_interval_s)

    def _reconcile() -> bool:
        enqueue(session, "reconcile_tags", {})
        return False

    steps = {
        "purge": lambda: purge(session, es, settings) == PURGE_BATCH,
        "gc": lambda: gc_blobs(session, store) == GC_BATCH,
        "reconcile": _reconcile,
    }
    for name, step in steps.items():
        if not periodic and name != op:
            continue
        try:
            with session.begin_nested():  # one failing step must not stop the others (periodic only)
                more = step()
            if more:  # full batch: continue in a follow-up job
                enqueue(session, "maintenance", {"op": name})
        except Exception:
            if not periodic:
                raise
            log.exception("maintenance step %s failed", name)


def ensure_maintenance(sf: sessionmaker[Session]) -> None:
    """Worker start: make sure the periodic job exists (advisory lock: several workers may start at once)."""
    with sf() as s:
        s.execute(text("SELECT pg_advisory_xact_lock(hashtextextended('maintenance', 0))"))
        if not _scheduled(s, "queued", "running"):
            enqueue(s, "maintenance", {"op": "all"})
        s.commit()
