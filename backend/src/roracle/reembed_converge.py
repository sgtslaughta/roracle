"""Re-embed convergence: make the target generation agree with Postgres before readers move (split from reembed.py).

`_verify` converges one copied page; `converge_step` is the pre-swap pass over the whole corpus, RESUMABLE: it works
for at most `SLICE_S`, then returns a checkpoint (`stage` + keyset cursor) that the `converge` phase job carries into
its continuation, so a worker restart (scale-down, rollout, drain release) loses at most one slice of work."""

import logging
import time
import uuid

from sqlalchemy import func, select

from roracle import search_index
from roracle.acl_sync import drifted, read_acl_many
from roracle.models import Document, DocumentVersion
from roracle.queue import check_lease

log = logging.getLogger(__name__)
RECONCILE_BATCH = 500  # documents per drift query in the pre-swap convergence
SLICE_S = 60.0  # one converge job runs at most this long, then hands over a checkpoint


def _uuid(v: str):
    try:
        return uuid.UUID(v)
    except ValueError:
        return None


def _pg_state(sf, srcs: list[dict]) -> tuple[dict, set, dict]:
    """Postgres (authority), read fresh: per-doc (acl+tags, title) or None, live version ids, max searchable version/doc.
    The ACL read is batched (4 queries for all documents of the page, identical to `read_acl` per document)."""
    dids = {x["doc_id"] for x in srcs}
    uuids = {d: u for d in dids if (u := _uuid(d))}
    with sf() as s0:
        many = read_acl_many(s0, list(uuids.values()))
    acls = {d: many.get(str(uuids[d])) if d in uuids else None for d in dids}
    vids = {u for x in srcs if (u := _uuid(x["version_id"]))}
    with sf() as s:
        live = set(s.scalars(select(DocumentVersion.id).where(DocumentVersion.id.in_(vids))))
        newest = dict(
            s.execute(
                select(DocumentVersion.document_id, func.max(DocumentVersion.version))
                .where(
                    DocumentVersion.document_id.in_(list(uuids.values())),
                    DocumentVersion.stage == "searchable",
                )
                .group_by(DocumentVersion.document_id)
            ).all()
        )
    return acls, live, {str(k): v for k, v in newest.items()}


def _dbq(es, index: str, query: dict) -> None:
    es.delete_by_query(index=index, query=query, refresh=True, conflicts="proceed")


def _verify(es, sf, index: str, source: str, hits: list[dict], acls: dict) -> None:
    """Converge what changed between our reads and our write (target-only resurrection, stale ACL).
    The target refreshes every COPY_REFRESH, so it is refreshed here ONLY when something has to be corrected:
    update/delete_by_query only see refreshed chunks. What lands later is converged by `converge_step` before the swap."""
    srcs = [h["_source"] for h in hits]
    ids = [h["_id"] for h in hits]
    got = es.search(index=source, query={"ids": {"values": ids}}, source=False, size=len(ids))
    still = {h["_id"] for h in got["hits"]["hits"]}
    fixes: list = []
    stale: list[str] = []
    if gone := [i for i in ids if i not in still]:  # deleted / stale chunk removed while we copied
        fixes.append(lambda: _dbq(es, index, {"ids": {"values": gone}}))
    now, live, newest = _pg_state(sf, srcs)
    for doc_id, was in acls.items():
        if now[doc_id] is None:
            fixes.append(lambda doc_id=doc_id: _dbq(es, index, {"term": {"doc_id": doc_id}}))
        elif now[doc_id] != was:
            stale.append(doc_id)
    for vid in {x["version_id"] for x in srcs if _uuid(x["version_id"]) not in live}:
        fixes.append(lambda vid=vid: _dbq(es, index, {"term": {"version_id": vid}}))
    for doc_id, n in newest.items():
        if any(x["doc_id"] == doc_id and x.get("is_latest") and x.get("version", 0) < n for x in srcs):
            fixes.append(lambda doc_id=doc_id, n=n: search_index.mark_previous_versions(es, doc_id, n))
    if stale:
        fixes.append(lambda: search_index.sync_acl_many(es, stale, _acl_compute(sf)))
    if fixes:
        es.indices.refresh(index=index)
        for fix in fixes:
            fix()


def _page(sf, stmt, after, size: int = RECONCILE_BATCH) -> list:
    """One keyset page of a one-column id select, ordered by id, strictly after `after` (a UUID or None)."""
    col = stmt.selected_columns[0]
    with sf() as s:
        q = stmt if after is None else stmt.where(col > after)
        return list(s.scalars(q.order_by(col).limit(size)))


