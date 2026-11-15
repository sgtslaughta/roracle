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
