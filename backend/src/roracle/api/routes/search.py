"""GET /search: hybrid query path or Postgres browse path (R15), ACL-filtered facets, sorts, paging."""

from collections import Counter
from dataclasses import dataclass, field
from typing import Annotated, Literal
from uuid import UUID

from elasticsearch import Elasticsearch, NotFoundError
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import and_, case, func, select
from sqlalchemy.orm import Session

from roracle import clients
from roracle.access import visible_documents
from roracle.api.routes.library import get_es
from roracle.auth import Principal, current_principal, get_app_settings
from roracle.config import Settings
from roracle.db import get_session
from roracle.models import Collection, CollectionItem, Document, DocumentVersion, Tag, Vote
from roracle.retrieval import DocHit, Passage, group_by_document, search_chunks
from roracle.search_index import file_type as ext_of

router = APIRouter()
MAX_DOCS = 200  # R15: matched set cap
FACET_MAX = 50
FACETS = ("tags", "year", "file_type", "org_unit", "collection", "author")
Who = Annotated[Principal, Depends(current_principal)]
Multi = Annotated[list[str] | None, Query()]


def get_llm(request: Request):
    app = request.app
    if not hasattr(app.state, "llm"):
        app.state.llm = clients.llm(app.state.settings)
    return app.state.llm


@dataclass
class Row:
    doc: Document
    ver: DocumentVersion
    tags: list[str] = field(default_factory=list)
    votes: tuple[int, int, int] = (0, 0, 0)  # up, down, mine
    colls: list[tuple[str, str]] = field(default_factory=list)  # caller's (id, name)
    score: float = 0.0
    passages: list[Passage] = field(default_factory=list)

    @property
    def meta(self) -> dict:
        return self.ver.meta or {}

    @property
    def title(self) -> str:
        return self.doc.title or self.meta.get("title") or self.ver.filename

    def facet_values(self) -> dict[str, list[str]]:
        year = self.meta.get("year")
        return {
            "tags": self.tags,
            "year": [] if year is None else [str(year)],
            "file_type": [ext_of(self.ver.filename)],
            "org_unit": [self.doc.org_unit],
            "collection": [c for c, _ in self.colls],
            "author": list(self.meta.get("authors") or []),
        }


def _votes(session: Session, ids: list[UUID], uid: UUID) -> dict[str, tuple[int, int, int]]:
    mine = func.coalesce(func.max(case((Vote.user_id == uid, Vote.value))), 0)
    rows = session.execute(
        select(
            Vote.document_id,
            func.count().filter(Vote.value == 1),
            func.count().filter(Vote.value == -1),
            mine,
        )
        .where(Vote.document_id.in_(ids))
        .group_by(Vote.document_id)
    ).all()
    return {str(d): (u, n, m) for d, u, n, m in rows}


def _load(session: Session, p: Principal, ids: list[UUID] | None) -> list[Row]:
    """Visible documents with a searchable latest version, newest first, enriched from Postgres."""
    stmt = (
        select(Document, DocumentVersion)
        .join(
            DocumentVersion,
            and_(
                DocumentVersion.document_id == Document.id,
                DocumentVersion.is_latest.is_(True),
                DocumentVersion.stage == "searchable",
            ),
        )
        .where(visible_documents(p))
        .order_by(DocumentVersion.created_at.desc(), Document.id)
    )
    if ids is not None:
        stmt = stmt.where(Document.id.in_(ids))
    rows = {d.id: Row(d, v) for d, v in session.execute(stmt).all()}
    if rows:
        keys = list(rows)
        for doc_id, tag in session.execute(select(Tag.document_id, Tag.tag).where(Tag.document_id.in_(keys))):
            rows[doc_id].tags.append(tag)
        for r in rows.values():
            r.tags.sort()
        for doc_id, cid, name in session.execute(
            select(CollectionItem.document_id, Collection.id, Collection.name)
            .join(Collection, Collection.id == CollectionItem.collection_id)
            .where(Collection.owner_id == p.user_id, CollectionItem.document_id.in_(keys))
        ):
            rows[doc_id].colls.append((str(cid), name))
        for k, v in _votes(session, keys, p.user_id).items():
            rows[UUID(k)].votes = v
    return list(rows.values())


def _own_collections(session: Session, p: Principal, values: list[str]) -> tuple[list[UUID], list[str]]:
    """R4/R10: the caller's collections among `values` -> (collection ids, member doc ids as str)."""
    cids = []
    for v in values:
        try:
            cids.append(UUID(v))
        except ValueError:
            pass
    docs = session.scalars(
        select(CollectionItem.document_id)
        .join(Collection, Collection.id == CollectionItem.collection_id)
        .where(Collection.owner_id == p.user_id, Collection.id.in_(cids))
        .distinct()
    )
    return cids, [str(d) for d in docs]


def _keep(row: Row, filters: dict[str, set[str]]) -> bool:
    """Values OR within a facet, AND across facets (R10)."""
    have = row.facet_values()
    return all(not want or want.intersection(have[name]) for name, want in filters.items())


