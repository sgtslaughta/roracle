"""Embedding-model switch: background re-embed into a new index generation, atomic alias cutover, catch-up.

Reads never see two generations: kNN/BM25 target ONE index named by the persisted active model (R4) and the
read alias `chunks` points at exactly one index. Every mutation (ACL, tags, deletes, is_latest) goes through
`chunks-all`, so it reaches the old and the new generation. Phases (one batch per job, self re-enqueueing):

copy     scan the active index by `chunk_id` (search_after), re-embed, bulk (refresh=wait_for) into the target.
         ACL/tags/title come from a Postgres read taken AFTER the ES read (never copied from the source);
         deleted docs / purged versions are skipped. After the write the batch is re-verified against
         Postgres and the source: deletes, ACL/tag changes, stale chunks and is_latest that raced the copy are
         converged (later mutations reach the target themselves through `chunks-all`)
converge  resumable pre-swap convergence with Postgres (reembed_converge.py), one slice per job
cutover  slow cleanup first, then under an advisory lock: persist the active model + ONE `update_aliases` swap of
         `chunks` (the last ES step; old stays in chunks-all). ensure_index never moves `chunks` off the PG-active index
catchup  twice, 300 s apart: copy chunks present in the old index but missing in the target (ingest races)
"""

import logging
import re
from typing import Any

from elasticsearch import Elasticsearch, NotFoundError, helpers
from sqlalchemy import select, text
from sqlalchemy.orm import Session, sessionmaker

from roracle import runtime, search_index
from roracle.config import Settings
from roracle.ingest.embed import BATCH_RETRIES, chunk_prompt, doc_prompt, embed, prompt_sha
from roracle.ingest.state import clear_summary_embeddings
from roracle.models import DocumentVersion, Job
from roracle.models_eval import EvalSet
from roracle.queue import NonRetryable, check_lease, enqueue
from roracle.reembed_converge import RECONCILE_BATCH, _dbq, _pg_state, _uuid, _verify, converge_step  # noqa: F401
from roracle.search_index import MUTATE_ALIAS

log = logging.getLogger(__name__)

BATCH = 1000  # chunks per job page: embedding-bound, not job-bound
HEX = "0123456789abcdef"
CATCHUP_DELAY_S = 300
COPY_REFRESH = "30s"  # target refresh interval while copying
_VECTORS = ["embedding", "summary_embedding"]


LOCK = "SELECT pg_advisory_xact_lock(hashtextextended('reembed', 0))"


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
    session.execute(text(LOCK))
    if running_job(session) is not None or session.scalar(
        select(Job.id).where(Job.kind == "rebuild_units", Job.status.in_(("queued", "running"))).limit(1)
    ):  # a rebuild rewriting the old index would be lost by catch-up (copies only missing chunks)
        raise AlreadyRunning
    eff = runtime.effective(session, settings)
    active = search_index.active_index(eff)
    prefix = f"chunks-{search_index.slug(model)}-"
    used = _alias_indices(es, MUTATE_ALIAS) | {active}
    gens = [int(n[len(prefix) :]) for n in used if re.fullmatch(re.escape(prefix) + r"\d+", n)]
    target = {"model": model, "dims": dims, "index": search_index.index_name(model, max(gens, default=0) + 1)}
    n = max(1, min(16, settings.reembed_slices))
    ids = [
        enqueue(
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
                "slice": {"i": i, "n": n},
            },
        )
        for i in range(n)
    ]
    return ids[0]


def status(session: Session, es: Elasticsearch, settings: Settings) -> dict[str, Any]:
    eff = runtime.effective(session, settings)
    jobs = list(
        session.scalars(select(Job).where(Job.kind == "reembed", Job.status.in_(("queued", "running"))))
    )
    running = None
    if jobs:  # one chain per slice (the newest job of each): progress is the sum
        newest: dict[int, Job] = {}
        for j in sorted(jobs, key=lambda j: j.id):
            newest[(j.payload.get("slice") or {"i": 0})["i"]] = j
        ps = [j.payload for j in newest.values()]
        totals = [p["total"] for p in ps]
        running = {
            "target": ps[0]["target"],
            "copied": sum(p["copied"] for p in ps),
            "total": None if any(t is None for t in totals) else sum(totals),
            "phase": ps[-1]["phase"],
            "slices": len(ps),
        }
    return {
        "active": {"index": search_index.active_index(eff), "model": eff.embed_model, "dims": eff.embed_dims},
        "running": running,
        "failed": _failed_pass(session, eff),
        "old": _old(session, es, settings),
    }


