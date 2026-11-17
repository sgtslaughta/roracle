# Connection and thread budget (Task 10.9, H3)

## Rules the code follows

- **No pooled connection is held across slow external calls.** Request sessions (the API's session factory sets
  `release_before_io`) `commit()` right before the query embedding, the ES `msearch`, the reranker and the LLM
  embedding in `/api/search`, chat retrieval and the global-answer candidates. A committed session returns its connection
  to the pool and checks one out again on its next statement. Worker/eval sessions never do this (they run inside the
  job transaction, ADR-0003). The chat SSE stream already commits before streaming and uses its own session.
- **Explicit pools** (`ROR_DB_POOL_SIZE` / `ROR_DB_MAX_OVERFLOW` for the API, `ROR_WORKER_DB_POOL_SIZE` /
  `ROR_WORKER_DB_MAX_OVERFLOW` for workers, `ROR_DB_POOL_TIMEOUT_S`). Defaults 8+4 for the API and 3+1 for a worker, PLUS a separate one-connection engine for the lease heartbeat (it must never wait behind handler sessions): a worker holds at most handler session + dedupe/lock session + stage-write session (3) from its pool and 1 heartbeat connection.
- **Threads:** every sync route and every streamed answer holds one anyio worker thread for its whole run (a blocking
  LLM stream cannot yield it). anyio's default is 40 tokens, so ~40 concurrent chats would stall `/api/search`, MCP and the
  readiness probes. The lifespan raises the limiter to `ROR_API_THREADS` (256: 200 concurrent chats + headroom for search/MCP). `/healthz` is `async` and needs no thread.
  Threads are cheap (stack only); the DB pool, not the thread count, bounds database load: a request waiting for a
  connection fails after `ROR_DB_POOL_TIMEOUT_S`.

- **Rule for new code:** no session writes before `release_before_io`; anything written earlier in the request is
  committed even if the request later fails. Worker/eval sessions are never flagged (`global_answer.reduce_inputs` and
  every shared retrieval helper go through `release_before_io`, never a bare `commit`).
- **Agentic sub-queries (`agentic_retrieval`, off by default):** a planning round runs its sub-queries in parallel, each on its
  own session from the API's session factory: up to `ROR_AGENTIC_MAX_PARALLEL` (4) extra pooled connections per API
  process, process-wide, on top of the chat stream's own session. They come out of the same pool, so the per-process
  maximum stays `pool + overflow` (12) and the chart formula below is unchanged; a burst that finds the pool empty waits
  up to `ROR_DB_POOL_TIMEOUT_S` and the affected sub-query is dropped (the answer uses what the other retrievals found).
  Workers never run the loop in parallel (their pool is 3+1): the `eval_run` job stays sequential; `roracle eval run|compare`
  (its own engine, default pool) runs it in parallel like production.

## Formula

```text
clients     = api_max_replicas x (ROR_DB_POOL_SIZE + ROR_DB_MAX_OVERFLOW)
            + worker_max_replicas x (ROR_WORKER_DB_POOL_SIZE + ROR_WORKER_DB_MAX_OVERFLOW)
direct      : clients + postgres.reservedConnections            <= postgres.parameters.max_connections
pooled      : pooler.instances x defaultPoolSize + reserved     <= max_connections
              clients                                            <= pooler.instances x maxClientConn
```

Chart defaults (`values.yaml`): API 10 x 12 = 120, workers 12 x (3+1+1) = 60, reserved 15 -> 195 <= `max_connections` 200.
`helm template` fails with the numbers when the budget is exceeded (so CI catches a values change). Each idle backend costs
about 10 MB of RAM; active ones add up to `work_mem` (16 MB) per sort/hash node, so budget `work_mem x concurrently
active queries` besides `shared_buffers` (2 GB of the 8 Gi limit). Past ~300 connections enable the pooler.
External Postgres: the chart cannot see `max_connections`; `NOTES.txt` prints the worst-case client count to compare.
One-off Jobs (migrate, eval/CLI) come out of `postgres.reservedConnections`.

## PgBouncer (CNPG Pooler)

`postgres.pooler.enabled=true` renders a `Pooler` (transaction mode, `max_prepared_statements=200`, NetworkPolicies).
Point `ROR_DATABASE_URL` in `secrets.existingSecret` at `<release>-pg-pooler-rw`; the migrate Job keeps the direct
`<release>-pg-rw` service. With the pooler on the chart sets `ROR_DB_DISABLE_PREPARED=true` (psycopg `prepare_threshold=None`), so the app never
uses server-side prepared statements and works with any PgBouncer; `max_prepared_statements` (needs PgBouncer >= 1.21
and a CNPG operator that passes the parameter through) is only a safety net. Transaction pooling is safe here: the only advisory locks are `pg_advisory_xact_lock`, and
`SET LOCAL` is transaction-scoped. Transaction pooling and the lease model (ADR-0003 amendment, Task 10.3): a handler session that has read something
and then waits on external I/O is `idle in transaction`. It holds no xid and no snapshot (vacuum is not blocked), but
in PgBouncer transaction mode **it still pins one server connection for the whole job**: a busy worker pins ONE server
connection for the job's duration (plus transient ones for the short stage-write/lock sessions, and the heartbeat's own
connection), exactly as before the lease model. Size `defaultPoolSize` x instances (default 40 x 2 = 80) for
`running workers x ~2 pinned-or-transient` plus API concurrency: 12 workers x 5 = 60 at full autoscale. Therefore
`idle_in_transaction_session_timeout` (PostgreSQL) and `idle_transaction_timeout` (PgBouncer) MUST stay unset on the worker
role, or exceed the longest job; they would kill healthy long handlers. The API role may use them.

## Other Postgres settings

`shared_buffers` 2GB (25% of 8Gi), `work_mem` 16MB (values.yaml). Migration `0014d` sets aggressive per-table
autovacuum on `jobs` (scale factors 0.01 / 0.005).
