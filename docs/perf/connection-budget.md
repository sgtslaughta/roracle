# Connection and thread budget (Task 10.9, H3)

## Rules the code follows
- **No pooled connection is held across slow external calls.** Request sessions (the API's session factory sets
  `release_before_io`) `commit()` right before the query embedding, the ES `msearch`, the reranker and the LLM
  embedding in `/search`, chat retrieval and the global-answer candidates. A committed session returns its connection
  to the pool and checks one out again on its next statement. Worker/eval sessions never do this (they run inside the
  job transaction, ADR-0003). The chat SSE stream already commits before streaming and uses its own session.
- **Explicit pools** (`ROR_DB_POOL_SIZE` / `ROR_DB_MAX_OVERFLOW` for the API, `ROR_WORKER_DB_POOL_SIZE` /
  `ROR_WORKER_DB_MAX_OVERFLOW` for workers, `ROR_DB_POOL_TIMEOUT_S`). Defaults 8+4 and 3+1.
- **Threads:** every sync route and every streamed answer holds one anyio worker thread for its whole run (a blocking
  LLM stream cannot yield it). anyio's default is 40 tokens, so ~40 concurrent chats would stall `/search`, MCP and the
  readiness probes. The lifespan raises the limiter to `ROR_API_THREADS` (256: 200 concurrent chats + headroom for search/MCP). `/healthz` is `async` and needs no thread.
  Threads are cheap (stack only); the DB pool, not the thread count, bounds database load: a request waiting for a
  connection fails after `ROR_DB_POOL_TIMEOUT_S`.

## Formula
```
clients     = api_max_replicas x (ROR_DB_POOL_SIZE + ROR_DB_MAX_OVERFLOW)
            + worker_max_replicas x (ROR_WORKER_DB_POOL_SIZE + ROR_WORKER_DB_MAX_OVERFLOW)
direct      : clients + postgres.reservedConnections            <= postgres.parameters.max_connections
pooled      : pooler.instances x defaultPoolSize + reserved     <= max_connections
              clients                                            <= pooler.instances x maxClientConn
```
Chart defaults (`values.yaml`): API 10 x 12 = 120, workers 12 x 4 = 48, reserved 15 -> 183 <= `max_connections` 200.
`helm template` fails with the numbers when the budget is exceeded (so CI catches a values change). Each backend costs
about 10 MB of RAM, so raising `max_connections` needs `shared_buffers`/memory headroom; past ~300 enable the pooler.

## PgBouncer (CNPG Pooler)
`postgres.pooler.enabled=true` renders a `Pooler` (transaction mode, `max_prepared_statements=200`, NetworkPolicies).
Point `ROR_DATABASE_URL` in `secrets.existingSecret` at `<release>-pg-pooler-rw`; the migrate Job keeps the direct
`<release>-pg-rw` service. Transaction pooling is safe here: the only advisory locks are `pg_advisory_xact_lock`, and
`SET LOCAL` is transaction-scoped. Workers hold a connection for a whole job transaction (until Task 10.3 lands the
lease model), so size `defaultPoolSize` >= concurrently running jobs + API concurrency.

## Other Postgres settings
`shared_buffers` 2GB (25% of 8Gi), `work_mem` 16MB (values.yaml). Migration `0014d` sets aggressive per-table
autovacuum on `jobs` (scale factors 0.01 / 0.005).