def _facets(rows: list[Row]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    labels = {c: n for r in rows for c, n in r.colls}
    for name in FACETS:
        counts = Counter(v for r in rows for v in set(r.facet_values()[name]))
        top = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:FACET_MAX]
        out[name] = [
            {"value": v, "count": n, **({"label": labels[v]} if name == "collection" else {})} for v, n in top
        ]
    return out


def _sort(rows: list[Row], sort: str) -> None:
    """Stable: ties keep relevance order (query) or newest order (browse)."""
    if sort == "newest":
        rows.sort(key=lambda r: r.ver.created_at, reverse=True)
    elif sort == "votes":
        rows.sort(key=lambda r: r.votes[0] - r.votes[1], reverse=True)
    elif sort == "title":
        rows.sort(key=lambda r: r.title.casefold())
    else:
        rows.sort(key=lambda r: r.score, reverse=True)


def _passage(x: Passage) -> dict:
    bbox = {k: v for k, v in x.bboxes[0].items() if k != "page"} if x.bboxes else None
    return {
        "page": x.pages[0] if x.pages else 1,
        "chunk_id": x.chunk_id,
        "text": x.text,
        "highlight": x.highlight,
        "bbox": bbox,
    }


def _hit(r: Row) -> dict:
    return {
        "doc_id": str(r.doc.id),
        "title": r.title,
        "authors": list(r.meta.get("authors") or []),
        "year": r.meta.get("year"),
        "score": r.score,
        "passages": [_passage(x) for x in r.passages],
        "tags": r.tags,
        "votes": {"up": r.votes[0], "down": r.votes[1], "mine": r.votes[2]},
        "file_type": ext_of(r.ver.filename),
        "version": r.ver.version,
        "venue": r.meta.get("venue"),
        "org_unit": r.doc.org_unit,
        "owner_id": str(r.doc.owner_id),
        "flag": None,  # outdated-flag lands later
        "cited_by_count": 0,  # Task 5
    }


def _response(rows: list[Row], sort: str, page: int, size: int) -> dict:
    rows = rows[:MAX_DOCS]
    _sort(rows, sort)
    start = (page - 1) * size
    return {
        "total": len(rows),
        "page": page,
        "results": [_hit(r) for r in rows[start : start + size]],
        "facets": _facets(rows),
    }


def _query_rows(
    session: Session,
    es: Elasticsearch,
    llm,
    settings: Settings,
    p: Principal,
    q: str,
    filters: dict[str, list[str]],
    coll_docs: list[str] | None,
) -> list[Row]:
    if coll_docs is not None and not coll_docs:
        return []
    try:
        passages = search_chunks(es, llm, settings, p, q, filters=filters, doc_ids=coll_docs, size=100)
    except NotFoundError:  # nothing ingested yet: no index
        return []
    ids = []
    for x in passages:
        try:
            ids.append(UUID(x.doc_id))
        except ValueError:
            pass
    votes = {k: (v[0], v[1]) for k, v in _votes(session, ids, p.user_id).items()} if ids else {}
    hits: list[DocHit] = group_by_document(passages, votes)[:MAX_DOCS]
    by_id = {str(r.doc.id): r for r in _load(session, p, ids)} if ids else {}  # PG visibility re-check
    rows = []
    for h in hits:
        if h.doc_id in by_id:
            r = by_id[h.doc_id]
            r.score, r.passages = h.score, h.passages
            rows.append(r)
    return rows


@router.get("/search")
def search(
    p: Who,
    session: Annotated[Session, Depends(get_session)],
    es: Annotated[Elasticsearch, Depends(get_es)],
    llm: Annotated[object, Depends(get_llm)],
    settings: Annotated[Settings, Depends(get_app_settings)],
    q: Annotated[str, Query(max_length=1000)] = "",
    tags: Multi = None,
    year: Annotated[list[int] | None, Query()] = None,
    file_type: Multi = None,
    org_unit: Multi = None,
    collection: Multi = None,
    author: Multi = None,
    sort: Literal["relevance", "newest", "votes", "title"] = "relevance",
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(ge=1, le=50)] = 20,
) -> dict:
    es_filters = {
        "tags": tags or [],
        "year": year or [],
        "file_type": file_type or [],
        "org_unit": org_unit or [],
        "author": author or [],
    }
    wanted = {k: {str(v) for v in vs} for k, vs in es_filters.items()}
    coll_docs = None
    if collection:
        cids, coll_docs = _own_collections(session, p, collection)
        wanted["collection"] = {str(c) for c in cids}
        if not cids:
            wanted["collection"] = {"-"}  # foreign/unknown collection: nothing matches
    if q.strip():
        try:
            rows = _query_rows(session, es, llm, settings, p, q, es_filters, coll_docs)
        except ValueError as e:  # backstop: search_chunks input errors are the client's
            raise HTTPException(422, str(e)) from None
    else:
        rows = _load(session, p, None)
    return _response([r for r in rows if _keep(r, wanted)], sort, page, size)
