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

## Update (2026-11-17)

Superseded in part by [ADR-0016](0016-terraform-iac.md). Read "MinIO" above as "the S3-compatible object store". Community MinIO images ended in October 2025, so production uses Rook-Ceph RGW (in-cluster) or an external S3 you operate. The Helm chart no longer has any object-store mode (chart 0.2.0): it only takes an S3 endpoint (`ROR_S3_ENDPOINT` is required, `backend/src/roracle/config.py:56`). Postgres stays the source of truth, and Elasticsearch is still rebuilt from Postgres plus the object store.
