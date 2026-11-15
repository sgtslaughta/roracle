"""Elasticsearch chunk index: `chunks-<model-slug>-1` behind alias `chunks` (ADR-0002 ACL fields)."""

import re
from typing import TYPE_CHECKING, Any

from elasticsearch import Elasticsearch, helpers
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
            "year": {"type": "integer"},
            "text": {"type": "text", "analyzer": "english"},
            "headings": {"type": "text", "analyzer": "english"},
            "pages": {"type": "integer"},
            "bboxes": {"type": "object", "enabled": False},
            "embedding_model": kw,
            "embedding": {
                "type": "dense_vector",
                "dims": dims,
                "index": True,
                "similarity": "cosine",
                "index_options": {"type": "int8_hnsw"},
            },
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
    if props.get("embedding", {}).get("dims") != dims:
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


def mark_previous_versions(es: Elasticsearch, doc_id, latest_version_id, alias: str = ALIAS) -> None:
    es.update_by_query(
        index=alias,
        query={
            "bool": {
                "filter": [{"term": {"doc_id": str(doc_id)}}],
                "must_not": [{"term": {"version_id": str(latest_version_id)}}],
            }
        },
        script={"source": "ctx._source.is_latest = false", "lang": "painless"},
        refresh=True,
        conflicts="proceed",
    )
