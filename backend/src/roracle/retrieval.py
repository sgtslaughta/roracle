"""Hybrid retrieval: BM25 + kNN fused with RRF, optional rerank, vote boost, per-document grouping.

Every query carries `acl_filter` + `is_latest` (+ facet filters) in filter context; kNN uses its
`filter` param (pre-filter). Never post_filter: ACL must hold before ranking.
"""

import html
import logging
import math
import time
from dataclasses import dataclass, field
from typing import Any

import httpx
from elasticsearch import BadRequestError

from roracle import http_pool
from roracle.auth import Principal
from roracle.config import Settings
from roracle.labels.policy import Policy
from roracle.metrics import RERANK_FALLBACK, RERANK_SECONDS
from roracle.retrieval_util import _EMBEDS, TtlLru, _msearch, embed_query  # noqa: F401 (re-exported)
from roracle.search_acl import acl_filter
from roracle.search_index import active_index

log = logging.getLogger(__name__)

LIST_SIZE = 100
PASSAGES_PER_DOC = 2
FACET_FIELDS = {
    "tags": "tags",
    "year": "year",
    "file_type": "file_type",
    "org_unit": "org_unit",
    "author": "authors",
    "label": "label_id",
}
_SOURCE = [
    "chunk_id", "doc_id", "version_id", "text", "pages", "bboxes", "title", "authors", "year",
    "unit", "source_chunks",
]  # fmt: skip
LIGHT_SOURCE = ["chunk_id", "doc_id", "version_id", "title", "authors", "year"]  # facet universe: ids only
UNIT_KINDS = ("section", "document")


@dataclass
class Passage:
    chunk_id: str
    doc_id: str
    version_id: str
    text: str
    highlight: str
    pages: list[int]
    bboxes: list[dict]
    score: float
    title: str = ""
    authors: list[str] = field(default_factory=list)
    year: int | None = None
    note_id: str | None = None
    unit: str | None = None  # "section" | "document": a summary unit, never citable (expand_units first)
    source_chunks: list[str] = field(default_factory=list)


@dataclass
class DocHit:
    doc_id: str
    title: str
    authors: list[str]
    year: int | None
    score: float
    passages: list[Passage]


def rrf(lists: list[list[str]], k: int = 60) -> dict[str, float]:
    out: dict[str, float] = {}
    for ids in lists:
        for rank, i in enumerate(ids, start=1):
            out[i] = out.get(i, 0.0) + 1.0 / (k + rank)
    return out


def vote_ratio(up: int, down: int) -> float:
    return (up + 1) / (up + down + 2)


def vote_multiplier(up: int, down: int) -> float:
    return 1 + 0.15 * max(-1.0, min(1.0, (vote_ratio(up, down) - 0.5) * 2))


def _unit_filter(units: tuple[str, ...]) -> dict[str, Any]:
    """chunk = no `unit` field; section/document = that `unit` value. Absent `unit` mapping matches nothing."""
    should: list[dict[str, Any]] = []
    if "chunk" in units:
        should.append({"bool": {"must_not": [{"exists": {"field": "unit"}}]}})
    if kinds := [u for u in units if u in UNIT_KINDS]:
        should.append({"terms": {"unit": kinds}})
    return {"bool": {"should": should, "minimum_should_match": 1}}


UNIT_BM25_FACTOR = 0.6  # summary units repeat the title: demote them so one document cannot crowd the list


def _bm25_clause(query: str, units: tuple[str, ...]) -> dict[str, Any]:
    mm = {"multi_match": {"query": query, "fields": ["text^1", "title^2", "headings", "context"]}}
    if not any(u in UNIT_KINDS for u in units):
        return mm
    return {
        "boosting": {
            "positive": mm,
            "negative": {"exists": {"field": "unit"}},
            "negative_boost": UNIT_BM25_FACTOR,
        }
    }