def _failed_pass(session: Session, eff: Settings) -> dict | None:
    """The newest re-embed pass that ended with a permanently failed slice (its cutover was refused), while nothing is
    running and its target is not the active index: {target, slices: [{i, error}]}. Start a new re-embed to retry."""
    if running_job(session) is not None:
        return None
    last = session.scalar(
        select(Job).where(Job.kind == "reembed", Job.status == "failed").order_by(Job.id.desc())
    )
    if last is None or last.payload["target"]["index"] == search_index.active_index(eff):
        return None
    tgt = last.payload["target"]["index"]
    bad = session.scalars(
        select(Job)
        .where(Job.kind == "reembed", Job.status == "failed", Job.payload["target"]["index"].astext == tgt)
        .order_by(Job.id)
    )
    return {
        "target": last.payload["target"],
        "slices": [{"i": (j.payload.get("slice") or {"i": 0})["i"], "error": j.last_error} for j in bad],
    }


class AliasMismatch(Exception):
    """Read alias does not point at exactly the active index (e.g. a cutover that failed after the swap)."""


def _old(session: Session, es: Elasticsearch, settings: Settings) -> list[str]:
    active = search_index.active_index(runtime.effective(session, settings))
    return sorted(_alias_indices(es, MUTATE_ALIAS) - {active})


def drop_old(session: Session, es: Elasticsearch, settings: Settings) -> list[str]:
    """Delete every generation in `chunks-all` except the active one (Postgres = authority). Refuses unless the
    read alias points at exactly the active index: otherwise 'old' could be the live index."""
    active = search_index.active_index(runtime.effective(session, settings))
    if _alias_indices(es, settings.read_alias) != {active}:
        raise AliasMismatch
    old = _old(session, es, settings)
    if old:
        es.indices.delete(index=old)
    return old


def _ensure_target(
    es: Elasticsearch, target: dict, settings: Settings | None = None, bulk: bool = False
) -> None:
    """Create the target inside `chunks-all` (mutations reach it at once). `bulk`: for the copy phase it refreshes every
    COPY_REFRESH instead of the configured interval, so mutations (ACL, deletes, is_latest) see copied chunks within
    that long. Replicas stay configured (the copy is embedding-bound; a red target would stall every mutation through
    `chunks-all`). `_restore_target` puts the configured interval back before cutover."""
    if not es.indices.exists(index=target["index"]):
        st = search_index.index_settings(settings)
        if bulk:
            st["index"].update(refresh_interval=COPY_REFRESH)
        es.indices.create(
            index=target["index"],
            mappings=search_index._mapping(target["dims"], settings),
            settings=st,
            aliases={MUTATE_ALIAS: {}},
        )


def _restore_target(es: Elasticsearch, target: dict, settings: Settings) -> None:
    """Idempotent: configured refresh interval and replicas back on the target, one refresh so every copied chunk is
    searchable, and a best-effort (asynchronous) merge down to a few segments."""
    st = search_index.index_settings(settings)["index"]
    es.indices.put_settings(
        index=target["index"],
        settings={
            "index": {
                "refresh_interval": st["refresh_interval"],
                "number_of_replicas": st["number_of_replicas"],
            }
        },
    )
    es.indices.refresh(index=target["index"])
    try:
        es.indices.forcemerge(index=target["index"], max_num_segments=5, wait_for_completion=False)
    except Exception:
        log.warning("force-merge of %s not started", target["index"], exc_info=True)


