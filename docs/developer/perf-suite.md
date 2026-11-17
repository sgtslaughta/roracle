# Perf suite

`roracle perf` generates synthetic data and load against a scratch database and scratch Elasticsearch indices, and gates the result on the budgets in `docs/perf/budgets.yaml`. It is opt-in tooling in `backend/src/roracle/perf/`. Methodology, scenarios and the measured full-scale numbers are in the [perf harness README](../perf/README.md) and the [perf report](../perf/report-2026-11-17.md); how budgets translate into replicas is in [capacity planning](../enterprise/capacity-planning.md). Flags are in the [CLI reference](../reference/cli.md).

## Scratch-only guards

The tooling must never touch real data. Two guards, both in code:

- **Database name.** `seed-pg`, `seed-lsh`, `explain`, `reconcile` and `suite` call `_require_scratch` (`perf/cli.py`), which exits unless the database name fully matches `roracle_perf[a-z0-9_]*` (for example `roracle_perf` or `roracle_perf_1m`). `roracle_test`, `roracle` and any other name are refused before any write.
- **Index name.** `seed-es` and the rebuild benchmark refuse any index whose name does not start with `perf-` (`perf/synth_es.py`, `perf/rebuild_bench.py`). The suite only uses `perf-chunks-1`, the aliases `perf-chunks` (read) and `perf-chunks-all` (mutate), and `perf-reembed-bench`. The real aliases `chunks` and `chunks-all` are never written.

Test tokens exist only in `perf/tokens.py`: a throwaway RSA key, a JWKS served from a local server, and RS256 tokens for synthetic users. The API under test is pointed at that JWKS. Nothing in the application can mint a token, so do not add token minting to application code.

## Commands

| Command | Does |
|---|---|
| `roracle perf seed-pg --url U [--docs N --users N --jobs-done N --jobs-queued N --audit-rows N --lsh N --seed N]` | seed a migrated scratch database with documents, users, jobs and audit rows (deterministic ids from `--seed`) |
| `roracle perf seed-lsh --url U --n N` | MinHash and LSH bands for the first N seeded versions |
| `roracle perf seed-es [--index perf-chunks-1 --chunks N --docs N --alias A ...]` | seed a `perf-*` index with chunks aligned to the Postgres corpus; `--alias` is repeatable |
| `roracle perf load SCENARIO --token T [--rps N --duration S --budget FILE]` | open-loop load against a running API; exit 1 when over budget |
| `roracle perf explain claim --url U` | job-claim latency at the seeded queue depth, `--workers` concurrent claimers |
| `roracle perf reconcile --url U` | drift-only ACL reconcile over the scratch corpus; set `ROR_MUTATE_ALIAS` first |
| `roracle perf suite --profile ci\|full --url U [--es-url E --skip-seed --only S1,S2 --out FILE]` | every budget in one run; non-zero exit on a miss |

Scenarios for `load` and the suite are `search`, `search_filtered`, `browse`, `chat_retrieve`, `mcp_search` and `doc_read`. Point the API at scratch aliases with `ROR_READ_ALIAS=perf-chunks ROR_MUTATE_ALIAS=perf-chunks-all`; the two must be set together.

A local run:

```sh
createdb -h localhost -p 55433 -U roracle roracle_perf
export ROR_DATABASE_URL=postgresql+psycopg://roracle:roracle@localhost:55433/roracle_perf
cd backend
uv run python -m roracle migrate
uv run python -m roracle perf suite --profile ci --url "$ROR_DATABASE_URL" --es-url http://localhost:9200 --out /tmp/perf-results.json
```

`suite` starts its own API process on a free port with fake embedding and rerank servers (fixed latency, `--embed-ms` 10 and `--rerank-ms` 50 by default), mints tokens, and runs each scenario. It sets `jit = off` on the scratch database, as the Helm chart does for Postgres.

## Profiles

| Profile | Corpus | Budget factor | Use |
|---|---|---|---|
| `ci` | 20,000 documents and chunks, 100 users, 10,000 queued and 100,000 done jobs | 1.5 (shared runners) | CI job `perf` |
| `full` | 1,000,000 documents and chunks, 1,000 users, 100,000 queued and 3,000,000 done jobs | 1.0 | report numbers; seed once, then re-run with `--skip-seed` |

## Budgets

`docs/perf/budgets.yaml` is the binding set, in milliseconds unless the key says otherwise: `search` p95 400, `search_filtered` p95 500, `browse` p95 100 (at 10M documents), `chat_retrieve` p95 800, `mcp_search` p95 450, `doc_read` p95 80, `claim` p99 5 (at 100k queued jobs, one claimer), `ingest_overhead_s` p95 2, `near_dup` p95 50, `rebuild_chunks_per_s` at least 1000 and `reconcile_es_writes` at most 0. If you change a budget, update the table in the perf README too.

## The CI job `perf`

Defined in `.gitlab-ci.yml`: stage `test`, Postgres with `max_connections=200` and an `elasticsearch:9.5.6` service (1 GB heap), database `roracle_perf`, timeout 40 minutes, one retry on a script failure (shared runners are noisy). It runs `python -m roracle migrate --wait 60` then `python -m roracle perf suite --profile ci --budget ../docs/perf/budgets.yaml --out perf-results.json`, and keeps `perf-results.json` as an artifact. It runs on pipeline schedules (nightly) and from the web UI. On a merge request it is manual and may fail.
