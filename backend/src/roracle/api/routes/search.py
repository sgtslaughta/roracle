"""GET /search: hybrid query path or Postgres browse path (R15), ACL-filtered disjunctive facets (R17), sorts, paging."""

from collections import Counter
from typing import Annotated, Literal
from uuid import UUID

import httpx
from elasticsearch import Elasticsearch, NotFoundError
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from roracle import clients
from roracle.api.routes.library import get_es
from roracle.api.routes.search_load import (  # noqa: F401 (names re-exported for callers and tests)
    ROW_COLUMNS,
    Row,
    _is_uuid,
    _load,
    _votes,
    cursor_of,
    load_stmt,
)
from roracle.auth import Principal, current_principal, get_app_settings
from roracle.citations import cited_by_counts
from roracle.config import Settings
from roracle.cursor import parse_cursor
from roracle.db import get_session, release_before_io
from roracle.labels.policy import Policy, label_json
from roracle.labels.textgate import gated_docs
from roracle.models import Collection, CollectionItem
from roracle.notes.community import attach_notes
from roracle.retrieval import DocHit, Passage, embed_query, group_by_document, search_chunks
from roracle.search_acl import acl_filter
from roracle.search_cache import PAGE_CACHE, PAGE_CACHE_MAX, cache_key, rehydrate, slim
from roracle.search_index import active_index
from roracle.search_index import file_type as ext_of

router = APIRouter()
MAX_DOCS = 200  # R15: browse cap; the query path is bounded by the fused lists (~100+ docs)
FACET_MAX = 50
FACETS = ("tags", "year", "file_type", "org_unit", "collection", "author", "label")
Who = Annotated[Principal, Depends(current_principal)]
Q_MAX, LIST_MAX, VALUE_MAX = 1000, 50, 200
Multi = Annotated[list[Annotated[str, Query(max_length=VALUE_MAX)]] | None, Query(max_length=LIST_MAX)]


class BadInput(ValueError):
    """Caller-supplied search parameter outside the documented limits (REST: 422, MCP: tool error)."""


def validate_search_params(q: str, lists: dict[str, list | None], size: int, page: int = 1) -> None:
    """The REST Query limits, for callers that invoke `search()` directly (MCP)."""
    if len(q) > Q_MAX:
        raise BadInput(f"query too long (max {Q_MAX})")
    for name, vs in lists.items():
        if vs and (len(vs) > LIST_MAX or any(len(str(v)) > VALUE_MAX for v in vs)):
            raise BadInput(f"{name}: at most {LIST_MAX} values of {VALUE_MAX} characters")
    if not 1 <= size <= 50:
        raise BadInput("limit must be 1..50")
    if page < 1:
        raise BadInput("page must be >= 1")


def get_llm(request: Request):
    app = request.app
    if not hasattr(app.state, "llm"):
        app.state.llm = clients.llm(app.state.settings)
    return app.state.llm


def get_http(request: Request) -> httpx.Client:
    """The process-wide keep-alive client created at app startup (`http_pool`)."""
    return request.app.state.http


def _own_collections(session: Session, p: Principal, values: list[str]) -> tuple[list[str], list[str]]:
    """R4/R10: the caller's collections among `values` -> (collection ids, member doc ids), both str."""
    cids = []
    for v in values:
        try:
            cids.append(UUID(v))
        except ValueError:
            pass
    owned = list(
        session.scalars(
            select(Collection.id).where(Collection.owner_id == p.user_id, Collection.id.in_(cids))
        )
    )
    docs = session.scalars(
        select(CollectionItem.document_id).where(CollectionItem.collection_id.in_(owned)).distinct()
    )
    return [str(c) for c in owned], [str(d) for d in docs]


def _keep(row: Row, filters: dict[str, set[str]], skip: str | None = None) -> bool:
    """Values OR within a facet, AND across facets (R10); `skip` leaves one facet out (R17)."""
    have = row.facet_values()
    return all(not want or want.intersection(have[name]) for name, want in filters.items() if name != skip)


def _facets(universe: list[Row], wanted: dict[str, set[str]]) -> dict[str, list[dict]]:
    """Disjunctive (R17): each facet counts the universe filtered by all OTHER facets' selections."""
    out: dict[str, list[dict]] = {}
    for name in FACETS:
        rows = [r for r in universe if _keep(r, wanted, skip=name)]
        labels = {c: n for r in rows for c, n in r.colls}
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
        "label": str(r.doc.label_id),  # id here; search() swaps in the label JSON
        "flag": None,  # outdated-flag lands later
        "cited_by_count": 0,  # filled by search() in one batched query
    }