def _filters(
    p: Principal,
    policy: Policy,
    filters: dict,
    doc_ids: list[str] | None,
    exclude_llm: bool,
    units: tuple[str, ...] = ("chunk",),
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = [acl_filter(p, policy), {"term": {"is_latest": True}}, _unit_filter(units)]
    if exclude_llm and (ids := policy.ids("exclude_llm")):  # gate (a): never let these chunks near a prompt
        out.append({"bool": {"must_not": [{"terms": {"label_id": sorted(ids)}}]}})
    for key, values in filters.items():
        if key not in FACET_FIELDS:
            raise ValueError(f"unknown filter: {key}")
        if values:
            out.append({"terms": {FACET_FIELDS[key]: list(values)}})
    if doc_ids is not None:
        out.append({"terms": {"doc_id": list(doc_ids)}})
    return out


def _passage(hit: dict, score: float = 0.0) -> Passage:
    s = hit["_source"]
    frags = hit.get("highlight", {}).get("text")
    text = s.get("text", "")  # absent in the light (ids-only) facet-universe pass
    return Passage(
        chunk_id=s["chunk_id"],
        doc_id=s["doc_id"],
        version_id=s["version_id"],
        text=text,
        highlight=frags[0] if frags else html.escape(text[:240]),
        pages=s.get("pages") or [],
        bboxes=s.get("bboxes") or [],
        score=score,
        title=s.get("title") or "",
        authors=s.get("authors") or [],
        year=s.get("year"),
        unit=s.get("unit"),
        source_chunks=s.get("source_chunks") or [],
    )


def _rerank_request(settings: Settings, query: str, texts: list[str]) -> dict:
    if settings.rerank_api == "tei":
        return {"query": query, "texts": texts, "truncate": True, "raw_scores": False}
    return {"model": settings.rerank_model, "query": query, "documents": texts}


def _rerank_scores(settings: Settings, body: Any, n: int) -> list[float]:
    """Score per input index from one response. Raises ValueError/KeyError/TypeError if malformed
    (wrong shape, indices not exactly 0..n-1, non-finite scores)."""
    results = body if settings.rerank_api == "tei" else body["results"]
    key = "score" if settings.rerank_api == "tei" else "relevance_score"
    out: dict[int, float] = {}
    for o in results:
        i, sc = o["index"], o[key]
        if type(i) is not int or not 0 <= i < n or i in out:
            raise ValueError("malformed rerank result")
        if isinstance(sc, bool) or not isinstance(sc, int | float) or not math.isfinite(sc):
            raise ValueError("malformed rerank score")
        out[i] = float(sc)
    if len(out) != n:
        raise ValueError("incomplete rerank result")
    return [out[i] for i in range(n)]


def _rerank_order(settings: Settings, body: Any) -> list[int]:
    """Indices best-first from a rerank response body. Raises ValueError/KeyError/TypeError if malformed."""
    results = body if settings.rerank_api == "tei" else body["results"]
    sc = _rerank_scores(settings, body, len(results))
    return sorted(range(len(sc)), key=lambda i: -sc[i])


def _rerank_scored(
    settings: Settings, query: str, texts: list[str], http: httpx.Client | None, headers: dict, t0: float
) -> list[float]:
    """Scores for all texts, in requests of <= rerank_max_batch; one wall-clock budget (rerank_timeout_s) overall."""
    step = max(1, settings.rerank_max_batch)
    scores: list[float] = []
    shared = http or http_pool.get()  # the process-wide keep-alive client (app/worker startup), if any
    client = shared or httpx.Client()
    try:
        for i in range(0, len(texts), step):
            left = settings.rerank_timeout_s - (time.monotonic() - t0)
            if left <= 0:
                raise httpx.ReadTimeout("rerank budget exhausted")
            chunk = texts[i : i + step]
            r = client.post(
                settings.rerank_url,
                json=_rerank_request(settings, query, chunk),
                headers=headers,
                timeout=httpx.Timeout(left, connect=min(left, 1.0)),
            )
            r.raise_for_status()
            scores += _rerank_scores(settings, r.json(), len(chunk))
    finally:
        if shared is None:
            client.close()
    return scores


def _minmax(xs: list[float]) -> list[float]:
    finite = [x for x in xs if math.isfinite(x)]
    floor = min(finite) if finite else 0.0
    xs = [x if math.isfinite(x) else floor for x in xs]  # a NaN/inf score must not poison min/max or the sort
    lo, hi = min(xs), max(xs)
    return [0.0 if hi == lo else (x - lo) / (hi - lo) for x in xs]


def blend_order(fused: list[float], rerank: list[float], blend: float) -> list[int]:
    """Indices best-first by `blend * minmax(rerank) + (1 - blend) * minmax(fused)` (per query). blend 1.0
    replaces the first-stage score; the stable sort keeps the first-stage order on ties."""
    f, r = _minmax(fused), _minmax(rerank)
    score = [blend * r[i] + (1 - blend) * f[i] for i in range(len(f))]
    return sorted(range(len(score)), key=lambda i: -score[i])


def _rerank(
    settings: Settings,
    query: str,
    passages: list[Passage],
    http: httpx.Client | None,
    depth: int | None = None,
) -> list[Passage]:
    depth = settings.rerank_depth if depth is None else depth
    head = passages[:depth]
    if not head:
        return passages
    headers = {"Authorization": f"Bearer {settings.rerank_api_key}"} if settings.rerank_api_key else {}
    t0 = time.monotonic()
    try:
        # the title disambiguates short chunks ("the results show ..."): send it with the text
        texts = [
            f"{x.title}\n{x.text[: settings.rerank_max_chars]}"
            if x.title
            else x.text[: settings.rerank_max_chars]
            for x in head
        ]
        sc = _rerank_scored(settings, query, texts, http, headers, t0)
        idx = blend_order([x.score for x in head], sc, settings.rerank_blend)
        scores = sorted((x.score for x in head), reverse=True)  # keep order under later score sorts
        reranked = [head[i] for i in idx]
        for x, v in zip(reranked, scores, strict=True):
            x.score = v
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as e:
        if isinstance(e, httpx.TimeoutException):
            reason = "timeout"
        elif isinstance(e, httpx.HTTPError):
            reason = "http"
        else:
            reason = "malformed"
        RERANK_FALLBACK.labels(reason).inc()
        status = e.response.status_code if isinstance(e, httpx.HTTPStatusError) else ""
        log.warning("rerank failed (%s %s), keeping fused order: %s", reason, status, type(e).__name__)
        return passages
    finally:
        RERANK_SECONDS.observe(time.monotonic() - t0)
    return reranked + passages[depth:]


def search_chunks(
    es,
    llm,
    settings: Settings,
    p: Principal,
    query: str,
    *,
    policy: Policy,
    filters: dict,
    doc_ids: list[str] | None = None,
    size: int = LIST_SIZE,
    http: httpx.Client | None = None,
    qvec: list[float] | None = None,
    min_similarity: float | None = None,
    summary_knn: bool = True,
    exclude_llm: bool = False,
    units: tuple[str, ...] = ("chunk",),
    rerank_depth: int | None = None,
    light: bool = False,
) -> list[Passage]:
    """Fused passages, best first. Raises ValueError on an empty query (callers browse instead).

    BM25, kNN and (summary_knn) the summary kNN go out in ONE `_msearch` round trip; RRF is client-side.
    summary_knn adds a third kNN list on the document summary vector (doc-level recall for search);
    only chunk 0 carries it, so a hit means "this document", not "this passage".
    exclude_llm=True drops chunks whose label has the `exclude_llm` gate (every LLM-bound caller sets it).
    units picks the retrieval units: "chunk" (default, library search unchanged), "section", "document"
    (summary units: LLM-bound callers must `expand_units` the result before use).
    rerank_depth overrides `settings.rerank_depth` (search uses the smaller `search_rerank_depth`).
    light=True is the facet-universe pass: ids only (no text/bboxes/highlight), never reranked.
    """
    if not query.strip():
        raise ValueError("empty query")
    flt = _filters(p, policy, filters, doc_ids, exclude_llm, units)
    index = active_index(settings)
    source = LIGHT_SOURCE if light else _SOURCE
    vec = qvec if qvec is not None else embed_query(llm, settings, query, p.user_id)

    def knn_body(field: str) -> dict:
        knn_q: dict[str, Any] = {
            "field": field,
            "query_vector": vec,
            "k": size,
            "num_candidates": 4 * size,
            "filter": flt,
        }
        if min_similarity:
            knn_q["similarity"] = min_similarity
        return {"size": size, "_source": source, "knn": knn_q}

    bm25_body: dict[str, Any] = {
        "size": size,
        "_source": source,
        "query": {"bool": {"filter": flt, "must": [_bm25_clause(query, units)]}},
    }
    if not light:
        bm25_body["highlight"] = {
            "fields": {"text": {"fragment_size": 200, "number_of_fragments": 1}},
            "encoder": "html",
        }
    bodies = [bm25_body, knn_body("embedding"), *([knn_body("summary_embedding")] if summary_knn else [])]
    res = _msearch(es, index, bodies)
    for r in res[:2]:
        if isinstance(r, Exception):
            raise r
    bm25, knn = (r["hits"]["hits"] for r in res[:2])
    sknn: list[dict] = []
    if summary_knn:
        r = res[2]
        if isinstance(
            r, BadRequestError
        ):  # index predates summary_embedding and ensure_index has not run yet
            log.warning("summary kNN skipped: %s", r)
        elif isinstance(r, Exception):
            raise r
        else:
            sknn = r["hits"]["hits"]
    by_id = {h["_source"]["chunk_id"]: h for h in [*sknn, *knn]}
    by_id.update({h["_source"]["chunk_id"]: h for h in bm25})  # BM25 hit carries the highlight
    fused = rrf([[h["_source"]["chunk_id"] for h in x] for x in (bm25, knn, sknn)])
    ranked = sorted(fused, key=lambda c: -fused[c])
    out = [_passage(by_id[c], fused[c]) for c in ranked]
    if settings.rerank_url and not light:
        out = _rerank(settings, query, out, http, rerank_depth)
    return out


def group_by_document(passages: list[Passage], votes: dict[str, tuple[int, int]]) -> list[DocHit]:
    """Passages must be best-first. Doc score = best passage score x vote multiplier."""
    groups: dict[str, list[Passage]] = {}
    for x in passages:
        groups.setdefault(x.doc_id, []).append(x)
    hits = []
    for doc_id, ps in groups.items():
        best = max(ps, key=lambda x: x.score)
        top = sorted(ps, key=lambda x: -x.score)[:PASSAGES_PER_DOC]
        up, down = votes.get(doc_id, (0, 0))
        hits.append(
            DocHit(doc_id, best.title, best.authors, best.year, best.score * vote_multiplier(up, down), top)
        )
    return sorted(hits, key=lambda h: -h.score)


def _overlap(query: str, text: str) -> int:
    q = {w for w in query.casefold().split() if len(w) > 2}
    return len(q & set(text.casefold().split()))


MAX_EXPAND_UNITS = 24  # top units expanded per call; the rest are dropped (never shown as summaries)
CANDS_PER_UNIT = 12  # source chunks fetched per unit (evenly strided over its sources)


def expand_units(
    es,
    settings: Settings,
    p: Principal,
    policy: Policy,
    query: str,
    passages: list[Passage],
    *,
    exclude_llm: bool,
    per_unit: int = 2,
    http: httpx.Client | None = None,
    max_units: int = MAX_EXPAND_UNITS,
) -> list[Passage]:
    """R11: replace summary units with up to `per_unit` of their own source chunks, so only real passages
    (real chunk ids and pages) reach the LLM and citations. Cost is constant per call: ONE `ids` search for the
    candidate sources of the top `max_units` units, ONE rerank over their union. The fetch re-applies the ACL +
    label filters; every candidate must be a source of a unit of the SAME document and version, else it is
    dropped. A unit without surviving sources is dropped. Chunks already present keep their place (max score)."""
    out: dict[str, Passage] = {x.chunk_id: x for x in passages if not x.unit}
    units = sorted((x for x in passages if x.unit and x.source_chunks), key=lambda x: -x.score)[:max_units]
    if not units:
        return sorted(out.values(), key=lambda x: -x.score)
    wanted: dict[str, list[Passage]] = {}  # source chunk id -> units listing it
    for u in units:
        src = u.source_chunks
        for cid in src[:: max(1, -(-len(src) // CANDS_PER_UNIT))][:CANDS_PER_UNIT]:
            wanted.setdefault(cid, []).append(u)
    hits = es.search(
        index=active_index(settings),
        size=len(wanted),
        source=_SOURCE,
        query={
            "bool": {
                "filter": [
                    *_filters(p, policy, {}, None, exclude_llm),
                    {"ids": {"values": list(wanted)}},
                    {"terms": {"doc_id": sorted({u.doc_id for u in units})}},
                ]
            }
        },
    )["hits"]["hits"]
    cands = [_passage(h, 0.0) for h in hits if not h["_source"].get("unit")]
    cands = [
        c
        for c in cands
        if any(u.doc_id == c.doc_id and u.version_id == c.version_id for u in wanted.get(c.chunk_id, []))
    ]
    cands.sort(key=lambda c: -_overlap(query, c.text))  # lexical order when no reranker answers
    if settings.rerank_url:
        cands = _rerank(settings, query, cands, http)
    taken: dict[int, int] = {}
    for c in cands:
        for u in wanted[c.chunk_id]:
            if u.doc_id != c.doc_id or u.version_id != c.version_id or taken.get(id(u), 0) >= per_unit:
                continue
            taken[id(u)] = taken.get(id(u), 0) + 1
            old = out.get(c.chunk_id)
            if old is None:
                c.score = u.score
                out[c.chunk_id] = c
            else:
                old.score = max(old.score, u.score)
    return sorted(out.values(), key=lambda x: -x.score)
