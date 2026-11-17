"""Elasticsearch chunk index: `chunks-<model-slug>-<gen>`; read alias `chunks` (one index), mutation alias
`chunks-all` (all live generations, ADR-0002 ACL fields)."""

import logging
import os
import re
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from elasticsearch import Elasticsearch, NotFoundError, helpers
from elasticsearch.exceptions import BadRequestError
from sqlalchemy import select, text
from sqlalchemy.orm import Session, sessionmaker

from roracle import runtime
from roracle.models import Document, DocumentCoauthor, DocumentShare, DocumentVersion
from roracle.queue import NonRetryable

if TYPE_CHECKING:  # runtime import would be circular (ingest/__init__ -> pipeline -> here)
    from roracle.ingest.chunking import Chunk
    from roracle.ingest.raptor import Unit

# ROR_READ_ALIAS / ROR_MUTATE_ALIAS isolate a scratch index (perf harness); the defaults are the real names
ALIAS = os.environ.get("ROR_READ_ALIAS") or "chunks"  # read alias: exactly one (the active) index
ALIAS_LOCK = "SELECT pg_advisory_xact_lock(hashtextextended('reembed-alias', 0))"  # cutover vs ensure_index
# every live generation: ALL update_by_query / delete_by_query go here
MUTATE_ALIAS = os.environ.get("ROR_MUTATE_ALIAS") or "chunks-all"


