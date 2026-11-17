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
from sqlalchemy import delete, exists, func, or_, select, text, update
from sqlalchemy.orm import Session, sessionmaker

from roracle.acl_sync import drifted, read_acl_many
from roracle.models import Annotation, Blob, Document, DocumentShare, DocumentVersion, Evidence, Job
from roracle.queue import enqueue, payload_uuid
from roracle.search_index import ALIAS, MUTATE_ALIAS
from roracle.storage import BlobStore

log = logging.getLogger(__name__)

GC_MIN_AGE = timedelta(days=1)
PURGE_BATCH = 100
GC_BATCH = 500
RECONCILE_BATCH = 500
JOB_BATCH = 10_000
_MISSING = {"404", "NoSuchKey", "NotFound"}


def purge(session: Session, es: Elasticsearch, settings: Any, store: BlobStore) -> int:
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
                _purge_doc(session, es, store, doc_id)
        except Exception:
            log.exception("purge of document %s failed; skipped", doc_id)
    return len(docs)


def prune_jobs(session: Session, *, done_days: int = 7, failed_days: int = 30, batch: int = JOB_BATCH) -> int:
    """Delete up to `batch` finished jobs: `done` older than `done_days`, `failed` older than `failed_days`.
    `queued`/`running` rows are never touched. Returns the number deleted (== batch: more to do)."""
    deleted = 0
    for status, days in (("done", done_days), ("failed", failed_days)):
        if deleted >= batch:
            break
        r = session.execute(
            text(
                "DELETE FROM jobs WHERE id IN (SELECT id FROM jobs WHERE status = :st"
                " AND updated_at < now() - make_interval(days => :d) ORDER BY id LIMIT :n FOR UPDATE SKIP LOCKED)"
            ),
            {"st": status, "d": days, "n": batch - deleted},
        )
        deleted += r.rowcount
    return deleted


def _purge_doc(session: Session, es: Elasticsearch, store: BlobStore, doc_id: UUID) -> None:
    try:  # chunks first: a retry after a later failure finds nothing left to delete
        r = es.delete_by_query(
            index=MUTATE_ALIAS, query={"term": {"doc_id": str(doc_id)}}, refresh=True, conflicts="proceed"
        )
        if r and r.get("failures"):
            raise RuntimeError(f"delete_by_query failures: {r['failures'][:1]}")
    except NotFoundError:
        # only "no index at all" means nothing was indexed; a missing `chunks-all` alias with a live
        # `chunks` index would orphan the ES chunks, so raise (savepoint skips the doc, retried next run)
        if es.indices.exists_alias(name=MUTATE_ALIAS) or es.indices.exists(index=ALIAS):
            raise
    versions = session.execute(
        select(DocumentVersion.id, DocumentVersion.blob_sha256).where(DocumentVersion.document_id == doc_id)
    ).all()
    vids = [v for v, _ in versions]
    shas = [s for _, s in versions] + list(
        session.scalars(
            select(Evidence.blob_sha256)
            .join(Annotation, Annotation.id == Evidence.annotation_id)
            .where(
                or_(Annotation.document_id == doc_id, Annotation.target_document_id == doc_id),
                Evidence.blob_sha256.is_not(None),
            )
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
    for (
        vid
    ) in vids:  # derived text (docling json, rendition) outlives the rows otherwise; blob GC skips derived/
        _delete_prefix(store, f"derived/{vid}/")


def _delete_prefix(store: BlobStore, prefix: str) -> None:
    pages = store.client.get_paginator("list_objects_v2").paginate(Bucket=store.bucket, Prefix=prefix)
    for page in pages:
        keys = [{"Key": o["Key"]} for o in page.get("Contents", [])]
        if keys:
            resp = store.client.delete_objects(Bucket=store.bucket, Delete={"Objects": keys, "Quiet": True})
            if resp.get("Errors"):
                log.warning(
                    "delete_objects under %s failed for %d keys: %s",
                    prefix,
                    len(resp["Errors"]),
                    resp["Errors"][:3],
                )


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


def handle_reconcile_tags(session: Session, payload: dict, *, es: Elasticsearch) -> None:
    """Drift DETECTION over the next RECONCILE_BATCH live documents after the id cursor (bulk priority): compare
    each document's Postgres `acl_hash` with the hashes in ES (an in-sync corpus costs 0 ES writes). Repair is a
    normal-priority `acl_sync` job per drifted document (deduped), so a revocation fix never waits behind user
    work and always re-reads Postgres when it runs. One chain job per batch while full."""
    q = select(Document.id).where(Document.deleted_at.is_(None))
    if payload.get("after"):
        q = q.where(Document.id > payload_uuid(payload, "after"))
    ids = session.scalars(q.order_by(Document.id).limit(RECONCILE_BATCH)).all()
    if bad := drifted(es, read_acl_many(session, ids)):
        key = Job.payload["doc_id"].astext
        pending = set(
            session.scalars(select(key).where(Job.kind == "acl_sync", Job.status == "queued", key.in_(bad)))
        )
        for doc_id in sorted(bad - pending):
            enqueue(session, "acl_sync", {"doc_id": doc_id})
    if len(ids) == RECONCILE_BATCH:
        enqueue(session, "reconcile_tags", {"after": str(ids[-1])})


LSH_BATCH = 500


def handle_lsh_backfill(session: Session, payload: dict) -> None:
    """One-off (enqueued by migration 0014e): write the LSH bands of every version that has a MinHash, in id order,
    LSH_BATCH per job. The ingest keeps using the exhaustive check while this job exists."""
    from roracle.ingest.neardup import write_bands

    q = select(DocumentVersion.id, DocumentVersion.minhash).where(DocumentVersion.minhash.is_not(None))
    if payload.get("after"):
        q = q.where(DocumentVersion.id > payload_uuid(payload, "after"))
    rows = session.execute(q.order_by(DocumentVersion.id).limit(LSH_BATCH)).all()
    for vid, mh in rows:
        write_bands(session, vid, mh)
    if len(rows) == LSH_BATCH:
        enqueue(session, "lsh_backfill", {"after": str(rows[-1][0])})


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
    """`op` = purge | prune_jobs | gc | reconcile (manual) or all (periodic: runs all, then reschedules itself)."""
    op = payload.get("op", "all")
    periodic = op == "all"
    if periodic and not _scheduled(session, "queued"):
        enqueue(session, "maintenance", {"op": "all"}, run_after_s=settings.maintenance_interval_s)

    def _reconcile() -> bool:
        enqueue(session, "reconcile_tags", {})
        return False

    steps = {
        "purge": lambda: purge(session, es, settings, store) == PURGE_BATCH,
        "prune_jobs": lambda: prune_jobs(session) == JOB_BATCH,
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
