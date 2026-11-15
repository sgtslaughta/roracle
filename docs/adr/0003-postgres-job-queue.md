# ADR-0003: Own job table in Postgres instead of a message broker

Status: Accepted (2026-11-15)

## Context
Ingest is ~10k docs/day (~7/min). Reliability matters more than throughput: an upload must never
exist without its job, or a job without its upload. Libraries like procrastinate use their own
connection, so they cannot share the API's SQLAlchemy transaction.

## Decision
A `jobs` table (`id, kind, payload jsonb, status, attempts, run_after, last_error, timestamps`).
Enqueue = INSERT in the caller's transaction. Workers claim with
`SELECT … FOR UPDATE SKIP LOCKED LIMIT 1`, retry with exponential backoff, max 5 attempts.
Valkey is optional (cache/rate limits), never the queue.

## Consequences
- No broker to run HA. Queue depth is a SQL query (metrics, admin dashboard).
- Revisit (RabbitMQ) only if queue metrics show Postgres is the bottleneck.
