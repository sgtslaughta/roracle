# ADR-0005: Ingestion pipeline and contextual chunk embeddings

Status: Accepted (2026-11-15)

## Context
All document types must be supported, including scans. Facts mentioned deep inside a document must
surface in search. Everything must run offline.

## Decision
Stages run as jobs: ClamAV scan → exact dedup → Gotenberg (office → PDF) → Docling parse (layout,
OCR, tables, page + bbox) + GROBID (metadata, references) → LLM summary → near-dup check →
Docling `HybridChunker` (~400–500 tokens, embedding tokenizer, structure-aware) → embed with a
context prefix (title, section path, summary) → index → reference linking.
Whole-document embeddings are used only for "papers about X" and near-dup, never as the only path.

## Consequences
- Every model and tokenizer must be mirrored; nothing downloads weights at runtime.
- Each stage is retryable on its own; users see per-stage status.
