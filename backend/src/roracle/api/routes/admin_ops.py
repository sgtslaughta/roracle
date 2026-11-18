"""Admin operations: job queue, stats, health."""

from typing import Annotated, Literal
from uuid import UUID

from elasticsearch import Elasticsearch
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import extract, func, select, text, update
from sqlalchemy.orm import Session

from roracle.api.routes.admin_deps import Admin, keyset_page, keyset_params
from roracle.api.routes.documents import get_blob_store
from roracle.api.routes.library import get_es
from roracle.audit import audit
from roracle.connectors.run import active_run_job
from roracle.db import get_session
from roracle.models import (
    Blob,
    Chat,
    Connector,
    Document,
    DocumentVersion,
    Job,
    User,
)
from roracle.queue import enqueue
from roracle.storage import BlobStore

router = APIRouter()


def _job(j: Job, doc: tuple[str, str] | None = None) -> dict:
    out = {
        "id": j.id,
        "kind": j.kind,
        "status": j.status,
        "attempts": j.attempts,
        "last_error": j.last_error,
        "payload": j.payload,
        "created_at": j.created_at.isoformat(),
        "updated_at": j.updated_at.isoformat(),
    }
    if doc:
        out["document_id"], out["filename"] = doc
    return out


def _uuid(v) -> UUID | None:
    try:
        return UUID(str(v))
    except ValueError:
        return None


@router.get("/admin/jobs")
def list_jobs(
    p: Admin,
    session: Annotated[Session, Depends(get_session)],
    paging: Annotated[tuple[int | None, int], Depends(keyset_params)],
    status: str = "",
    kind: str = "",
) -> dict:
    before_id, limit = paging
    q = select(Job)
    if status:
        q = q.where(Job.status == status)
    if kind:
        q = q.where(Job.kind == kind)
    if before_id:
        q = q.where(Job.id < before_id)
    jobs = list(session.scalars(q.order_by(Job.id.desc()).limit(limit + 1)))
    vids = {u for j in jobs if (u := _uuid(j.payload.get("version_id")))}
    docs = {}
    if vids:
        docs = {
            v.id: (str(v.document_id), v.filename)
            for v in session.scalars(select(DocumentVersion).where(DocumentVersion.id.in_(vids)))
        }
    items = [_job(j, docs.get(_uuid(j.payload.get("version_id")))) for j in jobs]
    return keyset_page(items, limit)


@router.get("/admin/jobs/summary")
def jobs_summary(p: Admin, session: Annotated[Session, Depends(get_session)]) -> dict:
    jobs: dict[str, dict[str, int]] = {}
    for kind, status, n in session.execute(
        select(Job.kind, Job.status, func.count()).group_by(Job.kind, Job.status)
    ):
        jobs.setdefault(kind, {})[status] = n
    versions = dict(
        session.execute(select(DocumentVersion.stage, func.count()).group_by(DocumentVersion.stage)).all()
    )
    return {"jobs": jobs, "versions": versions}


def _reset_ingest(session: Session, version_ids: list) -> None:
    ids = [u for v in version_ids if (u := _uuid(v))]
    if ids:  # R17: a retried ingest gets a fresh attempt budget
        session.execute(update(DocumentVersion).where(DocumentVersion.id.in_(ids)).values(ingest_attempts=0))


_REQUEUE = {"status": "queued", "attempts": 0, "run_after": func.now(), "last_error": None}


@router.post("/admin/jobs/{job_id}/retry")
def retry_job(job_id: int, p: Admin, session: Annotated[Session, Depends(get_session)]) -> dict:
    j = session.scalar(select(Job).where(Job.id == job_id).with_for_update())
    if j is None:
        raise HTTPException(404, "job not found")
    if j.status != "failed":
        raise HTTPException(409, "only failed jobs can be retried")
    if j.kind == "connector_run" and active_run_job(session, _uuid(j.payload.get("connector_id"))):
        raise HTTPException(409, "connector already has an active run")
    if j.kind == "ingest":
        _reset_ingest(session, [j.payload.get("version_id")])
    for k, v in _REQUEUE.items():
        setattr(j, k, v)
    audit(session, p, "admin.job_retry", str(job_id), {"kind": j.kind})
    session.flush()
    session.refresh(j)
    return _job(j)


RETRY_BATCH = 1000  # bound the FOR UPDATE; call again for the rest ("retried" says how many moved)


class RetryFailed(BaseModel):
    kind: str | None = None


