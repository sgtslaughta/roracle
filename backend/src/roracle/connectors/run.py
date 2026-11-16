"""Connector run + scheduler handlers. The handler session stays read-only for documents tables;
every write (run row, items, documents, connector timestamps) uses its own short committed session."""

import logging
import time
from collections import deque
from contextlib import closing
from datetime import timedelta
from uuid import UUID

from sqlalchemy import func, select, text, update
from sqlalchemy.orm import Session, sessionmaker

from roracle import notify, runtime
from roracle.config import Settings
from roracle.connectors import Item, RunDeadline, source_for
from roracle.ingest.intake import add_document, add_version
from roracle.labels import service
from roracle.models import Connector, ConnectorItem, ConnectorRun, Document, DocumentVersion, Job
from roracle.queue import enqueue, payload_uuid
from roracle.storage import BlobStore, EmptyFile, TooLarge, stage

log = logging.getLogger(__name__)
LOG_LINES = 500
SF = sessionmaker[Session]


class Gone(Exception):
    """The connector was deleted while its run was in flight."""


STATS = ("seen", "ingested", "versions", "unchanged", "duplicate", "too_large", "errors")


def active_run_job(session: Session, connector_id: UUID) -> int | None:
    return session.scalar(
        select(Job.id)
        .where(
            Job.kind == "connector_run",
            Job.status.in_(("queued", "running")),
            Job.payload["connector_id"].astext == str(connector_id),
        )
        .limit(1)
    )


def _outcome(sf: SF, c: Connector, item: Item, cap: int, store: BlobStore) -> str:
    cid = c.id
    with sf() as s:
        known = s.get(ConnectorItem, (cid, item.key))
        if known is not None and item.etag and known.etag == item.etag:
            return "unchanged"
    if item.size is not None and item.size > cap:
        return "too_large"
    stream = item.open()
    try:
        staged = stage(stream, cap)
    except TooLarge:
        return "too_large"
    except EmptyFile:
        return "unchanged"  # nothing to ingest; re-checked when the etag changes
    finally:
        stream.close()
    try:
        with sf() as s:
            # key-share lock: a concurrent DELETE of the connector waits for this commit; checked before any put
            if (
                s.scalar(select(Connector.id).where(Connector.id == cid).with_for_update(key_share=True))
                is None
            ):
                raise Gone
            known = s.get(ConnectorItem, (cid, item.key), with_for_update=True)
            if known is not None and known.sha256 == staged.sha256:
                known.etag = item.etag
                known.seen_at = func.now()
                s.commit()
                return "unchanged"
            dup = s.scalar(
                select(DocumentVersion.document_id)
                .join(Document, Document.id == DocumentVersion.document_id)
                .where(
                    DocumentVersion.blob_sha256 == staged.sha256,
                    Document.deleted_at.is_(None),
                    Document.connector_id == cid,  # never reference (or leak) another principal's document
                )
                .limit(1)
            )
            if dup is not None:
                result, doc_id = "duplicate", dup
            else:
                doc = (
                    s.get(Document, known.document_id, with_for_update=True)
                    if known and known.document_id
                    else None
                )
                if doc is not None and doc.deleted_at is None and doc.connector_id == cid:
                    add_version(s, store, staged, doc, filename=item.filename, content_type=item.content_type)
                    result = "versions"
                else:
                    label_id = service.initial_label(s, None, connector=c)
                    doc, _ = add_document(
                        s,
                        store,
                        staged,
                        owner_id=c.owner_id,
                        org_unit=c.org_unit,
                        visibility=c.visibility,
                        filename=item.filename,
                        content_type=item.content_type,
                        connector_id=cid,
                        label_id=label_id,
                    )
                    result = "ingested"
                doc_id = doc.id
            if known is None:
                s.add(
                    ConnectorItem(
                        connector_id=cid,
                        key=item.key,
                        etag=item.etag,
                        sha256=staged.sha256,
                        document_id=doc_id,
                    )
                )
            else:
                known.etag, known.sha256, known.document_id, known.seen_at = (
                    item.etag,
                    staged.sha256,
                    doc_id,
                    func.now(),
                )
            s.commit()
            return result
    finally:
        staged.path.unlink(missing_ok=True)


