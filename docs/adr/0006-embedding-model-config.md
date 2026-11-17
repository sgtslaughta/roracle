# ADR-0006: Embedding model is configuration

Status: Accepted (2026-11-15)

## Context

Candidates differ by 5× in vector size (EmbeddingGemma-300m 768-d vs e5-mistral-7b 4096-d), which
drives Elastic sizing more than anything else.

## Decision

Admins pick the embedding model. Each model gets its own index (`chunks-<model>-<n>`) behind the
alias `chunks`. Switching runs a background re-embed into a new index, then flips the alias.
Each chunk records `embedding_model`. Default to the smallest good model (EmbeddingGemma 768-d);
use int8 HNSW quantization for large models.

## Consequences

- Zero-downtime model changes; temporary double storage during re-embed.

## Update (2026-11-17)

Elasticsearch is 9.5.6 (it was 8.15 when this was written). Vector `index_options` are always explicit; the default is `int8_hnsw` (`ROR_VECTOR_INDEX_TYPE`, `config.py:93`). `bbq_hnsw` is available but eval-gated: it needs a recall A/B through the eval harness before anyone adopts it, and `int4_hnsw` is the fallback. The setting applies when an index is created; an existing index keeps its own. See [ADR-0015](0015-retrieval-quality.md), Outcomes.
