"""Empty-query browse from Elasticsearch head chunks (R3): corpus-wide totals and disjunctive facets under
`acl_filter`, every page re-checked in Postgres (R19) and topped up from ES when rows drop; the cited window (R2)."""

from typing import Any
from uuid import UUID

from elasticsearch import BadRequestError, Elasticsearch, NotFoundError
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from roracle.api.routes.search_load import Row, _load
from roracle.auth import Principal
from roracle.citations import cited_by_counts
from roracle.config import Settings
from roracle.cursor import make_sort_cursor, parse_sort_cursor
from roracle.db import release_before_io
from roracle.labels.policy import Policy, label_json
from roracle.labels.textgate import gated_docs
from roracle.models import DocumentVersion
from roracle.notes.community import attach_notes
from roracle.search_acl import acl_filter
from roracle.search_facets import facet_aggs, find_aggs, journal_rows, read_facets
from roracle.search_filters import facet_clauses, head_clauses, plain_clauses
from roracle.search_index import active_index
from roracle.search_limits import BROWSE_TRIES, BROWSE_WINDOW, MAX_DOCS
from roracle.search_params import SearchParams, Sort
from roracle.search_scope import DocScope

_TAIL = [{"added_at": {"order": "desc"}}, {"doc_id": {"order": "asc"}}]


def _s(field: str, order: str, last: bool = True) -> list[dict]:
    return [{field: {"order": order, **({"missing": "_last"} if last else {})}}, *_TAIL]


ES_SORTS: dict[Sort, list[dict]] = {
    "relevance": _TAIL,
    "newest": _TAIL,
    "published_desc": _s("year", "desc"),
    "published_asc": _s("year", "asc"),
    "votes": _s("votes_net", "desc"),
    "title": _s("title_sort", "asc", last=False),
    "first_author": _s("first_author_sort", "asc"),
    "venue": _s("venue_sort", "asc"),
    "pages": _s("page_count", "asc"),
    "cited": _s("cited_by_global", "desc", last=False),
}


def empty_body(page: int, policy: Policy) -> dict:
    """Same shape as a browse with no match (an invisible scope is indistinguishable from an empty one)."""
    return {
        "total": 0,
        "total_capped": False,
        "page": page,
        "next": None,
        "results": [],
        "facets": read_facets({}, policy, {}),
    }


def card_extras(session: Session, ids: list[UUID]) -> dict[str, tuple[str | None, str | None, int]]:
    """Page-only card fields, one query: kept out of `_load` (it loads up to MAX_DOCS rows for facets).
    Text fields (summary, abstract) exist ONLY here, so a route that skips `decorate` never carries them."""
    if not ids:
        return {}
    v = DocumentVersion
    rows = session.execute(
        select(
            v.document_id, v.summary, v.meta["abstract"].astext, func.jsonb_array_length(v.references)
        ).where(v.document_id.in_(ids), v.is_latest, v.stage == "searchable")
    )
    return {str(d): (s, a, n or 0) for d, s, a, n in rows}


def decorate(session: Session, p: Principal, policy: Policy, results: list[dict], notes: bool) -> None:
    """Viewer-relative extras of a result page: visible cited-by counts, label JSON, agent gate, notes."""
    ids = [UUID(h["doc_id"]) for h in results]
    counts = cited_by_counts(session, p, ids)
    extra = card_extras(session, ids)
    for h in results:
        h["cited_by_count"] = counts.get(h["doc_id"], 0)
        h["summary"], h["abstract"], h["ref_count"] = extra.get(h["doc_id"], (None, None, 0))
        h["label"] = label_json(policy.get(h["label"]))
    gated = gated_docs(session, p, [h["doc_id"] for h in results])
    for h in results:  # agents: metadata and label, no passage text of exclude_llm docs
        if h["doc_id"] in gated:
            h["passages"], h["withheld"] = [], gated[h["doc_id"]]
            h["summary"] = h["abstract"] = None
    attach_notes(session, p, results, notes)


