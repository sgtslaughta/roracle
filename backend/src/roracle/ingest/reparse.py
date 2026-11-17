"""Re-run GROBID on already-ingested PDFs to capture identifiers: job `reparse_refs` {version_id} + the
`roracle reparse-refs [--limit N]` CLI that enqueues it for versions whose meta has no `ids` yet."""

import json
import sys

from sqlalchemy import String, cast, exists, select, text
from sqlalchemy.orm import Session

from roracle.citations import _targets, backfill_for, match_ids, resolve_references
from roracle.config import Settings, get_settings
from roracle.db import make_session_factory
from roracle.ingest.parse import grobid_metadata, merge_page_ids
from roracle.ingest.pipeline import docling_key
from roracle.models import Document, DocumentVersion, Job
from roracle.queue import check_lease, enqueue, payload_uuid
from roracle.search_signals import enqueue_signals
from roracle.storage import BlobStore

KIND = "reparse_refs"


def _is_pdf(v: DocumentVersion) -> bool:
    return v.filename.lower().endswith(".pdf") or v.content_type == "application/pdf"


def handle_reparse_refs(session: Session, payload: dict, *, settings: Settings, store: BlobStore) -> None:
    """Refresh `ids`/`doi` and `references` of one version (user-edited title/authors/etc. are untouched), then
    relink both directions. Runs in the worker's SAVEPOINT; never commits."""
    v = session.get(DocumentVersion, payload_uuid(payload, "version_id"))
    d = session.get(Document, v.document_id) if v else None
    if v is None or d is None or d.deleted_at is not None or v.stage != "searchable":
        return
    if not settings.grobid_url or not _is_pdf(v):
        return
    meta, refs = grobid_metadata(
        store.open(v.blob_sha256).read(), settings.grobid_url, timeout=settings.grobid_timeout_s
    )
    check_lease()  # the slow call is done: stop here if the lease was lost
    if "ids" not in meta:  # no parsed TEI: nothing learned, the version stays eligible for another reparse
        return
    try:
        doc = json.loads(store.get_bytes(docling_key(v.id)))
    except Exception:  # noqa: BLE001  no parsed doc: header/reference ids only
        doc = {}
    new = merge_page_ids(meta, doc)
    old = v.meta or {}
    ids = {**(old.get("ids") or {}), **new["ids"]}
    before = match_ids(old)
    v.meta = {**old, "ids": ids, "doi": ids.get("doi") or old.get("doi")}
    if refs:  # a failed/empty GROBID answer must not wipe the references we already have
        v.references = refs
    session.flush()
    old_targets = _targets(session, v.id)
    resolve_references(session, v)
    if match_ids(v.meta) != before:  # edges INTO this document only depend on its matchable ids
        backfill_for(session, d)
    # as handle_citations: ES `cited_by_global` of every target gained or lost (and of d) stays an upper bound (R2)
    enqueue_signals(session, old_targets | _targets(session, v.id) | {d.id})


def add_parser(sub) -> None:
    p = sub.add_parser("reparse-refs", help="re-run GROBID on PDFs lacking identifiers (enqueues jobs)")
    p.add_argument("--limit", type=int, default=0, metavar="N", help="enqueue at most N versions (0 = all)")


def enqueue_missing(session: Session, limit: int = 0) -> int:
    """Latest searchable PDF versions without meta.ids and with no queued/running reparse job; returns the count."""
    busy = exists().where(
        Job.kind == KIND,
        Job.status.in_(("queued", "running")),
        Job.payload["version_id"].astext == cast(DocumentVersion.id, String),
    )
    q = (
        select(DocumentVersion.id)
        .join(Document, Document.id == DocumentVersion.document_id)
        .where(
            Document.deleted_at.is_(None),
            DocumentVersion.is_latest.is_(True),
            DocumentVersion.stage == "searchable",
            text("NOT (document_versions.meta ? 'ids')"),
            (DocumentVersion.filename.ilike("%.pdf")) | (DocumentVersion.content_type == "application/pdf"),
            ~busy,
        )
        .order_by(DocumentVersion.created_at, DocumentVersion.id)
    )
    if limit:
        q = q.limit(limit)
    ids = session.scalars(q).all()
    for vid in ids:
        enqueue(session, KIND, {"version_id": str(vid)})
    return len(ids)


def run(args) -> int:
    settings = get_settings()
    if not settings.grobid_url:
        print("error: GROBID is not configured (ROR_GROBID_URL)", file=sys.stderr)
        return 2
    with make_session_factory(settings.database_url, settings.db_connect_timeout_s)() as session:
        n = enqueue_missing(session, args.limit)
        session.commit()
    print(f"enqueued {n} {KIND} job(s)")
    return 0
