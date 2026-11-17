"""Legacy `/search` (pre search-fields indices): hybrid query path and the Postgres browse path (R15), in-memory
disjunctive facets (R17). Also serves non-empty queries until the query path moves to search fields (Task 8).

# ponytail: delete once every deployment has run the search-fields reindex (R5)
"""

from collections import Counter
from uuid import UUID

import httpx
from elasticsearch import Elasticsearch, NotFoundError
from fastapi import HTTPException
from sqlalchemy.orm import Session

from roracle.api.routes.search_load import Row, _hit, _is_uuid, _load, _own_collections, _votes, cursor_of
from roracle.auth import Principal
from roracle.config import Settings
from roracle.cursor import parse_cursor
from roracle.db import release_before_io
from roracle.labels.policy import Policy, label_json
from roracle.retrieval import DocHit, Passage, embed_query, group_by_document, search_chunks
from roracle.search_browse import decorate
from roracle.search_cache import PAGE_CACHE, PAGE_CACHE_MAX, cache_key, rehydrate, slim
from roracle.search_limits import FACET_MAX, MAX_DOCS

FACETS = ("tags", "year", "file_type", "org_unit", "collection", "author", "label")


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


def _response(
    rows: list[Row], universe: list[Row], wanted: dict[str, set[str]], sort: str, page: int, size: int
) -> dict:
    rows = rows[:MAX_DOCS]
    _sort(rows, sort)
    start = (page - 1) * size
    return {
        "total": len(rows),
        "total_capped": False,
        "page": page,
        "next": None,
        "results": [_hit(r) for r in rows[start : start + size]],
        "facets": _facets(universe, wanted),
    }


def empty_legacy(page: int) -> dict:
    return _response([], [], {}, "relevance", page, 1)


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
    ids = [UUID(x.doc_id) for x in passages if _is_uuid(x.doc_id)]
    votes = _votes(session, ids, p.user_id) if ids else {}  # once: grouping here, row enrichment in _load
    hits: list[DocHit] = group_by_document(passages, {k: (v[0], v[1]) for k, v in votes.items()})[:MAX_DOCS]
    by_id = {str(r.doc.id): r for r in _load(session, p, ids, votes=votes)} if ids else {}  # R19 PG re-check
    rows = []
    for h in hits:
        if h.doc_id in by_id:
            r = by_id[h.doc_id]
            live = [x for x in h.passages if x.version_id == str(r.ver.id)]
            if not live:
                continue  # only stale snippets: a newer version became latest after the first stage ran
            r.score, r.passages = h.score, live  # stale snippets are filtered, the document stays
            rows.append(r)
    return rows


def legacy_search(
    session: Session,
    es: Elasticsearch,
    llm,
    settings: Settings,
    http: httpx.Client | None,
    p: Principal,
    *,
    q: str = "",
    tags: list[str] | None = None,
    year: list[int] | None = None,
    file_type: list[str] | None = None,
    org_unit: list[str] | None = None,
    collection: list[str] | None = None,
    author: list[str] | None = None,
    label: list[str] | None = None,
    sort: str = "relevance",
    page: int = 1,
    size: int = 20,
    notes: bool = True,
    after: str | None = None,
) -> dict:
    """The pre-search-fields `/search` body (HTTPException 422 on bad input)."""
    cursor = None
    if after:  # keyset paging of the browse list (empty q, newest-first); `next` in the response
        try:
            cursor = parse_cursor(after)
        except ValueError:
            raise HTTPException(422, "malformed after cursor") from None
        if q.strip() or sort not in ("relevance", "newest"):
            raise HTTPException(422, "after applies to the newest-first browse list only")
        if page != 1:
            raise HTTPException(422, "after replaces page: use one paging mode")
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
            return empty_legacy(page)
        wanted["collection"] = set(cids)
    filtered = any(wanted.values())
    policy = Policy.load(session)  # once per request
    if q.strip():
        release_before_io(session)  # no pooled connection held idle-in-transaction across embed / ES / rerank
        try:
            qvec = embed_query(llm, settings, q, p.user_id)  # once for both searches
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
    if cursor:  # `total` and the facets describe the window AFTER the cursor, not the whole list
        out["window"] = True
    if not q.strip() and sort in ("relevance", "newest"):
        shown = rows[(page - 1) * size : page * size]
        out["next"] = cursor_of(shown[-1]) if shown and len(rows) > page * size else None
    for f in out["facets"]["label"]:  # counts come from post-filtered rows only
        f["label"] = label_json(policy.get(f["value"]))
    decorate(session, p, policy, out["results"], notes)
    return out
