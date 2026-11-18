"""ACL half of the chunk index (split from search_index.py): the ACL hash, the painless sync script and the
`update_by_query` helpers every mutation (ACL, tags, title, labels) goes through. Re-exported by `search_index`."""

import hashlib
import json
import logging
from collections.abc import Callable
from typing import Any
from uuid import UUID

from elasticsearch import Elasticsearch, NotFoundError

from roracle.config import AliasConfig
from roracle.search_fields import sort_key

READ_ALIAS = AliasConfig().read_alias
MUTATE_ALIAS = AliasConfig().mutate_alias  # same value search_index exposes (both or neither override)


MAX_SYNC_TRIES = 3


def run_update_by_query(es: Elasticsearch, build: "Callable[[], dict[str, Any]]") -> dict[str, Any]:
    """update_by_query with response checks. `build` recomputes the kwargs from Postgres on each try:
    non-empty `failures` raise; `version_conflicts` retry (concurrent writer), then raise.
    A missing index is a no-op (nothing ingested yet)."""
    for _ in range(MAX_SYNC_TRIES):
        try:
            r = es.update_by_query(**build(), refresh=True, conflicts="proceed") or {}
        except NotFoundError:
            return {}
        if r.get("failures"):
            raise RuntimeError(f"update_by_query failures: {r['failures'][:1]}")
        if not r.get("version_conflicts"):
            return r
    raise RuntimeError("update_by_query version conflicts persisted")


ACL_KEYS = (
    "owner_id", "visibility", "org_unit", "share_users", "share_groups", "coauthor_users", "label_id",
)  # fmt: skip
ACL_BATCH = 500  # doc ids per update_by_query


def acl_hash(acl: dict[str, Any], title: str | None) -> str:
    """Digest of everything `acl_sync` writes (ACL fields, tags, title): equal PG and ES hashes mean no drift.
    `acl` carries `tags` (read_acl); lists are order-insensitive."""
    parts = [acl.get(k) for k in ACL_KEYS]
    parts = [sorted(v) if isinstance(v, list) else v for v in parts]
    parts += [sorted(acl.get("tags") or []), title]
    return hashlib.sha256(json.dumps(parts, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


def acl_payload(acl: dict[str, Any], title: str | None) -> dict[str, Any]:
    """Fields the script writes for one document. Without title or tags the hash cannot be recomputed, so the
    stale one is dropped (`drop_hash`): the reconcile then re-syncs the document in full."""
    fields = {k: acl.get(k) for k in ACL_KEYS}
    if title is not None:
        fields["title"] = title
        if (
            title != "none"
        ):  # read_acl's placeholder for a title-less doc: keep ingest's key (meta title/filename)
            fields["title_sort"] = sort_key(title)  # a rename re-keys the title sort too
    if acl.get("tags") is not None:
        fields["tags"] = acl["tags"]
    full = title is not None and acl.get("tags") is not None
    if full:
        fields["acl_hash"] = acl_hash(acl, title)
    return {"fields": fields, "drop_hash": not full}


# Compares before writing: an unchanged document is a `noop` (no reindex, no vector re-insert into HNSW).
_ACL_SCRIPT = (
    "def f = params.acl[ctx._source.doc_id]; if (f == null) { ctx.op = 'noop'; return; } "
    "boolean changed = false; "
    "for (def e : f.fields.entrySet()) { "
    "if (!Objects.equals(ctx._source[e.getKey()], e.getValue())) { ctx._source[e.getKey()] = e.getValue(); "
    "changed = true; } } "
    "if (f.drop_hash == true && ctx._source.containsKey('acl_hash')) { ctx._source.remove('acl_hash'); "
    "changed = true; } "
    "if (!changed) { ctx.op = 'noop'; }"
)


def _acl_request(items: dict[str, tuple[dict[str, Any], str | None]]) -> dict[str, Any]:
    return {
        "index": MUTATE_ALIAS,
        "query": {"terms": {"doc_id": list(items)}},
        "script": {
            "source": _ACL_SCRIPT,
            "lang": "painless",
            "params": {"acl": {d: acl_payload(a, t) for d, (a, t) in items.items()}},
        },
    }


_mapping_ensured = False


def _ensure_acl_mapping(es: Elasticsearch) -> None:
    """Once per process, before any ACL script write: `label_id` and `acl_hash` must be keywords on EVERY index of
    `chunks-all`, or dynamic mapping makes them `text` and the `must_not terms label_id` gate (PAT block_agents)
    silently never matches. Idempotent; a failure is logged and retried on the next sync."""
    global _mapping_ensured
    if _mapping_ensured:
        return
    try:
        kw = {"type": "keyword"}
        es.indices.put_mapping(index=MUTATE_ALIAS, properties={"label_id": kw, "acl_hash": kw})
        _mapping_ensured = True
    except NotFoundError:
        pass  # nothing indexed yet: the first index is created with the right mapping
    except Exception:
        logging.getLogger(__name__).warning("could not ensure the label_id/acl_hash mapping", exc_info=True)


def sync_acl_many(
    es: Elasticsearch,
    doc_ids: "list[str]",
    compute: "Callable[[list[str]], dict[str, tuple[dict[str, Any], str | None]]]",
) -> int:
    """Converge ACL/title/tags of many documents, `ACL_BATCH` per update_by_query; unchanged documents are
    no-ops. `compute(ids)` re-reads Postgres on EVERY try (documents it omits are skipped), so a concurrent
    revocation is never overwritten with data read earlier. Returns the number of chunks actually updated."""
    _ensure_acl_mapping(es)
    updated = 0
    for i in range(0, len(doc_ids), ACL_BATCH):
        ids = doc_ids[i : i + ACL_BATCH]
        updated += (run_update_by_query(es, lambda ids=ids: _acl_request(compute(ids))) or {}).get(
            "updated", 0
        )
    return updated


def sync_acl(
    es: Elasticsearch, doc_id, compute: "Callable[[], tuple[dict[str, Any], str | None] | None]"
) -> None:
    """Push ACL fields (and title / tags when given) to every chunk of the document; `compute` re-reads PG
    each try and returns (acl, title), or None when the document is gone."""

    def build() -> dict[str, Any]:
        got = compute()
        if got is None:
            return {"index": MUTATE_ALIAS, "query": {"match_none": {}}, "script": {"source": "return"}}
        return _acl_request({str(doc_id): got})

    _ensure_acl_mapping(es)
    run_update_by_query(es, build)


def delete_doc_chunks(es: Elasticsearch, doc_id: UUID) -> None:
    """Remove every chunk and summary unit of the document from the index; raises on failure.
    A 404 is a no-op only when no chunk index exists at all (a missing mutate alias beside a live
    index would orphan chunks)."""
    try:
        r = es.delete_by_query(
            index=MUTATE_ALIAS, query={"term": {"doc_id": str(doc_id)}}, refresh=True, conflicts="proceed"
        )
        if r and r.get("failures"):
            raise RuntimeError(f"delete_by_query failures: {r['failures'][:1]}")
    except NotFoundError:
        if es.indices.exists_alias(name=MUTATE_ALIAS) or es.indices.exists(index=READ_ALIAS):
            raise