def _response(
    rows: list[Row], universe: list[Row], wanted: dict[str, set[str]], sort: str, page: int, size: int
) -> dict:
    rows = rows[:MAX_DOCS]
    _sort(rows, sort)
    start = (page - 1) * size
    return {
        "total": len(rows),
        "page": page,
        "results": [_hit(r) for r in rows[start : start + size]],
        "facets": _facets(universe, wanted),
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
    qvec: list[float],
    raw: list[Passage] | None = None,
    *,
    policy: Policy | None = None,
    http: httpx.Client | None = None,
    light: bool = False,
) -> list[Row]:
    """ES first stage (cached for `search_cache_s`: paging and facet clicks make 0 ES calls) + the Postgres
    visibility re-check (R19), which runs on EVERY request, cached or not."""
    if coll_docs is not None and not coll_docs:
        return []
    policy = policy or Policy.load(session)
    key = cache_key(p, settings, q, filters, coll_docs, light, policy)
    use_cache = settings.search_cache_s > 0 and raw is None
    cached = PAGE_CACHE.get(key, settings.search_cache_s) if use_cache else None
    if cached is not None:
        passages = rehydrate(cached)
    else:
        release_before_io(session)  # release the connection: ES and rerank can take 100s of ms
        try:
            passages = search_chunks(
                es,
                llm,
                settings,
                p,
                q,
                policy=policy,
                filters=filters,
                doc_ids=coll_docs,
                size=100,
                qvec=qvec,
                min_similarity=settings.search_min_similarity or None,
                http=http,
                rerank_depth=settings.search_rerank_depth,
                light=light,
            )
        except NotFoundError:  # nothing ingested yet: no index
            return []
        if use_cache and passages:  # never cache "nothing": a fresh grant must show up at once
            PAGE_CACHE.put(key, slim(passages), PAGE_CACHE_MAX)
    if raw is not None:  # eval: ES hits before the Postgres re-check
        raw.extend(passages)
    ids = []
    for x in passages:
        try:
            ids.append(UUID(x.doc_id))
        except ValueError:
            pass
    votes = _votes(session, ids, p.user_id) if ids else {}  # once: grouping here, row enrichment in _load
    hits: list[DocHit] = group_by_document(passages, {k: (v[0], v[1]) for k, v in votes.items()})[:MAX_DOCS]
    by_id = {str(r.doc.id): r for r in _load(session, p, ids, votes=votes)} if ids else {}  # R19 PG re-check
    rows = []
    for h in hits:
        if h.doc_id in by_id:
            r = by_id[h.doc_id]
            if any(x.version_id != str(r.ver.id) for x in h.passages):
                continue  # a newer version became latest after the first stage ran: never show its old snippets
            r.score, r.passages = h.score, h.passages
            rows.append(r)
    return rows


def rank_for_search(
    session: Session,
    es: Elasticsearch,
    llm,
    settings: Settings,
    p: Principal,
    q: str,
    *,
    filters: dict[str, list[str]] | None = None,
    raw: list[Passage] | None = None,
) -> list[Passage]:
    """Library ranking as the `/search` endpoint produces it (PG-rechecked, grouped per document, votes),
    flattened to passages in document-rank order. `raw` (out-param, eval) receives the ES hits pre-recheck."""
    rows = _query_rows(
        session, es, llm, settings, p, q, filters or {}, None, embed_query(llm, settings, q), raw
    )
    return [x for r in rows for x in r.passages]


QUICK_DOCS = 10


def _quick(session: Session, es: Elasticsearch, settings: Settings, p: Principal, q: str) -> dict:
    """Palette search: ONE ACL-filtered BM25 on titles (no embed, no kNN, no rerank), then the Postgres
    visibility re-check (R19) like every other path. At most QUICK_DOCS documents."""
    policy = Policy.load(session)
    release_before_io(session)
    body = {
        "size": 2 * QUICK_DOCS,
        "collapse": {"field": "doc_id"},  # one hit per document
        "_source": ["doc_id"],
        "query": {
            "bool": {
                "must": [{"multi_match": {"query": q, "fields": ["title"]}}],
                "filter": [acl_filter(p, policy), {"term": {"is_latest": True}}],
            }
        },
    }
    try:
        hits = es.search(index=active_index(settings), **body)["hits"]["hits"]
    except NotFoundError:
        return _empty(1)
    ids: list[UUID] = []
    for h in hits:  # ES order = score order; one entry per document
        d = UUID(h["_source"]["doc_id"])
        if d not in ids:
            ids.append(d)
    by_id = {r.doc.id: r for r in _load(session, p, ids[: 2 * QUICK_DOCS])}
    rows = [by_id[i] for i in ids if i in by_id][:QUICK_DOCS]
    out = [_hit(r) for r in rows]
    for h in out:
        h["label"] = label_json(policy.get(h["label"]))  # same shape as the full search
    return {"total": len(rows), "page": 1, "results": out, "facets": {"label": []}}


