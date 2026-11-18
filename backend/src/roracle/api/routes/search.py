"""GET /search: ES browse for empty queries (R3), legacy path until the search-fields reindex (R5), the hybrid
query path (search_ranked), quick title search; GET /search/facets/{name}: the facet find box."""

from typing import Annotated
from uuid import UUID

import httpx
from elasticsearch import Elasticsearch, NotFoundError
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from roracle import clients
from roracle.api.routes.library import get_es
from roracle.api.routes.search_load import (  # noqa: F401 (names re-exported for callers and tests)
    ROW_COLUMNS,
    Row,
    _hit,
    _is_uuid,
    _load,
    _own_collections,
    _votes,
    cursor_of,
    load_stmt,
)
from roracle.auth import Principal, current_principal, get_app_settings
from roracle.config import Settings
from roracle.db import get_session, release_before_io
from roracle.labels.policy import Policy, label_json
from roracle.onboarding import mark
from roracle.retrieval import Passage, embed_query
from roracle.search_acl import acl_filter
from roracle.search_browse import browse, decorate, empty_body, find_values
from roracle.search_fields import fields_ready
from roracle.search_index import active_index
from roracle.search_legacy import _query_rows, legacy_search
from roracle.search_limits import BadInput
from roracle.search_params import FindParams, SearchParams, uses_new_params
from roracle.search_query import parse_query
from roracle.search_ranked import ranked
from roracle.search_scope import resolve_scope

router = APIRouter()
Who = Annotated[Principal, Depends(current_principal)]
UPGRADING = "search index upgrade in progress"
_DEFAULT = SearchParams()
_QUICK_KEYS = {"q", "mode", "size", "notes", "sort"}


def _quick_empty() -> dict:
    return {"total": 0, "page": 1, "results": [], "facets": {"label": []}}


def get_llm(request: Request):
    app = request.app
    if not hasattr(app.state, "llm"):
        app.state.llm = clients.llm(app.state.settings)
    return app.state.llm


def get_http(request: Request) -> httpx.Client:
    """The process-wide keep-alive client created at app startup (`http_pool`)."""
    return request.app.state.http


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
        session, es, llm, settings, p, q, filters or {}, None, embed_query(llm, settings, q, p.user_id), raw
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
        return _quick_empty()
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


def _legacy_kwargs(f: SearchParams) -> dict:
    """The parameters the legacy body understands (callers check `uses_new_params` first)."""
    lists = {
        k: getattr(f, k) or None for k in ("tags", "file_type", "org_unit", "collection", "author", "label")
    }
    return {
        **lists,
        "q": f.q,
        "sort": f.sort,
        "page": f.page,
        "size": f.size,
        "notes": f.notes,
        "after": f.after,
    }


@router.get("/search")
def search(
    p: Who,
    session: Annotated[Session, Depends(get_session)],
    es: Annotated[Elasticsearch, Depends(get_es)],
    llm: Annotated[object, Depends(get_llm)],
    settings: Annotated[Settings, Depends(get_app_settings)],
    http: Annotated[httpx.Client, Depends(get_http)],
    f: Annotated[SearchParams, Query()],
) -> dict:
    if f.mode == "quick":
        if any(
            getattr(f, k) != getattr(_DEFAULT, k) for k in SearchParams.model_fields if k not in _QUICK_KEYS
        ):
            raise HTTPException(
                422, "mode=quick takes only q"
            )  # title search for the palette: no filters/paging
        if not f.q.strip():
            return _quick_empty()
        return _quick(session, es, settings, p, f.q.strip())
    if f.q.strip():
        mark(session, p.user_id, "search")
    if not fields_ready(es, settings):
        if uses_new_params(f):
            raise HTTPException(503, UPGRADING)
        return {**legacy_search(session, es, llm, settings, http, p, **_legacy_kwargs(f)), "upgrading": True}
    policy = Policy.load(session)
    try:
        pq = parse_query(f.q) if f.q.strip() else None
        scope = resolve_scope(session, p, f)
    except BadInput as e:
        raise HTTPException(422, str(e)) from None
    if scope is None:
        return empty_body(f.page, policy)
    if pq is not None:
        got = ranked(session, es, llm, settings, http, p, policy, f, scope, pq)
    else:
        got = browse(session, es, settings, p, policy, f, scope)
    out = {k: got[k] for k in ("total", "total_capped", "next", "facets")} | {"page": f.page}
    out["results"] = [_hit(r) for r in got["rows"]]
    decorate(session, p, policy, out["results"], f.notes)
    return out


@router.get("/search/facets/{name}")
def facet_find(
    p: Who,
    session: Annotated[Session, Depends(get_session)],
    es: Annotated[Elasticsearch, Depends(get_es)],
    settings: Annotated[Settings, Depends(get_app_settings)],
    name: str,
    f: Annotated[FindParams, Query()],
) -> dict:
    if name not in ("author", "venue", "tags", "journal"):
        raise HTTPException(404, "not found")
    if not fields_ready(es, settings):
        raise HTTPException(503, UPGRADING)
    policy = Policy.load(session)
    try:
        scope = resolve_scope(session, p, f, facets=False)
    except BadInput as e:
        raise HTTPException(422, str(e)) from None
    if scope is None:
        return {"values": []}
    return {"values": find_values(session, es, settings, p, policy, f, scope, name, f.find)}
