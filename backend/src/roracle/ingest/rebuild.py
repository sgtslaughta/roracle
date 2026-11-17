"""Index-time rebuild jobs (R7): re-run `pipeline._index` for every latest searchable version, into the
active index or a shadow index that joins `chunks-all` (so ACL syncs and deletes reach it) but never the
read alias. Live search is untouched: the existing alias/cutover mechanics are not involved.

`rebuild_units`  one per version; `rebuild_watch`  polls every 60 s, then triggers the reindex eval.
"""

import json
import logging
import re
import uuid
from typing import Literal

from elasticsearch import Elasticsearch, NotFoundError
from sqlalchemy import String, cast, func, insert, literal, select, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Session, sessionmaker

from roracle import reembed, runtime, search_index
from roracle.config import Settings
from roracle.ingest import pipeline
from roracle.models import Document, DocumentVersion, Job
from roracle.models_eval import EvalSet
from roracle.queue import NonRetryable, check_lease, enqueue, payload_uuid
from roracle.search_index import MUTATE_ALIAS

log = logging.getLogger(__name__)
WATCH_S = 60
_NAME = re.compile(r"chunks-[a-z0-9][a-z0-9-]{0,100}")
_GENERATION = re.compile(r"chunks-.+-\d+")  # re-embed generations are never shadows
Kind = Literal["context", "raptor"]


def _holders(es: Elasticsearch, alias: str) -> set[str]:
    try:
        return set(es.indices.get_alias(name=alias))
    except NotFoundError:
        return set()


def create_shadow(es: Elasticsearch, settings: Settings, name: str) -> str:
    """Create `name` inside `chunks-all` only; `settings` must be the effective ones (model/dims/active index)."""
    if not _NAME.fullmatch(name) or name == MUTATE_ALIAS or _GENERATION.fullmatch(name):
        raise ValueError("index name must match chunks-<slug>")
    if name == search_index.active_index(settings) or name in _holders(es, settings.read_alias):
        raise ValueError("refusing the active index")
    if es.indices.exists(index=name):
        if name not in _holders(es, MUTATE_ALIAS):
            raise ValueError(f"{name!r} exists but is not a chunk index")
        return name
    es.indices.create(
        index=name,
        mappings=search_index._mapping(settings.embed_dims, settings),
        settings=search_index.index_settings(settings),
        aliases={MUTATE_ALIAS: {}},
    )
    return name


def drop_shadow(es: Elasticsearch, name: str, active: str | None = None, read_alias: str = "chunks") -> None:
    """Delete one non-active index of `chunks-all`. Refuses the active index, the read-alias holder and
    anything outside `chunks-all`."""
    if name == active or name in _holders(es, read_alias):
        raise ValueError("refusing the active index")
    if name not in _holders(es, MUTATE_ALIAS):
        raise ValueError(f"{name!r} is not an index of {MUTATE_ALIAS}")
    es.indices.delete(index=name)


VERSIONS_PER_JOB = 25  # one job per page of versions: queue rows and job round trips stay O(versions / 25)


def enqueue_all(session: Session, what: set[Kind], into: str | None) -> int:
    """`rebuild_units` jobs of VERSIONS_PER_JOB latest searchable non-deleted versions each (INSERT ... SELECT, in id
    order) + one watch. Returns the number of jobs."""
    batch, kinds = uuid.uuid4().hex, sorted(what)
    live = (
        select(
            DocumentVersion.id.label("id"),
            func.div(func.row_number().over(order_by=DocumentVersion.id) - 1, VERSIONS_PER_JOB).label("g"),
        )
        .select_from(DocumentVersion.__table__.join(Document, Document.id == DocumentVersion.document_id))
        .where(
            DocumentVersion.stage == "searchable", DocumentVersion.is_latest, Document.deleted_at.is_(None)
        )
        .subquery()
    )
    payload = func.jsonb_build_object(
        "version_ids", func.jsonb_agg(cast(live.c.id, String)), "what", cast(literal(json.dumps(kinds)), JSONB),
        "into", cast(literal(into), String), "batch", batch,
    )  # fmt: skip
    sel = select(literal("rebuild_units"), payload).select_from(live).group_by(live.c.g)
    n = len(session.execute(insert(Job).from_select(["kind", "payload"], sel).returning(Job.id)).all())
    enqueue(session, "rebuild_watch", {"what": kinds, "into": into, "batch": batch}, run_after_s=WATCH_S)
    return n


