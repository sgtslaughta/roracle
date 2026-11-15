"""Elasticsearch chunk index: `chunks-<model-slug>-1` behind alias `chunks` (ADR-0002 ACL fields)."""

import re
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from elasticsearch import Elasticsearch, NotFoundError, helpers
from elasticsearch.exceptions import BadRequestError
from sqlalchemy import select
from sqlalchemy.orm import Session

from roracle.models import Document, DocumentShare, DocumentVersion
from roracle.queue import NonRetryable

if TYPE_CHECKING:  # runtime import would be circular (ingest/__init__ -> pipeline -> here)
    from roracle.ingest.chunking import Chunk

ALIAS = "chunks"


def index_name(model: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", model.lower()).strip("-")
    return f"chunks-{slug}-1"


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
            "share_users": kw,
            "share_groups": kw,
            "title": {"type": "text", "analyzer": "english"},
            "authors": kw,
            "tags": kw,
            "file_type": kw,
            "collections": kw,
            "year": {"type": "integer"},
            "text": {"type": "text", "analyzer": "english"},
            "headings": {"type": "text", "analyzer": "english"},
            "pages": {"type": "integer"},
            "bboxes": {"type": "object", "enabled": False},
            "embedding_model": kw,
            "embedding": vec,
            "summary_embedding": vec,
        }
    }


def ensure_index(es: Elasticsearch, model: str, dims: int, alias: str = ALIAS) -> str:
    """Create the index and alias if absent. Idempotent and race-safe."""
    name = index_name(model)
    if not es.indices.exists(index=name):
        try:
            es.indices.create(index=name, mappings=_mapping(dims))
        except BadRequestError as e:
            if e.error != "resource_already_exists_exception":
                raise
    props = es.indices.get_mapping(index=name)[name]["mappings"].get("properties", {})
    missing = {
        f: t
        for f, t in _mapping(dims)["properties"].items()
        if f not in props and t["type"] in ("keyword", "dense_vector")
    }
    if missing:  # fields added after the index was created (R9)
        es.indices.put_mapping(index=name, properties=missing)
    props = {**props, **missing}
    if any(props.get(f, {}).get("dims") != dims for f in ("embedding", "summary_embedding")):
        raise NonRetryable("search index dimension mismatch: reindex required")
    if not es.indices.exists_alias(name=alias, index=name):
        es.indices.put_alias(index=name, name=alias)
    return name


def acl_fields(session: Session, document: Document) -> dict[str, Any]:
    shares = session.execute(
        select(DocumentShare.principal_kind, DocumentShare.principal).where(
            DocumentShare.document_id == document.id
        )
    ).all()
    return {
        "owner_id": str(document.owner_id),
        "visibility": document.visibility,
        "org_unit": document.org_unit,
        "share_users": sorted(p for k, p in shares if k == "user"),
        "share_groups": sorted(p for k, p in shares if k == "group"),
    }


def file_type(filename: str | None) -> str:
    """Lowercase extension without dot; "other" when absent."""
    ext = (filename or "").rsplit(".", 1)[1].lower() if "." in (filename or "") else ""
    return ext if re.fullmatch(r"[a-z0-9]{1,8}", ext) else "other"


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
) -> int:
    """Bulk index with deterministic `_id` (re-runs overwrite); raise on any item error."""
    meta = version.meta or {}
    base = {
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
    actions = [
        {
            "_op_type": "index",
            "_index": index_name(model),
            "_id": f"{version.id}:{c.n}",
            "_source": {
                **base,
                "chunk_id": f"{version.id}:{c.n}",
                "text": c.text,
                "headings": c.headings,
                "pages": c.pages,
                "bboxes": c.bboxes,
                "embedding": v,
                **({"summary_embedding": summary_vector} if c.n == 0 and summary_vector else {}),
            },
        }
        for c, v in zip(chunks, vectors, strict=True)
    ]
    ok, errors = helpers.bulk(es, actions, refresh="wait_for", raise_on_error=False)
    if errors:
        raise RuntimeError(f"bulk indexing failed for {len(errors)} chunks: {errors[0]}")
    return ok


def delete_stale_chunks(es: Elasticsearch, version_id, keep: int, alias: str = ALIAS) -> None:
    """Remove chunks of this version with n >= keep (a re-run may produce fewer chunks)."""
    es.delete_by_query(
        index=alias,
        query={
            "bool": {
                "filter": [{"term": {"version_id": str(version_id)}}],
                "must_not": [{"ids": {"values": [f"{version_id}:{n}" for n in range(keep)]}}],
            }
        },
        refresh=True,
        conflicts="proceed",
    )


def mark_previous_versions(es: Elasticsearch, doc_id, version: int, alias: str = ALIAS) -> None:
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
    "ctx._source.share_groups = params.share_groups; "
    "if (params.title != null) { ctx._source.title = params.title; } "
    "if (params.tags != null) { ctx._source.tags = params.tags; }"
)


def sync_acl(
    es: Elasticsearch, doc_id, compute: "Callable[[], tuple[dict[str, Any], str | None] | None]"
) -> None:
    """Push ACL fields (and title when not None) to every chunk of the document; `compute` re-reads PG
    each try and returns (acl, title), or None when the document is gone."""

    def build() -> dict[str, Any]:
        got = compute()
        if got is None:
            return {"index": ALIAS, "query": {"match_none": {}}, "script": {"source": "return"}}
        acl, title = got
        return {
            "index": ALIAS,
            "query": {"term": {"doc_id": str(doc_id)}},
            "script": {"source": _ACL_SCRIPT, "lang": "painless", "params": {**acl, "title": title}},
        }

    run_update_by_query(es, build)