def _query(p: Principal, policy: Policy, f: SearchParams, scope: DocScope, extra=()) -> dict:
    """ACL first, always: every request (and so every aggregation) of this module is built here."""
    flt, nn = plain_clauses(f, p, scope)
    head = head_clauses(f)
    return {
        "bool": {
            "filter": [acl_filter(p, policy), {"term": {"is_latest": True}}, *head, *flt, *extra],
            "must_not": nn,
        }
    }


def _post(f: SearchParams) -> dict:
    flt, nn = facet_clauses(f)
    return {"bool": {"filter": flt, "must_not": nn}}


def _search(es: Elasticsearch, settings: Settings, body: dict[str, Any]) -> dict:
    try:
        return es.search(index=active_index(settings), **body)
    except BadRequestError:  # a cursor from another sort (type mismatch): the client's input
        if "search_after" in body:
            raise HTTPException(422, "malformed after cursor") from None
        raise


def _rows(session: Session, p: Principal, ids: list[str]) -> dict[str, Row]:
    return {str(r.doc.id): r for r in _load(session, p, [UUID(i) for i in ids])} if ids else {}


def browse(
    session: Session,
    es: Elasticsearch,
    settings: Settings,
    p: Principal,
    policy: Policy,
    f: SearchParams,
    scope: DocScope,
) -> dict:
    """`{rows, total, total_capped, next, facets}` of one browse page (R3)."""
    if f.sort == "cited" or f.cited_min is not None:
        return _cited(session, es, settings, p, policy, f, scope)
    sort = ES_SORTS[f.sort]
    size = 2 * f.size
    body: dict[str, Any] = {
        "query": _query(p, policy, f, scope),
        "post_filter": _post(f),
        "aggs": facet_aggs(f, scope.colls),
        "sort": sort,
        "_source": ["doc_id"],
        "track_total_hits": BROWSE_WINDOW,
        "size": size,
    }
    if f.after:
        if f.page != 1:
            raise HTTPException(422, "after replaces page: use one paging mode")
        try:
            body["search_after"] = parse_sort_cursor(f.after)
        except ValueError:
            raise HTTPException(422, "malformed after cursor") from None
        if len(body["search_after"]) != len(sort):
            raise HTTPException(422, "malformed after cursor")
    else:
        start = (f.page - 1) * f.size
        if start + f.size > BROWSE_WINDOW:
            raise HTTPException(422, "use after to page this deep")
        size = body["size"] = min(size, BROWSE_WINDOW - start)  # the last window page fetches no spare hits
        body["from_"] = start
    release_before_io(session)
    try:
        resp = _search(es, settings, body)
    except NotFoundError:  # nothing ingested yet: no index
        return {**empty_body(f.page, policy), "rows": []}
    total = resp["hits"]["total"]
    out = {
        "total": total["value"],
        "total_capped": total["relation"] == "gte",
        "facets": read_facets(resp.get("aggregations", {}), policy, scope.colls),
    }
    rows: list[Row] = []
    hits, last, more = resp["hits"]["hits"], None, False
    for tries in range(1, BROWSE_TRIES + 1):
        by_id = _rows(session, p, [h["_source"]["doc_id"] for h in hits])  # R19 re-check, ES order kept
        used = 0
        for h in hits:
            if len(rows) == f.size:
                break
            used, last = used + 1, h["sort"]
            if (r := by_id.get(h["_source"]["doc_id"])) is not None:
                rows.append(r)
        more = used < len(hits) or len(hits) == size  # unread hits, or a full ES page: maybe more
        if len(rows) == f.size or not more or tries == BROWSE_TRIES:
            break
        top = {k: body[k] for k in ("query", "post_filter", "sort", "_source")}  # no aggs, no total
        release_before_io(session)
        top.update(size=size, search_after=last, track_total_hits=False)
        hits = _search(es, settings, top)["hits"]["hits"]
        if not hits:
            more = False
            break
    out["rows"] = rows
    out["next"] = make_sort_cursor(last) if more and last is not None else None
    return out


