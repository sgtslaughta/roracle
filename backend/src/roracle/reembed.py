"""Embedding-model switch: background re-embed into a new index generation, atomic alias cutover, catch-up.

Reads never see two generations: kNN/BM25 target ONE index named by the persisted active model (R4) and the
read alias `chunks` points at exactly one index. Every mutation (ACL, tags, deletes, is_latest) goes through
`chunks-all`, so it reaches the old and the new generation. Phases (one batch per job, self re-enqueueing):

copy     scan the active index by `chunk_id` (search_after), re-embed, bulk into the target; then re-check the
         batch's documents against Postgres (authority) and resync ACL/tags/title through `chunks-all` when
         they differ from what was copied (an update landing between our read and our write is not lost:
         an update after the write hits the target itself, one before it is seen by the Postgres re-read)
cutover  ONE `update_aliases` call swaps `chunks`, then the active model is persisted (old stays in chunks-all)
catchup  twice, 300 s apart: copy chunks present in the old index but missing in the target (ingest races)
"""

import logging
import re
import uuid
from typing import Any

from elasticsearch import Elasticsearch, NotFoundError, helpers
from sqlalchemy import select, text
from sqlalchemy.orm import Session, sessionmaker

from roracle import runtime, search_index
from roracle.acl_sync import read_acl
from roracle.config import Settings
from roracle.ingest.embed import doc_prompt, embed, prompt_sha
from roracle.ingest.state import clear_summary_embeddings
from roracle.models import DocumentVersion, Job
from roracle.queue import enqueue
from roracle.search_index import ALIAS, MUTATE_ALIAS

log = logging.getLogger(__name__)

BATCH = 200
CATCHUP_DELAY_S = 300
_VECTORS = ["embedding", "summary_embedding"]
_SYNCED = ("owner_id", "visibility", "org_unit", "share_users", "share_groups", "tags")


class AlreadyRunning(Exception):
    pass


def _alias_indices(es: Elasticsearch, alias: str) -> set[str]:
    try:
        return set(es.indices.get_alias(name=alias))
    except NotFoundError:
        return set()


def running_job(session: Session) -> Job | None:
    return session.scalar(
        select(Job)
        .where(Job.kind == "reembed", Job.status.in_(("queued", "running")))
        .order_by(Job.id.desc())
    )


def start(session: Session, es: Elasticsearch, settings: Settings, model: str, dims: int) -> int:
    """Enqueue the copy phase into a fresh generation of `model`. Raises AlreadyRunning."""
    session.execute(text("SELECT pg_advisory_xact_lock(hashtextextended('reembed', 0))"))
    if running_job(session) is not None:
        raise AlreadyRunning
    eff = runtime.effective(session, settings)
    active = search_index.active_index(eff)
    prefix = f"chunks-{search_index.slug(model)}-"
    used = _alias_indices(es, MUTATE_ALIAS) | {active}
    gens = [int(n[len(prefix) :]) for n in used if re.fullmatch(re.escape(prefix) + r"\d+", n)]
    target = {"model": model, "dims": dims, "index": search_index.index_name(model, max(gens, default=0) + 1)}
    return enqueue(
        session,
        "reembed",
        {
            "phase": "copy",
            "source": active,
            "target": target,
            "cursor": None,
            "copied": 0,
            "total": None,
            "round": 0,
        },
    )


def status(session: Session, es: Elasticsearch, settings: Settings) -> dict[str, Any]:
    eff = runtime.effective(session, settings)
    job = running_job(session)
    running = None
    if job is not None:
        p = job.payload
        running = {"target": p["target"], "copied": p["copied"], "total": p["total"], "phase": p["phase"]}
    read = _alias_indices(es, ALIAS)
    return {
        "active": {"index": search_index.active_index(eff), "model": eff.embed_model, "dims": eff.embed_dims},
        "running": running,
        "old": sorted(_alias_indices(es, MUTATE_ALIAS) - read),
    }


def drop_old(es: Elasticsearch) -> list[str]:
    """Delete every generation that is in `chunks-all` but not behind the read alias."""
    old = sorted(_alias_indices(es, MUTATE_ALIAS) - _alias_indices(es, ALIAS))
    if old:
        es.indices.delete(index=old)
    return old


def _ensure_target(es: Elasticsearch, target: dict) -> None:
    if not es.indices.exists(index=target["index"]):  # created inside chunks-all: mutations reach it at once
        es.indices.create(
            index=target["index"],
            mappings=search_index._mapping(target["dims"]),
            aliases={MUTATE_ALIAS: {}},
        )