def _finish(sf: SF, c: Connector, run_id: int, status: str, stats: dict, lines: deque) -> None:
    with sf() as s:
        run = s.get(ConnectorRun, run_id)
        if run is None:  # connector deleted mid-run (cascade)
            return
        run.status, run.finished_at, run.stats, run.log = status, func.now(), stats, "\n".join(lines)
        conn = s.get(Connector, c.id)
        if conn is not None:
            conn.last_run_at = func.now()
        if status != "ok":
            notify.emit_admins(
                s,
                "ops.connector_run",
                {
                    "connector_id": str(c.id),
                    "name": c.name,
                    "run_id": run_id,
                    "status": status,
                    "stats": stats,
                },
            )
        s.commit()


def _run(session: Session, payload: dict, settings: Settings, store: BlobStore, sf: SF) -> None:
    c = session.get(Connector, payload_uuid(payload, "connector_id"))
    if c is None:
        return
    eff = runtime.effective(session, settings)
    with sf() as s:
        run = ConnectorRun(connector_id=c.id, status="running")
        s.execute(
            update(ConnectorRun)
            .where(ConnectorRun.connector_id == c.id, ConnectorRun.status == "running")
            .values(status="failed", finished_at=func.now(), log="worker interrupted")
        )
        s.add(run)
        s.commit()
        run_id = run.id
    stats = dict.fromkeys(STATS, 0)
    lines: deque[str] = deque(maxlen=LOG_LINES)
    status = "ok"
    deadline = time.monotonic() + settings.connector_max_run_s
    warned = False

    def warn(msg: str) -> None:
        nonlocal warned
        warned = True
        lines.append(msg)

    try:
        with closing(source_for(c, eff, deadline, warn)) as it:
            for item in it:
                # the item cap bounds work done; unchanged items are cheap (the time cap bounds them)
                if (
                    stats["seen"] - stats["unchanged"] >= settings.connector_max_items
                    or time.monotonic() > deadline
                ):
                    status = "partial"
                    lines.append("run cap reached (items or time); remaining items next run")
                    break
                stats["seen"] += 1
                try:
                    stats[_outcome(sf, c, item, eff.max_upload_bytes, store)] += 1
                except Gone:
                    return
                except Exception as e:
                    stats["errors"] += 1
                    status = "partial"
                    lines.append(f"error {item.key}: {type(e).__name__}: {str(e)[:200]}")
                    log.exception("connector %s item %s", c.id, item.key)
    except RunDeadline:
        status = "partial"
        lines.append("run cap reached (time) while listing; remaining items next run")
    except Exception as e:
        status = "failed"
        lines.append(f"source failed: {type(e).__name__}: {str(e)[:300]}")
        log.exception("connector %s source failed", c.id)
    if warned and status == "ok":
        status = "partial"
    _finish(sf, c, run_id, status, stats, lines)


def handle_connector_run(
    session: Session, payload: dict, *, settings: Settings, store: BlobStore, session_factory: SF
) -> None:
    """Never raises: a failed run is recorded; a broken DB is only logged."""
    try:
        _run(session, payload, settings, store, session_factory)
    except Exception:
        log.exception("connector run %s", payload)


def _enqueue_due(session: Session) -> None:
    due = session.scalars(
        select(Connector)
        .where(
            Connector.enabled.is_(True),
            Connector.schedule_minutes.is_not(None),
            (Connector.next_run_at.is_(None)) | (Connector.next_run_at <= func.now()),
        )
        .with_for_update(skip_locked=True)
    ).all()
    for c in due:
        if active_run_job(session, c.id) is None:
            enqueue(session, "connector_run", {"connector_id": str(c.id)})
        c.next_run_at = func.now() + timedelta(minutes=c.schedule_minutes)
    session.flush()


def handle_connector_tick(session: Session, payload: dict, *, settings: Settings) -> None:
    """Enqueue due connectors (one run each), then always schedule the next tick."""
    try:
        with session.begin_nested():  # a bad connector must not kill the scheduler
            _enqueue_due(session)
    except Exception:
        log.exception("connector tick scan failed")
    if not session.scalar(
        select(Job.id).where(Job.kind == "connector_tick", Job.status == "queued").limit(1)
    ):
        enqueue(session, "connector_tick", {}, run_after_s=settings.connector_tick_s)


def ensure_tick(sf: SF) -> None:
    """Worker start: make sure a tick is queued/running (advisory lock: several workers may start at once)."""
    with sf() as s:
        s.execute(text("SELECT pg_advisory_xact_lock(hashtextextended('connector_tick', 0))"))
        if not s.scalar(
            select(Job.id).where(Job.kind == "connector_tick", Job.status.in_(("queued", "running"))).limit(1)
        ):
            enqueue(s, "connector_tick", {})
        s.commit()
