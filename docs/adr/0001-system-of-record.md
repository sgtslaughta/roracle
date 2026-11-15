# ADR-0001: Postgres is the source of truth; Elastic is a rebuildable index

Status: Accepted (2026-11-15)

## Context
Users, ownership, ACLs, versions, votes, notes and settings are relational data with integrity rules.
Elastic has no foreign keys or multi-document transactions, and restoring it is slow. At ~50M chunks,
Postgres-only search (pgvector + BM25 extension) would strain a single primary.

## Decision
- Postgres (CloudNativePG) holds all metadata and the job queue.
- MinIO (S3 API) holds file bytes, addressed by SHA-256.
- Elasticsearch (ECK) holds chunks, vectors and search-only projections. It can be fully rebuilt
  from Postgres + MinIO by re-running indexing jobs.

## Consequences
- Three stateful systems to run HA; Elastic loss is an outage, never data loss.
- Every write goes to Postgres first; indexing is asynchronous (jobs).