def _empty(page: int) -> dict:
    return _response([], [], {}, "relevance", page, 1)


@router.get("/search")
def search(
    p: Who,
    session: Annotated[Session, Depends(get_session)],
    es: Annotated[Elasticsearch, Depends(get_es)],
    llm: Annotated[object, Depends(get_llm)],
    settings: Annotated[Settings, Depends(get_app_settings)],
    http: Annotated[httpx.Client, Depends(get_http)],
    q: Annotated[str, Query(max_length=1000)] = "",
    tags: Multi = None,
    year: Annotated[list[int] | None, Query(max_length=LIST_MAX)] = None,
    file_type: Multi = None,
    org_unit: Multi = None,
    collection: Multi = None,
    author: Multi = None,
    label: Multi = None,
    sort: Literal["relevance", "newest", "votes", "title"] = "relevance",
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(ge=1, le=50)] = 20,
    notes: bool = True,
    after: Annotated[str | None, Query(max_length=100)] = None,
    mode: Literal["full", "quick"] = "full",
) -> dict:
    if mode == "quick":
        if any((tags, year, file_type, org_unit, collection, author, label, after)) or page != 1:
            raise HTTPException(
                422, "mode=quick takes only q"
            )  # title search for the palette: no filters/paging
        if not q.strip():
            return _empty(1)
        return _quick(session, es, settings, p, q.strip())
    cursor = None
    if after:  # keyset paging of the browse list (empty q, newest-first); `next` in the response
        try:
            cursor = parse_cursor(after)
        except ValueError:
            raise HTTPException(422, "malformed after cursor") from None
        if q.strip() or sort not in ("relevance", "newest"):
            raise HTTPException(422, "after applies to the newest-first browse list only")
    es_filters = {
        "tags": tags or [],
        "year": year or [],
        "file_type": file_type or [],
        "org_unit": org_unit or [],
        "author": author or [],
        "label": [x for x in label or [] if _is_uuid(x)],
    }
    wanted = {k: {str(v) for v in vs} for k, vs in es_filters.items()}
    coll_docs = None
    if collection:
        cids, coll_docs = _own_collections(session, p, collection)
        if not cids:  # foreign/unknown collection: nothing matches
            return _empty(page)
        wanted["collection"] = set(cids)
    filtered = any(wanted.values())
    policy = Policy.load(session)  # once per request
    if q.strip():
        release_before_io(session)  # no pooled connection held idle-in-transaction across embed / ES / rerank
        try:
            qvec = embed_query(llm, settings, q)  # once for both searches
            rows = _query_rows(
                session, es, llm, settings, p, q, es_filters, coll_docs, qvec, policy=policy, http=http
            )
            # R17: facets need the unfiltered match set too (second search, only when filters are active):
            # a light pass, ids only, no rerank
            universe = (
                _query_rows(session, es, llm, settings, p, q, {}, None, qvec, policy=policy, light=True)
                if filtered
                else rows
            )
        except ValueError as e:  # backstop: search_chunks input errors are the client's
            raise HTTPException(422, str(e)) from None
        rows = [r for r in rows if _keep(r, wanted)]
    else:
        rows = _load(session, p, None, wanted, MAX_DOCS, after=cursor)
        rows = [r for r in rows if _keep(r, wanted)]
        universe = _load(session, p, None, None, MAX_DOCS, after=cursor) if filtered else rows
    seen = {r.doc.id for r in universe}
    universe = universe + [r for r in rows if r.doc.id not in seen]
    out = _response(rows, universe, wanted, sort, page, size)
    if not q.strip() and sort in ("relevance", "newest"):
        shown = rows[(page - 1) * size : page * size]
        out["next"] = cursor_of(shown[-1]) if shown and len(rows) > page * size else None
    counts = cited_by_counts(session, p, [UUID(h["doc_id"]) for h in out["results"]])
    for h in out["results"]:
        h["cited_by_count"] = counts.get(h["doc_id"], 0)
        h["label"] = label_json(policy.get(h["label"]))
    for f in out["facets"]["label"]:  # counts come from post-filtered rows only
        f["label"] = label_json(policy.get(f["value"]))
    gated = gated_docs(session, p, [h["doc_id"] for h in out["results"]])
    for h in out["results"]:  # agents: metadata and label, no passage text of exclude_llm docs
        if h["doc_id"] in gated:
            h["passages"], h["withheld"] = [], gated[h["doc_id"]]
    attach_notes(session, p, out["results"], notes)
    return out