def start(session: Session, es: Elasticsearch, settings: Settings, what: set[Kind], into: str | None) -> int:
    """`settings` effective. A running re-embed would copy stale notes: refuse (reembed.AlreadyRunning)."""
    session.execute(text(reembed.LOCK))  # same lock as reembed.start: the two never interleave
    if reembed.running_job(session) is not None:
        raise reembed.AlreadyRunning
    if "context" in what and not settings.context_model:
        raise ValueError("context_model (ROR_CONTEXT_MODEL) is not set")
    if into == search_index.active_index(settings):
        into = None
    if into:
        create_shadow(es, settings, into)
    return enqueue_all(session, what, into)


def handle_rebuild_units(
    session: Session,
    payload: dict,
    *,
    settings: Settings,
    store,
    es,
    llm,
    session_factory: sessionmaker[Session],
) -> None:
    ids = payload.get("version_ids") or [
        payload.get("version_id")
    ]  # a single `version_id`: jobs queued before 10.11
    vids = [payload_uuid({"version_id": x}, "version_id") for x in ids]
    settings = runtime.effective(session, settings)
    into = payload.get("into") or None
    if into == search_index.active_index(settings):
        into = None
    what = payload.get("what") or []
    if "context" in what and not settings.context_model:
        raise NonRetryable("ROR_CONTEXT_MODEL is not set")
    # a raptor-only job keeps the configured notes setting (cached), so chunks are rewritten identically
    settings = settings.model_copy(
        update={
            "context_notes": settings.context_notes or "context" in what,
            "raptor": settings.raptor or "raptor" in what,
        }
    )
    failed: list[str] = []
    for vid in vids:  # up to VERSIONS_PER_JOB versions, each idempotent; progress survives in ES, not here
        check_lease()
        try:
            _rebuild_one(session, vid, payload, settings, store, es, llm, session_factory, into, what)
        except NonRetryable as e:  # one broken version must not discard the other 24; reported at the end
            log.warning("rebuild of version %s skipped: %s", vid, e)
            failed.append(f"{vid}: {e}")
    if failed:
        raise NonRetryable(f"{len(failed)} of {len(vids)} versions could not be rebuilt: {failed[0]}")


def _rebuild_one(session, vid, payload, settings, store, es, llm, session_factory, into, what) -> None:
    v = session.get(DocumentVersion, vid)
    if v is None:
        return
    session.refresh(v)
    d = session.get(Document, v.document_id)
    session.refresh(d)
    if d.deleted_at is not None or v.stage != "searchable" or not v.is_latest:
        return  # deleted / re-processing / superseded since the enqueue
    if into and not es.indices.exists(index=into):
        raise NonRetryable(f"index {into!r} no longer exists")
    try:
        doc = json.loads(store.get_bytes(pipeline.docling_key(vid)))
    except Exception as e:
        raise NonRetryable("parsed document is missing; re-ingest the version") from e
    if into is None and set(what) == {"raptor"}:  # in place: units only, chunks/vectors untouched
        pipeline.index_units_only(session, v, d, doc, settings, es, llm, session_factory, store)
        return
    pipeline._index(session, v, d, doc, settings, es, llm, session_factory, store, into=into, rebuild=True)


def handle_rebuild_watch(session: Session, payload: dict, *, settings: Settings) -> None:
    pending = session.scalar(
        select(func.count())
        .select_from(Job)
        .where(
            Job.kind == "rebuild_units",
            Job.status.in_(("queued", "running")),
            Job.payload["batch"].astext == payload["batch"],
        )
    )
    if pending:
        enqueue(session, "rebuild_watch", payload, run_after_s=WATCH_S)
        return
    ref = session.scalar(select(EvalSet.id).where(EvalSet.is_reference.is_(True)))
    # a shadow is judged by an explicit `eval compare --candidate-index`; the active index by the auto run
    if (
        payload.get("into") is None
        and runtime.effective(session, settings).eval_on_reindex
        and ref is not None
    ):
        enqueue(session, "eval_run", {"set_id": str(ref), "trigger": "reindex", "judge": True})
