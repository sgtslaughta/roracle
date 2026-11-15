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

## Addendum (2026-11-15, P1 final review)

- `run_once` claims, runs the handler inside a SAVEPOINT, and records `complete`/`fail` in one transaction. The claim's row lock is held for the whole job, so a crashed worker's transaction aborts and the job is `queued` again — no stale-job reaper.
- Handlers must never commit the session they are given. Progress that must survive a crash (ingest `stage`) is written through a separate short session.
- A process crash does not bump `jobs.attempts`; handlers that can crash the process bound retries themselves (ingest: `document_versions.ingest_attempts`).
- Trade-off: a job's transaction stays open for its duration (minutes for large PDFs). Revisit with leases + heartbeat if vacuum lag or connection count becomes a problem.