def _copy(es, llm, sf, hits: list[dict], target: dict) -> None:
    vids = {h["_source"]["version_id"] for h in hits}
    with sf() as s:
        summaries = dict(
            s.execute(
                select(DocumentVersion.id, DocumentVersion.summary).where(
                    DocumentVersion.id.in_([u for v in vids if (u := _uuid(v))])
                )
            ).all()
        )
    srcs = [h["_source"] for h in hits]
    prompts = [
        doc_prompt(
            x["title"],
            "\n".join([*(x.get("headings") or []), x["text"]]),  # = Chunk.contextual
            summaries.get(_uuid(x["version_id"])),
        )
        for x in srcs
    ]
    vectors = embed(llm, target["model"], prompts, target["dims"])
    sums = {
        i: x
        for i, x in enumerate(srcs)
        if x["chunk_id"].endswith(":0") and (summaries.get(_uuid(x["version_id"])) or "").strip()
    }
    svecs = embed(
        llm,
        target["model"],
        [doc_prompt(x["title"], summaries[_uuid(x["version_id"])]) for x in sums.values()],
        target["dims"],
    )
    sv = dict(zip(sums, svecs, strict=True))
    actions = []
    for i, (h, x, v, p) in enumerate(zip(hits, srcs, vectors, prompts, strict=True)):
        doc = {k: val for k, val in x.items() if k not in _VECTORS}  # ACL fields copied verbatim
        doc.update(embedding=v, embedding_model=target["model"], prompt_sha=prompt_sha(p))
        if i in sv:
            doc["summary_embedding"] = sv[i]
        actions.append({"_op_type": "index", "_index": target["index"], "_id": h["_id"], "_source": doc})
    _, errors = helpers.bulk(es, actions, refresh=False, raise_on_error=False)
    if errors:
        raise RuntimeError(f"re-embed bulk failed for {len(errors)} chunks: {errors[0]}")
    _resync(es, sf, srcs)


def _uuid(v: str):
    try:
        return uuid.UUID(v)
    except ValueError:
        return None


def _resync(es, sf, srcs: list[dict]) -> None:
    """Postgres is the authority: if it differs from the ACL/tags/title we just copied, push it to every
    generation through `chunks-all` (same path as a user-triggered change)."""
    seen: dict[str, dict] = {}
    for x in srcs:
        seen.setdefault(x["doc_id"], x)
    for doc_id, x in seen.items():
        did = _uuid(doc_id)
        fresh = read_acl(sf, did) if did else None
        if fresh is None:
            continue
        acl, title = fresh
        if any(acl.get(k) != x.get(k) for k in _SYNCED) or title != x.get("title"):
            search_index.sync_acl(es, doc_id, lambda d=did: read_acl(sf, d))


def _scan(session: Session, payload: dict, es, llm, sf) -> None:
    phase, target, src = payload["phase"], payload["target"], payload["source"]
    _ensure_target(es, target)
    if payload["total"] is None:
        payload["total"] = es.count(index=src)["count"]
    cursor = payload["cursor"]
    hits = es.search(
        index=src,
        size=BATCH,
        sort=[{"chunk_id": "asc"}],
        search_after=[cursor] if cursor else None,
        source_excludes=_VECTORS,
    )["hits"]["hits"]
    todo = hits
    if phase == "catchup" and hits:
        have = {
            h["_id"]
            for h in es.search(
                index=target["index"], query={"ids": {"values": [h["_id"] for h in hits]}}, source=False,
                size=len(hits),
            )["hits"]["hits"]
        }  # fmt: skip
        todo = [h for h in hits if h["_id"] not in have]
    if todo:
        _copy(es, llm, sf, todo, target)
    if phase == "copy":
        payload["copied"] += len(todo)
    if len(hits) == BATCH:
        enqueue(session, "reembed", {**payload, "cursor": hits[-1]["sort"][0]})
    elif phase == "copy":
        enqueue(session, "reembed", {**payload, "phase": "cutover", "cursor": None})
    elif payload["round"] == 1:
        enqueue(session, "reembed", {**payload, "cursor": None, "round": 2}, run_after_s=CATCHUP_DELAY_S)


def _cutover(session: Session, payload: dict, es, settings: Settings, sf) -> None:
    target, src = payload["target"], payload["source"]
    if _alias_indices(es, ALIAS) != {target["index"]}:  # idempotent on a re-run after a crash
        es.indices.update_aliases(
            actions=[
                {"remove": {"index": src, "alias": ALIAS}},
                {"add": {"index": target["index"], "alias": ALIAS}},
            ]
        )
    before = runtime.effective(session, settings)
    runtime.set_active_embedding(session, target["model"], target["dims"], target["index"])
    if (before.embed_model, before.embed_dims) != (target["model"], target["dims"]):
        clear_summary_embeddings(sf)  # derived cache of the old model; lazy backfill on re-ingest
    enqueue(session, "reconcile_tags", {})  # converge ACL/tags of every document from Postgres
    enqueue(
        session,
        "reembed",
        {**payload, "phase": "catchup", "cursor": None, "round": 1},
        run_after_s=CATCHUP_DELAY_S,
    )


def handle_reembed(
    session: Session,
    payload: dict,
    *,
    es: Elasticsearch,
    llm,
    session_factory: sessionmaker[Session],
    settings: Settings,
) -> None:
    payload = dict(payload)
    if payload["phase"] == "cutover":
        _cutover(session, payload, es, settings, session_factory)
    else:
        _scan(session, payload, es, llm, session_factory)
