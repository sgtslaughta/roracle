"""Non-empty-query path (R1, R4, R6, R7): hybrid first stage with every ES-side filter pushed down, the Postgres
re-check (R19), votes/cited post-filters from Postgres, in-memory sorts, and exact facet counts over the re-checked
candidates (one ACL-first `size=0` request on their head docs)."""

from typing import Any
from uuid import UUID

import httpx
from elasticsearch import Elasticsearch, NotFoundError
from fastapi import HTTPException
from sqlalchemy.orm import Session

from roracle.api.routes.search_load import Row, _is_uuid, _load, _votes
from roracle.auth import Principal
from roracle.citations import cited_by_counts
from roracle.config import Settings
from roracle.db import release_before_io
from roracle.labels.policy import Policy
from roracle.retrieval import DocHit, embed_query, group_by_document, search_chunks
from roracle.search_browse import _query, _search, empty_body
from roracle.search_cache import PAGE_CACHE, PAGE_CACHE_MAX, cache_key, rehydrate, slim
from roracle.search_facets import facet_aggs, read_facets
from roracle.search_filters import facet_clauses, plain_clauses
from roracle.search_limits import MAX_DOCS
from roracle.search_params import SearchParams, cache_tuple
from roracle.search_post import post_filter, sort_rows
from roracle.search_query import ParsedQuery, bm25_clause, embed_text, head_only, text_clauses
from roracle.search_scope import DocScope

UNITS = ("chunk",)  # library search: chunks only (summary units are for LLM-bound callers)
FIRST_STAGE = 100  # chunks per list


class _Stage:
    """One request's first-stage context: everything but the ES clauses and the light flag."""

    def __init__(self, session, es, llm, settings, http, p, policy, f, scope, pq):
        self.session, self.es, self.llm, self.settings, self.http = session, es, llm, settings, http
        self.p, self.policy, self.f, self.scope, self.pq = p, policy, f, scope, pq
        self.knn = not head_only(f.in_)
        self.qvec = None
        self.capped = False
        if self.knn:
            release_before_io(session)  # no pooled connection held idle across the embed call
            self.qvec = embed_query(llm, settings, embed_text(pq), p.user_id)

    def rows(self, flt: list[dict], nn: list[dict], light: bool) -> list[Row]:
        """ES first stage (cached for `search_cache_s`) + the Postgres re-check on EVERY request (R19)."""
        s, f = self.settings, self.f
        extra = (cache_tuple(f), tuple(sorted(self.scope.exclude)))
        key = cache_key(self.p, s, f.q, {}, self.scope.include, light, self.policy, extra=extra)
        use_cache = s.search_cache_s > 0
        cached = PAGE_CACHE.get(key, s.search_cache_s) if use_cache else None
        if cached is not None:
            passages = rehydrate(cached)
        else:
            release_before_io(self.session)
            try:
                passages = search_chunks(
                    self.es, self.llm, s, self.p, embed_text(self.pq), policy=self.policy, filters={},
                    size=FIRST_STAGE, http=self.http, qvec=self.qvec, rerank_depth=s.search_rerank_depth,
                    min_similarity=s.search_min_similarity or None, light=light, units=UNITS,
                    bm25=bm25_clause(self.pq, f.in_, UNITS), extra_filter=flt, must_not=nn, knn=self.knn,
                )  # fmt: skip
            except NotFoundError:  # nothing ingested yet: no index
                return []
            if use_cache and passages:  # never cache "nothing": a fresh grant must show up at once
                PAGE_CACHE.put(key, slim(passages), PAGE_CACHE_MAX)
        ids = [UUID(x.doc_id) for x in passages if _is_uuid(x.doc_id)]
        if not ids:
            return []
        votes = _votes(self.session, ids, self.p.user_id)
        hits: list[DocHit] = group_by_document(passages, {k: (v[0], v[1]) for k, v in votes.items()})
        by_id = {str(r.doc.id): r for r in _load(self.session, self.p, ids, votes=votes)}
        out = []
        for h in hits[: MAX_DOCS + 1]:  # one past the window: is there a VISIBLE candidate beyond it?
            if (r := by_id.get(h.doc_id)) is None:
                continue
            live = [x for x in h.passages if x.version_id == str(r.ver.id)]
            if live:  # only stale snippets: a newer version became latest after the first stage ran
                r.score, r.passages = h.score, live
                out.append(r)
        self.capped = len(out) > MAX_DOCS  # Postgres-visible rows only, nothing citation-derived (R2)
        return out[:MAX_DOCS]


def _facets(st: _Stage, universe: list[Row]) -> dict[str, list[dict]]:
    """R4: disjunctive counts over the head docs of the re-checked universe, ACL first (`_query`). Votes and
    cited were applied in Postgres; their ES fields (stale / counting invisible citers) are never used here."""
    if not universe:
        return empty_body(1, st.policy)["facets"]
    wide = st.f.model_copy(update={"votes_min": None, "cited_min": None})
    ids = sorted({str(r.doc.id) for r in universe})
    body: dict[str, Any] = {
        "query": _query(st.p, st.policy, wide, st.scope, extra=[{"terms": {"doc_id": ids}}]),
        "aggs": facet_aggs(wide, st.scope.colls),
        "size": 0,
        "track_total_hits": False,
    }
    release_before_io(st.session)
    try:
        resp = _search(st.es, st.settings, body)
    except NotFoundError:
        return empty_body(1, st.policy)["facets"]
    return read_facets(resp.get("aggregations", {}), st.policy, st.scope.colls)


def ranked(
    session: Session,
    es: Elasticsearch,
    llm,
    settings: Settings,
    http: httpx.Client | None,
    p: Principal,
    policy: Policy,
    f: SearchParams,
    scope: DocScope,
    pq: ParsedQuery,
) -> dict:
    """`{rows, total, total_capped, next, facets}` of one query page. `total` counts the kept candidates
    (≤ MAX_DOCS), never an ES count; `total_capped` when a visible candidate lay beyond the window."""
    if f.after:
        raise HTTPException(422, "after applies to browsing (empty q) only: use page")
    if (
        scope.include is not None and not scope.include
    ):  # an empty collection / citer scope: nothing to search
        return {**empty_body(f.page, policy), "rows": []}
    fac_flt, fac_nn = facet_clauses(f)
    pl_flt, pl_nn = plain_clauses(f, p, scope)  # carries the scope's doc ids
    tx_flt, tx_nn = text_clauses(pq, f.in_)
    flt = [*pl_flt, *tx_flt, *([{"term": {"doc_head": True}}] if head_only(f.in_) else [])]
    nn = [*pl_nn, *tx_nn]
    st = _Stage(session, es, llm, settings, http, p, policy, f, scope, pq)
    rows = st.rows([*fac_flt, *flt], [*fac_nn, *nn], light=False)
    capped, universe = st.capped, rows
    if fac_flt or fac_nn:  # facet universe: every non-facet clause, no facet clause (light: ids only)
        universe = st.rows(flt, nn, light=True)
        seen = {r.doc.id for r in universe}
        universe += [r for r in rows if r.doc.id not in seen]
    cited: dict[str, int] = {}
    if f.cited_min is not None or f.sort == "cited":  # visible citers only (R2)
        cited = cited_by_counts(session, p, list({r.doc.id for r in universe}))
    rows, universe = post_filter(rows, f, cited), post_filter(universe, f, cited)
    sort_rows(rows, f.sort, cited)
    start = (f.page - 1) * f.size
    return {
        "rows": rows[start : start + f.size],
        "total": len(rows),
        "total_capped": capped,
        "next": None,
        "facets": _facets(st, universe),
    }