def _cited(
    session: Session,
    es: Elasticsearch,
    settings: Settings,
    p: Principal,
    policy: Policy,
    f: SearchParams,
    scope: DocScope,
) -> dict:
    """R2 cited window: the top `MAX_DOCS` head docs by `cited_by_global` (an upper bound, so `cited_min` never
    misses), then VISIBLE counts from Postgres; filter, sort and page in memory. `total` counts the kept rows
    (never ES `hits.total`, which would count invisible citers); with `cited_min` the facets and the user's sort
    come from a re-query over the kept documents only."""
    if f.after:
        raise HTTPException(422, "after does not apply to the cited window: use page")
    exact = f.cited_min is not None
    body: dict[str, Any] = {
        "query": _query(p, policy, f, scope),
        "sort": ES_SORTS["cited"],
        "_source": ["doc_id"],
        "size": MAX_DOCS,
        "track_total_hits": False,
    }
    if not exact:  # no cited filter: facets are the browse facets (corpus-wide, nothing citation-derived)
        body.update(post_filter=_post(f), aggs=facet_aggs(f, scope.colls))
    release_before_io(session)
    try:
        resp = _search(es, settings, body)
    except NotFoundError:
        return {**empty_body(f.page, policy), "rows": []}
    hits = resp["hits"]["hits"]
    ids = [h["_source"]["doc_id"] for h in hits]
    by_id = _rows(session, p, ids)  # R19
    counts = cited_by_counts(session, p, [r.doc.id for r in by_id.values()])
    # more visible qualifiers may lie beyond a full window only if its LAST doc still qualifies (visible counts)
    capped = len(ids) == MAX_DOCS and counts.get(ids[-1], 0) >= (f.cited_min or 1)
    ids = [i for i in ids if i in by_id and counts.get(i, 0) >= (f.cited_min or 0)]
    facets = read_facets(resp.get("aggregations", {}), policy, scope.colls)
    if exact:
        ids, facets = _exact(session, es, settings, p, policy, f, scope, ids)
    keep = [by_id[i] for i in ids]
    if f.sort == "cited":  # visible count desc, ties newest-added first, then id
        keep.sort(key=lambda r: str(r.doc.id))
        keep.sort(key=lambda r: r.doc.created_at, reverse=True)
        keep.sort(key=lambda r: counts.get(str(r.doc.id), 0), reverse=True)
    start = (f.page - 1) * f.size
    return {
        "rows": keep[start : start + f.size],
        "total": len(keep),
        "total_capped": capped,
        "next": None,
        "facets": facets,
    }


def _exact(session, es, settings, p, policy, f: SearchParams, scope, ids: list[str]):
    """Facet filters, the user's sort and disjunctive facets over the window docs that passed the visible filter."""
    if not ids:
        return [], empty_body(1, policy)["facets"]
    wide = f.model_copy(update={"cited_min": None})  # applied above, from visible citers
    body = {
        "query": _query(p, policy, wide, scope, extra=[{"terms": {"doc_id": ids}}]),
        "post_filter": _post(wide),
        "aggs": facet_aggs(wide, scope.colls),
        "sort": ES_SORTS[f.sort],
        "_source": ["doc_id"],
        "size": len(ids),
        "track_total_hits": False,
    }
    release_before_io(session)
    resp = _search(es, settings, body)
    kept = [h["_source"]["doc_id"] for h in resp["hits"]["hits"]]
    return kept, read_facets(resp.get("aggregations", {}), policy, scope.colls)


def find_values(
    session: Session,
    es: Elasticsearch,
    settings: Settings,
    p: Principal,
    policy: Policy,
    f: SearchParams,
    scope: DocScope,
    name: str,
    find: str,
) -> list[dict]:
    """Facet find box over the whole visible library under the browse filters (`q` ignored). `cited_min` is
    dropped: its ES field counts invisible citers, so filtering on it here would leak them."""
    wide = f.model_copy(update={"cited_min": None})
    body = {
        "query": _query(p, policy, wide, scope),
        "aggs": {"find": find_aggs(name, find, wide)},  # type: ignore[arg-type]
        "size": 0,
    }
    release_before_io(session)
    try:
        resp = _search(es, settings, body)
    except NotFoundError:
        return []
    buckets = resp.get("aggregations", {}).get("find", {}).get("v", {}).get("buckets", [])
    if name == "journal":
        return journal_rows(buckets)
    return [{"value": str(b["key"]), "count": b["doc_count"]} for b in buckets]
