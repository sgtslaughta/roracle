"""Hybrid retrieval: BM25 + kNN fused with RRF, optional rerank, vote boost, per-document grouping.

Every query carries `acl_filter` + `is_latest` (+ facet filters) in filter context; kNN uses its
`filter` param (pre-filter). Never post_filter: ACL must hold before ranking.
"""

import html
import logging
from dataclasses import dataclass, field
from typing import Any

import httpx
from elasticsearch import BadRequestError

from roracle.auth import Principal
from roracle.config import Settings
from roracle.ingest.embed import embed, query_prompt
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
}
_SOURCE = ["chunk_id", "doc_id", "version_id", "text", "pages", "bboxes", "title", "authors", "year"]


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


def _filters(p: Principal, filters: dict, doc_ids: list[str] | None) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = [acl_filter(p), {"term": {"is_latest": True}}]
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
    return Passage(
        chunk_id=s["chunk_id"],
        doc_id=s["doc_id"],
        version_id=s["version_id"],
        text=s["text"],
        highlight=frags[0] if frags else html.escape(s["text"][:240]),
        pages=s.get("pages") or [],
        bboxes=s.get("bboxes") or [],
        score=score,
        title=s.get("title") or "",
        authors=s.get("authors") or [],
        year=s.get("year"),
    )


def _rerank(
    settings: Settings, query: str, passages: list[Passage], http: httpx.Client | None
) -> list[Passage]:
    head = passages[: settings.rerank_depth]
    if not head:
        return passages
    try:
        payload = {"model": settings.rerank_model, "query": query, "documents": [x.text for x in head]}
        if http is not None:
            r = http.post(settings.rerank_url, json=payload)
        else:
            with httpx.Client(timeout=5.0) as client:
                r = client.post(settings.rerank_url, json=payload)
        r.raise_for_status()
        order = sorted(r.json()["results"], key=lambda x: -x["relevance_score"])
        idx = [o["index"] for o in order]
        if not all(type(i) is int and 0 <= i < len(head) for i in idx) or len(set(idx)) != len(head):
            raise ValueError("malformed rerank result")
        if len(idx) != len(head):
            raise ValueError("incomplete rerank result")
        scores = sorted((x.score for x in head), reverse=True)  # keep order under later score sorts
        reranked = [head[i] for i in idx]
        for x, sc in zip(reranked, scores, strict=True):
            x.score = sc
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as e:
        log.warning("rerank failed, keeping fused order: %s", e)
        return passages
    return reranked + passages[settings.rerank_depth :]


def embed_query(llm, settings: Settings, query: str) -> list[float]:
    [vec] = embed(llm, settings.embed_model, [query_prompt(query)], settings.embed_dims)
    return vec


def search_chunks(
    es,
    llm,
    settings: Settings,
    p: Principal,
    query: str,
    *,
    filters: dict,
    doc_ids: list[str] | None = None,
    size: int = LIST_SIZE,
    http: httpx.Client | None = None,
    qvec: list[float] | None = None,
    min_similarity: float | None = None,
    summary_knn: bool = True,
) -> list[Passage]:
    """Fused passages, best first. Raises ValueError on an empty query (callers browse instead).

    summary_knn adds a third kNN list on the document summary vector (doc-level recall for search);
    only chunk 0 carries it, so a hit means "this document", not "this passage".
    """
    if not query.strip():
        raise ValueError("empty query")
    flt = _filters(p, filters, doc_ids)
    index = active_index(settings)
    bm25 = es.search(
        index=index,
        size=size,
        source=_SOURCE,
        query={
            "bool": {
                "filter": flt,
                "must": [{"multi_match": {"query": query, "fields": ["text^1", "title^2", "headings"]}}],
            }
        },
        highlight={"fields": {"text": {"fragment_size": 200, "number_of_fragments": 1}}, "encoder": "html"},
    )["hits"]["hits"]
    vec = qvec if qvec is not None else embed_query(llm, settings, query)

    def knn_search(field: str) -> list[dict]:
        knn_q: dict[str, Any] = {
            "field": field,
            "query_vector": vec,
            "k": size,
            "num_candidates": 4 * size,
            "filter": flt,
        }
        if min_similarity:
            knn_q["similarity"] = min_similarity
        return es.search(index=index, size=size, source=_SOURCE, knn=knn_q)["hits"]["hits"]

    knn = knn_search("embedding")
    try:
        sknn = knn_search("summary_embedding") if summary_knn else []
    except BadRequestError as e:  # index predates summary_embedding and ensure_index has not run yet
        log.warning("summary kNN skipped: %s", e)
        sknn = []
    by_id = {h["_source"]["chunk_id"]: h for h in [*sknn, *knn]}
    by_id.update({h["_source"]["chunk_id"]: h for h in bm25})  # BM25 hit carries the highlight
    fused = rrf([[h["_source"]["chunk_id"] for h in x] for x in (bm25, knn, sknn)])
    ranked = sorted(fused, key=lambda c: -fused[c])
    out = [_passage(by_id[c], fused[c]) for c in ranked]
    if settings.rerank_url:
        out = _rerank(settings, query, out, http)
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
