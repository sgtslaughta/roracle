"""Search fields denormalized onto ES chunks (bibliographic, viewer-independent) + readiness marker."""

import time
from datetime import datetime
from types import SimpleNamespace
from typing import Any
from uuid import UUID

from elasticsearch import NotFoundError
from sqlalchemy import select
from sqlalchemy.orm import Session

from roracle.models import Document, DocumentVersion

SEARCH_FIELDS_VERSION = 1
_kw: dict[str, Any] = {"type": "keyword"}
_bool: dict[str, Any] = {"type": "boolean"}
_int: dict[str, Any] = {"type": "integer"}
SEARCH_PROPS: dict[str, dict] = {
    "venue": _kw,
    "venue_sort": _kw,
    "has_doi": _bool,
    "has_abstract": _bool,
    "has_refs": _bool,
    "page_count": _int,
    "added_at": {"type": "date"},
    "title_sort": _kw,
    "first_author_sort": _kw,
    "authors_text": {"type": "text", "analyzer": "standard"},
    "abstract": {"type": "text", "analyzer": "english"},
    "doc_head": _bool,
    "votes_net": _int,
    "cited_by_global": _int,
    "searchable": _bool,
}


def sort_key(s: str | None) -> str | None:
    out = " ".join((s or "").split()).casefold()[:128]
    return out or None


def doc_fields(version: Any, document: Any) -> dict[str, Any]:
    meta = version.meta or {}
    authors = [a for a in (meta.get("authors") or []) if isinstance(a, str)]
    venue = meta.get("venue") if isinstance(meta.get("venue"), str) else None
    created: datetime | None = document.created_at
    return {
        "venue": venue or None,
        "venue_sort": sort_key(venue),
        "has_doi": bool(meta.get("doi")),
        "has_abstract": bool(meta.get("abstract")),
        "has_refs": bool(version.references),
        "page_count": version.page_count,
        "added_at": created.isoformat() if created else None,
        "title_sort": sort_key(document.title or meta.get("title") or version.filename),
        "first_author_sort": sort_key(authors[0]) if authors else None,
        "authors_text": "; ".join(authors),
    }


def head_fields(version: Any) -> dict[str, Any]:
    return {"doc_head": True, "abstract": (version.meta or {}).get("abstract") or None}


def fields_for(session: Session, version_ids: list[UUID]) -> dict[str, tuple[dict, dict]]:
    """One query, only the columns the builders read (never minhash / summary_embedding)."""
    if not version_ids:
        return {}
    rows = session.execute(
        select(
            DocumentVersion.id, DocumentVersion.meta, DocumentVersion.references,
            DocumentVersion.page_count, DocumentVersion.filename, Document.title, Document.created_at,
        )
        .join(Document, Document.id == DocumentVersion.document_id)
        .where(DocumentVersion.id.in_(version_ids))
    ).all()  # fmt: skip
    out = {}
    for r in rows:
        v = SimpleNamespace(
            meta=r.meta, references=r.references, page_count=r.page_count, filename=r.filename
        )
        out[str(r.id)] = (doc_fields(v, r), head_fields(v))
    return out


_READY: dict[str, tuple[float, bool]] = {}
READY_TTL_S = 30.0


def fields_ready(es: Any, settings: Any) -> bool:
    """Active index carries `_meta.search_fields >= SEARCH_FIELDS_VERSION` (missing index: nothing to upgrade)."""
    from roracle.search_index import active_index

    name = active_index(settings)
    hit = _READY.get(name)
    if hit and time.monotonic() - hit[0] < READY_TTL_S:
        return hit[1]
    try:
        meta = es.indices.get_mapping(index=name)[name]["mappings"].get("_meta") or {}
        ok = int(meta.get("search_fields", 0)) >= SEARCH_FIELDS_VERSION
    except NotFoundError:
        ok = True
    _READY[name] = (time.monotonic(), ok)
    return ok