@router.post("/admin/jobs/retry-failed")
def retry_failed(
    p: Admin, session: Annotated[Session, Depends(get_session)], body: RetryFailed | None = None
) -> dict:
    kind = body.kind if body else None
    q = select(Job).where(Job.status == "failed").order_by(Job.id).limit(RETRY_BATCH).with_for_update()
    if kind:
        q = q.where(Job.kind == kind)
    jobs = []
    for j in session.scalars(q):  # a failed connector_run is skipped while its connector is running
        if j.kind == "connector_run" and active_run_job(session, _uuid(j.payload.get("connector_id"))):
            continue
        jobs.append(j)
    _reset_ingest(session, [j.payload.get("version_id") for j in jobs if j.kind == "ingest"])
    for j in jobs:
        for k, v in _REQUEUE.items():
            setattr(j, k, v)
    audit(session, p, "admin.jobs_retry_failed", "", {"kind": kind, "retried": len(jobs)})
    return {"retried": len(jobs)}


@router.post("/admin/maintenance/{op}", status_code=202)
def run_maintenance(
    op: Literal["purge", "gc", "reconcile"], p: Admin, session: Annotated[Session, Depends(get_session)]
) -> dict:
    """Queue a one-off maintenance job (retention purge, blob GC or ES tag reconcile)."""
    job_id = enqueue(session, "maintenance", {"op": op})
    audit(session, p, f"admin.maintenance_{op}", str(job_id))
    return {"job_id": job_id}


@router.get("/admin/stats")
def stats(
    p: Admin,
    session: Annotated[Session, Depends(get_session)],
    top: Annotated[int, Query(ge=1, le=50)] = 10,
) -> dict:
    """top_uploaders bytes skip rejected/cancelled versions (quota semantics); connector docs count."""
    live, deleted = session.execute(
        select(
            func.count().filter(Document.deleted_at.is_(None)),
            func.count().filter(Document.deleted_at.is_not(None)),
        )
    ).one()
    versions = dict(
        session.execute(select(DocumentVersion.stage, func.count()).group_by(DocumentVersion.stage)).all()
    )
    n_blobs, blob_bytes = session.execute(select(func.count(), func.coalesce(func.sum(Blob.size), 0))).one()
    size = func.coalesce(func.sum(DocumentVersion.size), 0)
    top = session.execute(
        select(User.id, User.username, func.count(func.distinct(Document.id)), size)
        .join(Document, Document.owner_id == User.id)
        .join(DocumentVersion, DocumentVersion.document_id == Document.id)
        .where(Document.deleted_at.is_(None), DocumentVersion.stage.not_in(("rejected", "cancelled")))
        .group_by(User.id, User.username)
        .order_by(size.desc(), User.username)
        .limit(top)
    ).all()

    def count(model) -> int:
        return session.scalar(select(func.count()).select_from(model)) or 0

    return {
        "documents": {"live": live, "deleted": deleted},
        "versions": versions,
        "blobs": {"count": n_blobs, "bytes": int(blob_bytes)},
        "users": count(User),
        "chats": count(Chat),
        "connectors": count(Connector),
        "top_uploaders": [
            {"user_id": str(uid), "username": name, "documents": n, "bytes": int(b)}
            for uid, name, n, b in top
        ],
    }


def _check(fn) -> str:
    try:
        fn()
        return "ok"
    except Exception as e:  # noqa: BLE001 - a health probe reports any failure
        return f"error: {type(e).__name__}"


@router.get("/admin/health")
def health(
    p: Admin,
    session: Annotated[Session, Depends(get_session)],
    es: Annotated[Elasticsearch, Depends(get_es)],
    store: Annotated[BlobStore, Depends(get_blob_store)],
) -> dict:
    def elastic():
        if not es.ping():
            raise ConnectionError

    checks = {
        "db": _check(lambda: session.execute(text("SELECT 1"))),
        "elastic": _check(elastic),
        "storage": _check(lambda: store.client.head_bucket(Bucket=store.bucket)),
    }
    status = "ok" if all(v == "ok" for v in checks.values()) else "degraded"
    try:
        queue = dict.fromkeys(("queued", "running", "failed"), 0)
        queue.update(dict(session.execute(select(Job.status, func.count()).group_by(Job.status)).all()))
        oldest = session.scalar(
            select(extract("epoch", func.now() - func.min(Job.created_at))).where(
                Job.status == "queued", Job.run_after <= func.now()
            )
        )
        ticking = session.scalar(
            select(
                select(Job.id)
                .where(Job.kind == "connector_tick", Job.status.in_(("queued", "running")))
                .exists()
            )
        )
    except Exception:  # noqa: BLE001 - db down: report degraded, not 500
        session.rollback()
        return {"status": "degraded", "checks": checks, "queue": None, "scheduler": None}
    return {
        "status": status,
        "checks": checks,
        "queue": {
            "queued": queue["queued"],
            "running": queue["running"],
            "failed": queue["failed"],
            "oldest_queued_s": int(oldest) if oldest is not None else None,
        },
        "scheduler": {"tick_queued": bool(ticking)},
    }
