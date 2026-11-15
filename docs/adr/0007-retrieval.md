# ADR-0007: Hybrid retrieval with app-side fusion, rerank and capped vote boost

Status: Accepted (2026-11-15)

## Decision
- BM25 and kNN queries (both ACL-filtered) fused with RRF (k = 60) in the API; top 50 reranked by the
  reranker model; chat keeps top 8–10.
- Community score = Wilson lower bound (z = 1.96) of up/down votes, mapped to a multiplier
  `1 + 0.15 × clamp((wilson − 0.5) × 2, −1, 1)`. Votes refine relevance, never override it.
- Results grouped per document; facets computed from the result set only.

## Consequences
- Fusion and boost logic is plain Python and unit-tested without Elastic.