def _copy(es, llm, sf, hits: list[dict], target: dict, source: str, settings: Settings | None = None) -> None:
    acls, live, newest = _pg_state(
        sf, [h["_source"] for h in hits]
    )  # AFTER the ES read: ACL never from source
    hits = [
        h for h in hits
        if acls[h["_source"]["doc_id"]] is not None and _uuid(h["_source"]["version_id"]) in live
    ]  # fmt: skip
    if not hits:
        return
    srcs = [h["_source"] for h in hits]
    with sf() as s:
        summaries = dict(
            s.execute(
                select(DocumentVersion.id, DocumentVersion.summary).where(
                    DocumentVersion.id.in_([_uuid(x["version_id"]) for x in srcs])
                )
            ).all()
        )
    prompts = [
        doc_prompt(x["title"], x["text"])  # summary unit: same prompt as pipeline._index_units
        if x.get("unit")
        else chunk_prompt(
            x["title"],
            "\n".join([*(x.get("headings") or []), x["text"]]),  # = Chunk.contextual
            summaries.get(_uuid(x["version_id"])),
            x.get("context"),  # copied from the source chunk: the note is never regenerated here
        )
        for x in srcs
    ]
    conc = settings.embed_concurrency if settings is not None else 1
    sums = {
        i: x
        for i, x in enumerate(srcs)
        if x["chunk_id"].endswith(":0")
        and not x.get("unit")
        and (summaries.get(_uuid(x["version_id"])) or "").strip()
    }
    sum_prompts = [doc_prompt(x["title"], summaries[_uuid(x["version_id"])]) for x in sums.values()]
    # chunk and summary prompts go out as ONE stream of requests, `embed_concurrency` in flight
    allv = embed(
        llm,
        target["model"],
        [*prompts, *sum_prompts],
        target["dims"],
        concurrency=conc,
        retries=BATCH_RETRIES,
    )
    vectors, svecs = allv[: len(prompts)], allv[len(prompts) :]
    sv = dict(zip(sums, svecs, strict=True))
    check_lease()  # last cooperative checkpoint before the page is written
    actions = []
    for i, (h, x, v, p) in enumerate(zip(hits, srcs, vectors, prompts, strict=True)):
        acl, title = acls[x["doc_id"]]
        doc = {k: val for k, val in x.items() if k not in _VECTORS}
        doc.update(acl, title=title)  # ACL/tags/title from the fresh Postgres read, never from the source
        doc["acl_hash"] = search_index.acl_hash(acl, title)  # describes exactly the fields written above
        doc["is_latest"] = bool(x.get("is_latest")) and x.get("version", 0) >= newest.get(x["doc_id"], 0)
        doc.update(embedding=v, embedding_model=target["model"], prompt_sha=prompt_sha(p))
        if i in sv:
            doc["summary_embedding"] = sv[i]
        actions.append({"_op_type": "index", "_index": target["index"], "_id": h["_id"], "_source": doc})
    # no explicit refresh: the target refreshes every COPY_REFRESH (and the cutover once more)
    _, errors = helpers.bulk(es, actions, refresh=False, raise_on_error=False)
    if errors:
        raise RuntimeError(f"re-embed bulk failed for {len(errors)} chunks: {errors[0]}")
    _verify(es, sf, target["index"], source, hits, acls)


def _slice_query(sl: dict) -> dict | None:
    """chunk_id starts with the version uuid (uniform hex): slice i of n = a range of the first hex digit."""
    i, n = sl["i"], sl["n"]
    if n <= 1:
        return None
    lo, hi = 16 * i // n, 16 * (i + 1) // n
    rng: dict = {"gte": HEX[lo]} if i else {}
    if i < n - 1:
        rng["lt"] = HEX[hi]
    return {"range": {"chunk_id": rng}}


def _last_slice(session: Session, payload: dict) -> bool:
    """True when no OTHER slice of this re-embed pass is still queued/running (this slice finished last). Serialised
    by an advisory lock held until this job's transaction (which also completes this job) commits, so two slices
    finishing together cannot both see the other as active."""
    sl = payload.get("slice") or {"i": 0, "n": 1}
    if sl["n"] <= 1:
        return True
    session.execute(text(LOCK))
    other = session.scalar(
        select(Job.id)
        .where(
            Job.kind == "reembed",
            Job.status.in_(("queued", "running")),
            Job.payload["target"]["index"].astext == payload["target"]["index"],
            Job.payload["phase"].astext.in_(("copy", "catchup")),
            Job.payload["slice"]["i"].astext != str(sl["i"]),
        )
        .limit(1)
    )
    return other is None


def _pass_has_failed_slice(session: Session, payload: dict) -> bool:
    """A copy slice of THIS pass (the target index is unique per start) ended `failed`: its range is missing from the
    target, so the read alias must never move to it."""
    return (
        session.scalar(
            select(Job.id)
            .where(
                Job.kind == "reembed",
                Job.status == "failed",
                Job.payload["target"]["index"].astext == payload["target"]["index"],
                Job.payload["phase"].astext == "copy",
            )
            .limit(1)
        )
        is not None
    )


