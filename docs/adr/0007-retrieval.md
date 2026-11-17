# ADR-0007: Hybrid retrieval with app-side fusion, rerank and capped vote boost

Status: Accepted (2026-11-15)

## Decision

- BM25 and kNN queries (both ACL-filtered) fused with RRF (k = 60) in the API; top 50 reranked by the
  reranker model; chat keeps top 8–10.
- Community score = smoothed vote ratio `(up + 1) / (up + down + 2)`, mapped to a multiplier
  `1 + 0.15 × clamp((ratio − 0.5) × 2, −1, 1)` (amended 2026-11-15: the Wilson lower bound ranked
  documents with 1–3 upvotes below unvoted ones). Votes refine relevance, never override it.
- Results grouped per document; facets computed from the result set only.

## Consequences

- Fusion and boost logic is plain Python and unit-tested without Elastic.

## Update (2026-11-17)

Rerank depth and the reranker are amended by [ADR-0015](0015-retrieval-quality.md). `/search` reranks the top `ROR_SEARCH_RERANK_DEPTH` (50); chat and other LLM-bound callers rerank `ROR_RERANK_DEPTH` (200) candidates and keep `ROR_CHAT_PASSAGES` (8, clamped to 4-12) passages. The reranker is optional: an empty `ROR_RERANK_URL` turns it off. By user ruling the reranker may score `exclude_llm` passages (it is a scorer, not a generator), see [known limits](../enterprise/security/known-limits.md). RRF `k` = 60 and the vote multiplier (`retrieval.py:74,87`) are unchanged.
