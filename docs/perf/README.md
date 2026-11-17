# Performance harness

Opt-in tooling (Task 10). It only touches a **scratch database** (name must contain `perf`) and `perf-*`
Elasticsearch indices; never the real DB, the test DBs or the `chunks` aliases.

```bash
export ROR_DATABASE_URL=postgresql+psycopg://roracle:roracle@localhost:55433/roracle_perf
createdb -h localhost -p 55433 -U roracle roracle_perf
uv run roracle migrate
# Postgres: ~1M docs in ~3 min (pure generate_series SQL, then VACUUM ANALYZE)
uv run roracle perf seed-pg --url "$ROR_DATABASE_URL" --docs 1000000 --users 1000 \
  --jobs-done 3000000 --jobs-queued 100000 --audit-rows 1000000
# Elasticsearch: isolated index + aliases, then point the API at them
uv run roracle perf seed-es --index perf-chunks-1 --chunks 1000000 --docs 1000000 \
  --alias perf-chunks --alias perf-chunks-all
export ROR_READ_ALIAS=perf-chunks ROR_MUTATE_ALIAS=perf-chunks-all   # defaults: chunks / chunks-all
# Load (open loop; latency counted from the scheduled arrival). Exit code 1 when over budget.
uv run roracle perf load search --token "$TOKEN" --rps 20 --duration 60 --budget docs/perf/budgets.yaml
# Queue claim latency at the seeded queue depth (budget: p50 <= 1 ms, p99 <= 5 ms)
uv run roracle perf explain claim --url "$ROR_DATABASE_URL"
```

Scenarios: `search`, `search_filtered`, `browse`, `chat_retrieve` (the retrieval half of a chat turn,
approximated by a wide `/search`), `mcp_search`, `doc_read`. Budgets live in `budgets.yaml`.
Documents/chunks share deterministic ids (`md5('<seed>:d:<i>')::uuid`) across Postgres and ES.
Plan-shape tests (`tests/test_perf_plans.py`) run in the integration suite; timing gates run nightly only.
Upgrading (migration 0014 builds queue indexes with `CREATE INDEX CONCURRENTLY`, which waits for every open
transaction, and a claim shares one transaction with its handler): drain workers or let long jobs finish
before `roracle migrate`. A failed build leaves an invalid index; re-running the migration drops and rebuilds it.
`ROR_READ_ALIAS` and `ROR_MUTATE_ALIAS` must be set together (Settings validates it) on API and worker.

Drop the scratch database when done: `dropdb -h localhost -p 55433 -U roracle roracle_perf`.