def _scan(session: Session, payload: dict, es, llm, sf, settings: Settings) -> None:
    phase, target, src = payload["phase"], payload["target"], payload["source"]
    sl = payload.get("slice") or {"i": 0, "n": 1}
    if phase == "catchup" and _alias_indices(es, settings.read_alias) != {target["index"]}:
        raise AliasMismatch  # read alias drifted after cutover: fail loudly, never copy blindly
    check_lease()
    _ensure_target(es, target, settings, bulk=phase == "copy")
    sq = _slice_query(sl)
    if payload["total"] is None:
        payload["total"] = es.count(index=src, **({"query": sq} if sq else {}))["count"]
    cursor = payload["cursor"]
    page = {"query": sq} if sq else {}
    hits = es.search(
        index=src,
        size=BATCH,
        sort=[{"chunk_id": "asc"}],
        search_after=[cursor] if cursor else None,
        source_excludes=_VECTORS,
        **page,
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
        _copy(es, llm, sf, todo, target, src, settings)
    if phase == "copy":
        payload["copied"] += len(todo)
    if len(hits) == BATCH:
        enqueue(session, "reembed", {**payload, "cursor": hits[-1]["sort"][0]})
        return
    last = _last_slice(session, payload)  # this slice is done; the pass is done when the others are too
    if last and phase == "copy" and _pass_has_failed_slice(session, payload):
        log.error("re-embed into %s: a slice failed permanently, cutover refused", target["index"])
        _restore_target(es, target, settings)  # leave the abandoned target in normal shape
        return
    if last:
        enqueue(
            session, "reconcile_tags", {}
        )  # end of every pass (before cutover / after each catch-up round)
    if phase == "copy":
        if last:
            enqueue(
                session,
                "reembed",
                {
                    **payload,
                    "phase": "converge",
                    "cursor": None,
                    "conv": None,
                    "slice": {"i": 0, "n": sl["n"]},
                },
            )
    elif payload["round"] == 1:
        enqueue(session, "reembed", {**payload, "cursor": None, "round": 2}, run_after_s=CATCHUP_DELAY_S)
    elif last:  # the settled index: the final catch-up round of the last slice triggers the auto eval
        _enqueue_eval(session, settings)


def _enqueue_eval(session: Session, settings: Settings) -> None:
    ref = session.scalar(select(EvalSet.id).where(EvalSet.is_reference.is_(True)))
    if settings.eval_on_reindex and ref is not None:
        enqueue(session, "eval_run", {"set_id": str(ref), "trigger": "reindex", "judge": True})


def _converge_job(session: Session, payload: dict, es, settings: Settings, sf) -> None:
    """One resumable slice of the pre-swap convergence (`converge_step`); continues itself with the checkpoint, then
    hands over to the cutover. The target is back at its normal refresh interval from the first slice on, so what
    changes meanwhile reaches it through `chunks-all`."""
    if _pass_has_failed_slice(session, payload):
        raise NonRetryable("a copy slice failed permanently; cutover refused")
    if payload.get("conv") is None:  # first slice: normal refresh/replicas back, one refresh, async merge
        _restore_target(es, payload["target"], settings)
    nxt = converge_step(es, sf, payload["target"], payload.get("conv"))
    if nxt is not None:
        enqueue(session, "reembed", {**payload, "conv": nxt})
    else:
        enqueue(session, "reembed", {**payload, "phase": "cutover", "conv": None})


def _cutover(session: Session, payload: dict, es, settings: Settings, sf) -> None:
    target, src, alias = payload["target"], payload["source"], settings.read_alias
    if _pass_has_failed_slice(session, payload):  # belt and braces: never swap onto an incomplete index
        raise NonRetryable("a copy slice failed permanently; cutover refused")
    before = runtime.effective(session, settings)
    if (before.embed_model, before.embed_dims) != (target["model"], target["dims"]):
        clear_summary_embeddings(sf)  # slow, derived cache of the old model: BEFORE the swap (R2-I)
    session.execute(text(search_index.ALIAS_LOCK))  # until job commit: ensure_index waits, then reads PG
    runtime.set_active_embedding(session, target["model"], target["dims"], target["index"])
    if _alias_indices(es, alias) != {target["index"]}:  # last ES step; idempotent on a re-run after a crash
        es.indices.update_aliases(
            actions=[
                {"remove": {"index": src, "alias": alias}},
                {"add": {"index": target["index"], "alias": alias}},
            ]
        )
    enqueue(session, "reconcile_tags", {})  # converge ACL/tags of every document from Postgres
    for i in range(payload["slice"]["n"]):  # catch-up runs per slice too
        enqueue(
            session,
            "reembed",
            {
                **payload,
                "phase": "catchup",
                "cursor": None,
                "round": 1,
                "total": None,
                "copied": 0,
                "slice": {"i": i, "n": payload["slice"]["n"]},
            },
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
    if payload["phase"] == "converge":
        _converge_job(session, payload, es, settings, session_factory)
    elif payload["phase"] == "cutover":
        _cutover(session, payload, es, settings, session_factory)
    else:
        try:
            _scan(session, payload, es, llm, session_factory, settings)
        except NonRetryable:
            if (
                payload["phase"] == "copy"
            ):  # permanent failure: tidy the abandoned target (it never lost its replicas, only a 30 s refresh)
                _restore_target(es, payload["target"], settings)
            raise