def slug(model: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", model.lower()).strip("-")


def index_name(model: str, gen: int = 1) -> str:
    return f"chunks-{slug(model)}-{gen}"


def active_index(settings: Any) -> str:
    """Index that kNN/BM25 reads and bulk writes target: persisted by re-embed cutover (R4), else the env default."""
    return settings.embed_index or index_name(settings.embed_model)


def _mapping(dims: int) -> dict[str, Any]:
    kw = {"type": "keyword"}
    vec = {
        "type": "dense_vector",
        "dims": dims,
        "index": True,
        "similarity": "cosine",
        "index_options": {"type": "int8_hnsw"},
    }
    return {
        "properties": {
            "doc_id": kw,
            "version_id": kw,
            "version": {"type": "integer"},
            "is_latest": {"type": "boolean"},
            "chunk_id": kw,
            "owner_id": kw,
            "visibility": kw,
            "org_unit": kw,
            "label_id": kw,
            "share_users": kw,
            "share_groups": kw,
            "coauthor_users": kw,
            "title": {"type": "text", "analyzer": "english"},
            "authors": kw,
            "tags": kw,
            "file_type": kw,
            "collections": kw,
            "year": {"type": "integer"},
            "text": {"type": "text", "analyzer": "english"},
            "headings": {"type": "text", "analyzer": "english"},
            "context": {"type": "text", "analyzer": "english"},
            "unit": kw,  # "section" | "document"; absent = ordinary chunk
            "source_chunks": kw,
            "pages": {"type": "integer"},
            "bboxes": {"type": "object", "enabled": False},
            "embedding_model": kw,
            "prompt_sha": kw,
            "embedding": vec,
            "summary_embedding": vec,
        }
    }


def _adopt_plain_index(es: Elasticsearch, name: str, dims: int, alias: str = ALIAS) -> None:
    """A concrete index called `chunks` (pre-alias deployment) blocks the read alias: copy it into `name`,
    then drop it and add both aliases in ONE atomic update_aliases. Idempotent (same `_id`s)."""
    if not es.indices.exists(index=alias) or es.indices.exists_alias(name=alias):
        return
    if not es.indices.exists(index=name):
        es.indices.create(index=name, mappings=_mapping(dims))
    r = (
        es.reindex(source={"index": alias}, dest={"index": name}, refresh=True, wait_for_completion=True)
        or {}
    )
    if r.get("failures"):
        raise RuntimeError(f"legacy reindex failed: {r['failures'][:1]}")
    es.indices.update_aliases(
        actions=[
            {"remove_index": {"index": alias}},
            {"add": {"index": name, "alias": alias}},
            {"add": {"index": name, "alias": MUTATE_ALIAS}},
        ]
    )


def _holders(es: Elasticsearch, alias: str) -> list[str]:
    try:
        return list(es.indices.get_alias(name=alias))
    except NotFoundError:
        return []


def _point_read_alias(
    es: Elasticsearch, name: str, alias: str, sf: sessionmaker[Session] | None, settings: Any
) -> None:
    """Make `alias` (exclusive) point at `name`. With `sf`, only when `name` is the active index persisted in
    Postgres, read fresh under the cutover's advisory lock: a stale worker never moves it back (S5)."""
    if es.indices.exists_alias(name=alias, index=name):
        return

    def move() -> None:
        es.indices.update_aliases(
            actions=[
                *({"remove": {"index": i, "alias": alias}} for i in _holders(es, alias)),
                {"add": {"index": name, "alias": alias}},
            ]
        )

    if sf is None:  # unguarded (tests/tools): add-only, never steal the alias from an existing holder
        if not _holders(es, alias):
            move()
        return
    with sf() as s:
        s.execute(text(ALIAS_LOCK))  # held until this session closes (rollback)
        if active_index(runtime.effective(s, settings)) == name:
            move()


def ensure_index(
    es: Elasticsearch,
    model: str,
    dims: int,
    alias: str = ALIAS,
    name: str | None = None,
    *,
    session_factory: sessionmaker[Session] | None = None,
    settings: Any = None,
    create: bool = True,
) -> str:
    """Create the index and add it to `chunks-all`. The read alias is (re)pointed here only when no
    `session_factory` is given (tests) or `name` is the Postgres-persisted active index."""
    name = name or index_name(model)
    _adopt_plain_index(es, name, dims, alias)
    if not create and not es.indices.exists(index=name):
        raise NonRetryable(f"index {name!r} no longer exists")  # shadow dropped mid-rebuild: never re-created
    if not es.indices.exists(index=name):
        try:
            es.indices.create(index=name, mappings=_mapping(dims))
        except BadRequestError as e:
            if e.error != "resource_already_exists_exception":
                raise
    props = es.indices.get_mapping(index=name)[name]["mappings"].get("properties", {})
    if (
        props.get("embedding", {}).get("dims") != dims
        or props.get("summary_embedding", {}).get("dims", dims) != dims
    ):
        raise NonRetryable("search index dimension mismatch: reindex required")
    missing = {
        f: t
        for f, t in _mapping(dims)["properties"].items()
        if f not in props and (t["type"] in ("keyword", "dense_vector") or f == "context")
    }
    if missing:  # fields added after the index was created (R9)
        es.indices.put_mapping(index=name, properties=missing)
    if not es.indices.exists_alias(name=MUTATE_ALIAS, index=name):
        es.indices.update_aliases(actions=[{"add": {"index": name, "alias": MUTATE_ALIAS}}])
    _point_read_alias(es, name, alias, session_factory, settings)
    return name


def acl_fields(session: Session, document: Document) -> dict[str, Any]:
    shares = session.execute(
        select(DocumentShare.principal_kind, DocumentShare.principal).where(
            DocumentShare.document_id == document.id
        )
    ).all()
    coauthors = session.scalars(
        select(DocumentCoauthor.user_id).where(DocumentCoauthor.document_id == document.id)
    )
    return {
        "owner_id": str(document.owner_id),
        "coauthor_users": sorted(str(u) for u in coauthors),
        "visibility": document.visibility,
        "org_unit": document.org_unit,
        "share_users": sorted(p for k, p in shares if k == "user"),
        "share_groups": sorted(p for k, p in shares if k == "group"),
        "label_id": str(document.label_id),
    }


def file_type(filename: str | None) -> str:
    """Lowercase extension without dot; "other" when absent."""
    ext = (filename or "").rsplit(".", 1)[1].lower() if "." in (filename or "") else ""
    return ext if re.fullmatch(r"[a-z0-9]{1,8}", ext) else "other"


def _base(version, document, acl, model, title, is_latest, tags) -> dict[str, Any]:
    """Fields shared by a document's chunks and summary units (parity: ACL, label, latest, tags)."""
    meta = version.meta or {}
    return {
        **acl,
        "doc_id": str(document.id),
        "version_id": str(version.id),
        "version": version.version,
        "is_latest": version.is_latest if is_latest is None else is_latest,
        "title": title,
        "authors": meta.get("authors") or [],
        "year": meta.get("year"),
        "tags": tags or [],
        "file_type": file_type(version.filename),
        "embedding_model": model,
    }


def index_chunks(
    es: Elasticsearch,
    version: DocumentVersion,
    document: Document,
    chunks: "list[Chunk]",
    vectors: list[list[float]],
    acl: dict[str, Any],
    model: str,
    *,
    title: str,
    is_latest: bool | None = None,
    tags: list[str] | None = None,
    summary_vector: list[float] | None = None,
    prompt_shas: list[str] | None = None,
    index: str | None = None,
    contexts: list[str] | None = None,
) -> int:
    """Bulk index with deterministic `_id` (re-runs overwrite); raise on any item error."""
    base = _base(version, document, acl, model, title, is_latest, tags)
    actions = [
        {
            "_op_type": "index",
            "_index": index or index_name(model),
            "_id": f"{version.id}:{c.n}",
            "_source": {
                **base,
                "chunk_id": f"{version.id}:{c.n}",
                "text": c.text,
                "headings": c.headings,
                "pages": c.pages,
                "bboxes": c.bboxes,
                "embedding": v,
                **({"prompt_sha": prompt_shas[i]} if prompt_shas else {}),
                **({"context": contexts[i]} if contexts and contexts[i] else {}),
                **({"summary_embedding": summary_vector} if c.n == 0 and summary_vector else {}),
            },
        }
        for i, (c, v) in enumerate(zip(chunks, vectors, strict=True))
    ]
    ok, errors = helpers.bulk(es, actions, refresh="wait_for", raise_on_error=False)
    if errors:
        raise RuntimeError(f"bulk indexing failed for {len(errors)} chunks: {errors[0]}")
    return ok


def index_units(
    es: Elasticsearch,
    version: DocumentVersion,
    document: Document,
    units: "list[Unit]",
    vectors: list[list[float]],
    acl: dict[str, Any],
    model: str,
    *,
    title: str,
    is_latest: bool | None = None,
    tags: list[str] | None = None,
    index: str | None = None,
    prompt_shas: list[str] | None = None,
) -> int:
    """Bulk index per-document summary units (same ACL/label fields as the document's chunks)."""
    base = _base(version, document, acl, model, title, is_latest, tags)
    actions = [
        {
            "_op_type": "index",
            "_index": index or index_name(model),
            "_id": f"{version.id}:{u.key}",
            "_source": {
                **base,
                "chunk_id": f"{version.id}:{u.key}",
                "unit": u.kind,
                "source_chunks": u.source_chunks,
                "text": u.text,
                "headings": u.headings,
                "pages": u.pages,
                "bboxes": [],
                "embedding": v,
                **({"prompt_sha": prompt_shas[i]} if prompt_shas else {}),
            },
        }
        for i, (u, v) in enumerate(zip(units, vectors, strict=True))
    ]
    ok, errors = helpers.bulk(es, actions, refresh="wait_for", raise_on_error=False)
    if errors:
        raise RuntimeError(f"bulk indexing failed for {len(errors)} units: {errors[0]}")
    return ok


def cached_vectors(
    es: Elasticsearch, model: str, doc_id, shas: list[str], dims: int, index: str | None = None
) -> dict[str, list[float]]:
    """Embeddings already indexed for this document under the same prompt hash. Scope is the one
    document only: cross-document reuse would leak content existence via timing."""
    out: dict[str, list[float]] = {}
    for i in range(0, len(shas), 1000):
        batch = shas[i : i + 1000]
        try:
            r = es.search(
                index=index or index_name(model),
                query={
                    "bool": {"filter": [{"term": {"doc_id": str(doc_id)}}, {"terms": {"prompt_sha": batch}}]}
                },
                source=["prompt_sha", "embedding"],
                collapse={"field": "prompt_sha"},
                size=10000,
            )
        except NotFoundError:
            return {}
        except BadRequestError as e:
            if "no mapping found" in str(e):
                return {}
            raise
        for h in r["hits"]["hits"]:
            src = h["_source"]
            vec = src.get("embedding")
            if vec and len(vec) == dims:
                out[src["prompt_sha"]] = vec
    return out


def delete_stale_chunks(
    es: Elasticsearch, version_id, keep: int, alias: str = MUTATE_ALIAS, keep_units: list[str] | None = None
) -> None:
    """Remove chunks of this version with n >= keep (a re-run may produce fewer chunks). Summary units are left
    alone unless `keep_units` is given: then ONE query also removes units not in it (raptor ingest)."""
    keep_ids = [f"{version_id}:{n}" for n in range(keep)]
    es.delete_by_query(
        index=alias,
        query={
            "bool": {
                "filter": [{"term": {"version_id": str(version_id)}}],
                "must_not": [
                    {"ids": {"values": keep_ids + (keep_units or [])}},
                    *([] if keep_units is not None else [{"exists": {"field": "unit"}}]),
                ],
            }
        },
        refresh=True,
        conflicts="proceed",
    )


def delete_stale_units(es: Elasticsearch, version_id, keep_ids: list[str], alias: str = MUTATE_ALIAS) -> None:
    """Remove summary units of this version that were not just (re)written (`keep_ids` = their chunk ids)."""
    es.delete_by_query(
        index=alias,
        query={
            "bool": {
                "filter": [{"term": {"version_id": str(version_id)}}, {"exists": {"field": "unit"}}],
                "must_not": [{"ids": {"values": keep_ids}}] if keep_ids else [],
            }
        },
        refresh=True,
        conflicts="proceed",
    )


def mark_previous_versions(es: Elasticsearch, doc_id, version: int, alias: str = MUTATE_ALIAS) -> None:
    """Flag every chunk of this document's versions older than `version` as not latest."""
    es.update_by_query(
        index=alias,
        query={
            "bool": {
                "filter": [{"term": {"doc_id": str(doc_id)}}, {"range": {"version": {"lt": version}}}],
            }
        },
        script={"source": "ctx._source.is_latest = false", "lang": "painless"},
        refresh=True,
        conflicts="proceed",
    )


MAX_SYNC_TRIES = 3


def run_update_by_query(es: Elasticsearch, build: "Callable[[], dict[str, Any]]") -> None:
    """update_by_query with response checks. `build` recomputes the kwargs from Postgres on each try:
    non-empty `failures` raise; `version_conflicts` retry (concurrent writer), then raise.
    A missing index is a no-op (nothing ingested yet)."""
    for _ in range(MAX_SYNC_TRIES):
        try:
            r = es.update_by_query(**build(), refresh=True, conflicts="proceed") or {}
        except NotFoundError:
            return
        if r.get("failures"):
            raise RuntimeError(f"update_by_query failures: {r['failures'][:1]}")
        if not r.get("version_conflicts"):
            return
    raise RuntimeError("update_by_query version conflicts persisted")


_ACL_SCRIPT = (
    "ctx._source.owner_id = params.owner_id; ctx._source.visibility = params.visibility; "
    "ctx._source.org_unit = params.org_unit; ctx._source.share_users = params.share_users; "
    "ctx._source.share_groups = params.share_groups; ctx._source.coauthor_users = params.coauthor_users; "
    "ctx._source.label_id = params.label_id; "
    "if (params.title != null) { ctx._source.title = params.title; } "
    "if (params.tags != null) { ctx._source.tags = params.tags; }"
)


def _ensure_label_mapping(es: Elasticsearch) -> None:
    """`label_id` must be a keyword before the first script writes it, or dynamic mapping makes it `text`
    and the `terms label_id` pre-filter silently never matches (Postgres still enforces). Idempotent."""
    try:
        es.indices.put_mapping(index=MUTATE_ALIAS, properties={"label_id": {"type": "keyword"}})
    except Exception:
        logging.getLogger(__name__).warning("could not ensure the label_id mapping", exc_info=True)


def sync_acl(
    es: Elasticsearch, doc_id, compute: "Callable[[], tuple[dict[str, Any], str | None] | None]"
) -> None:
    """Push ACL fields (and title when not None) to every chunk of the document; `compute` re-reads PG
    each try and returns (acl, title), or None when the document is gone."""

    def build() -> dict[str, Any]:
        got = compute()
        if got is None:
            return {"index": MUTATE_ALIAS, "query": {"match_none": {}}, "script": {"source": "return"}}
        acl, title = got
        return {
            "index": MUTATE_ALIAS,
            "query": {"term": {"doc_id": str(doc_id)}},
            "script": {"source": _ACL_SCRIPT, "lang": "painless", "params": {**acl, "title": title}},
        }

    _ensure_label_mapping(es)
    run_update_by_query(es, build)