def _acl_compute(sf):
    def compute(ids: list[str]) -> dict:
        with sf() as s:
            return read_acl_many(s, [u for i in ids if (u := _uuid(i))])

    return compute


def _purge_page(es, sf, idx: str, field: str, model, after) -> dict | None:
    """One composite page of target ids (`field`) with no Postgres row (purged while we copied) -> deleted.
    Returns the next composite `after_key` (None when the aggregation is exhausted)."""
    comp: dict = {"size": 1000, "sources": [{"d": {"terms": {"field": field}}}]}
    if after:
        comp["after"] = after
    agg = es.search(index=idx, size=0, aggs={"docs": {"composite": comp}})["aggregations"]["docs"]
    keys = [b["key"]["d"] for b in agg["buckets"]]
    if keys:
        uu = [u for k in keys if (u := _uuid(k))]
        with sf() as s:
            have = {str(i) for i in s.scalars(select(model.id).where(model.id.in_(uu)))}
        if gone := [k for k in keys if k not in have]:
            _dbq(es, idx, {"terms": {field: gone}})
    return agg.get("after_key") if len(keys) >= 1000 else None


STAGES = ("drift", "deleted", "superseded", "orphan_docs", "orphan_versions")


def converge_step(es, sf, target: dict, state: dict | None = None, slice_s: float = SLICE_S) -> dict | None:
    """Right before the swap, with the target refreshed: make it agree with Postgres about everything that changed
    while it was being copied (mutations only reach refreshed chunks). Clock-free, resumable. Stages:
    drift          ACL/tags/title drift of every live document (acl_hash vs Postgres) -> `sync_acl_many`
    deleted        target chunks of EVERY soft-deleted, not yet purged document are deleted
    superseded     target chunks still `is_latest` whose Postgres version is not the latest are demoted by version id
    orphan_docs / orphan_versions   target ids that no longer exist in Postgres (hard purge raced the copy)
    Works for at most `slice_s`; returns the checkpoint `{"stage", "after"}` to continue from, or None when done."""
    idx = target["index"]
    st = state or {"stage": STAGES[0], "after": None}
    deadline = time.monotonic() + slice_s
    sup = select(DocumentVersion.id).where(
        DocumentVersion.stage == "searchable", DocumentVersion.is_latest.is_(False)
    )
    while st["stage"] in STAGES:
        stage, after = st["stage"], st["after"]
        while True:
            check_lease()
            if stage in ("orphan_docs", "orphan_versions"):
                field, model = (
                    ("doc_id", Document) if stage == "orphan_docs" else ("version_id", DocumentVersion)
                )
                nxt = _purge_page(es, sf, idx, field, model, after)
                done = nxt is None
            else:
                a = uuid.UUID(after) if after else None
                if stage == "drift":
                    ids = _page(sf, select(Document.id).where(Document.deleted_at.is_(None)), a)
                    with sf() as s:
                        items = read_acl_many(s, ids)
                    if bad := sorted(drifted(es, items, index=idx)):
                        search_index.sync_acl_many(es, bad, _acl_compute(sf))
                elif stage == "deleted":
                    ids = _page(sf, select(Document.id).where(Document.deleted_at.is_not(None)), a)
                    if ids:
                        _dbq(es, idx, {"terms": {"doc_id": [str(i) for i in ids]}})
                else:
                    ids = _page(sf, sup, a)
                    if ids:
                        es.update_by_query(
                            index=idx,
                            query={"bool": {"filter": [{"terms": {"version_id": [str(i) for i in ids]}},
                                                       {"term": {"is_latest": True}}]}},
                            script={"source": "ctx._source.is_latest = false", "lang": "painless"},
                            refresh=False,
                            conflicts="proceed",
                        )  # fmt: skip
                done = len(ids) < RECONCILE_BATCH
                nxt = str(ids[-1]) if ids else None
            if done:
                break
            after = nxt
            if time.monotonic() >= deadline:
                return {"stage": stage, "after": after}
        if stage == "superseded":
            es.indices.refresh(index=idx)  # one refresh for all the demotion pages
        nxt_stage = STAGES.index(stage) + 1
        st = {"stage": STAGES[nxt_stage] if nxt_stage < len(STAGES) else "done", "after": None}
        if time.monotonic() >= deadline and st["stage"] != "done":
            return st
    return None
