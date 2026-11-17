# Capacity planning

Audience: platform engineers and SRE. Back to the [enterprise index](README.md). Component sizing for the reference target (500 concurrent users, 1M documents, about 50M chunks, 10k documents a day) is in the [deployment guide](deployment-guide.md#3-sizing-target-500-concurrent-users-1m-docs-50m-chunks-10k-docsday); this page explains the numbers behind it: Elasticsearch 9.5.6 (with BBQ), Postgres, the object store (Rook-Ceph RGW), inference and the connection budget. Measured behaviour: [performance report](../perf/report-2026-11-17.md) and the [budgets](../perf/README.md#budgets-at-a-glance) in `docs/perf/budgets.yaml`.

## Elasticsearch (9.5.6)

## What the index looks like

One index per embedding model and generation (`chunks-<model>-<n>`), read alias `chunks`, mutation alias `chunks-all`.
New indices are created with explicit settings (they cannot be changed afterwards except by re-embed / rebuild into a
new generation): `ROR_ES_SHARDS`, `ROR_ES_REPLICAS`, `ROR_ES_REFRESH_INTERVAL` (5s), `ROR_VECTOR_INDEX_TYPE`
(`int8_hnsw` default | `int4_hnsw` | `bbq_hnsw`, with `ROR_VECTOR_RESCORE_OVERSAMPLE` = 3.0 for bbq), ACORN filtered kNN
(`ROR_ES_HNSW_FILTER_HEURISTIC=acorn`: the ACL/facet filter sits INSIDE the `knn` clause, so the graph walk only visits
matching nodes) and `index.mapping.exclude_source_vectors` (vectors are not stored in `_source`; ES rehydrates them for
reindex and ACL `update_by_query`, and `fields`/`_source.includes` still return them).
The kNN `index_options` are always explicit: ES 9.1+ silently defaults to `bbq_hnsw` for >= 384 dims otherwise.

Library search adds bibliographic fields to every chunk and the abstract to each document's head chunk: < 2 KB per document.

## RAM and disk (768 dims, HNSW m=16): ESTIMATES from formulas, not measured

Per copy of the index; with one replica double the numbers. Page cache must hold the quantized vectors plus the graph;
`bbq_hnsw` additionally reads float vectors from disk to rescore `oversample x k` candidates.
| chunks | int8_hnsw: vectors + graph | bbq_hnsw: vectors + graph | float32 on disk | disk with vectors in `_source` (estimate ~9 KB/chunk) |
|---|---|---|---|---|
| 1M | ~0.8 GB | ~0.17 GB | 3.1 GB | ~9 GB |
| 10M | ~8.4 GB | ~1.7 GB | 31 GB | ~90 GB |
| 50M | ~42 GB | ~8.7 GB | 154 GB | ~450 GB (estimate; `exclude_source_vectors` removes the duplicate float copy, roughly a third to 40%: measure) |
| 1B | ~836 GB | ~174 GB | 3.1 TB | ~9 TB |
Formulas: int8 `n x (dims + 4)` + graph `n x 4 x m`; bbq `n x (dims / 8 + 14)` + graph.

Sizing rules: one shard per 30-50 GB (`ROR_ES_SHARDS`: 12 for 50M chunks); page cache >= 1.2 x (vectors + graph) on every
data node; for 50M chunks with int8 about 6 x 64 GiB data nodes (8-16 GiB heap, rest page cache); enable mmap
(`vm.max_map_count >= 262144` through a node-tuning DaemonSet) and drop `node.store.allow_mmap: false`. With
`bbq_hnsw` the same corpus needs about a fifth of the vector RAM.

## Choosing the vector type (eval gate)

The default stays `int8_hnsw`. A measurement on ES 9.5.6 (50k synthetic clustered vectors, 768 dims, 100 queries,
production kNN `k=100, num_candidates=400`, recall against exact cosine, single segment):
| type | recall@10 | recall@100 | p50 / p95 ms | store |
|---|---|---|---|---|
| int8_hnsw | 0.817 | 0.720 | 4.6 / 6.1 | 196 MB |
| int4_hnsw | 0.696 | 0.682 | 3.7 / 4.8 | 351 MB (more than int8: the store includes the raw float copy plus both quantized copies of the segment, not forcemerged to a steady state; not comparable, treat as unreliable) |
| bbq_hnsw oversample 1.5 | 0.667 | 0.410 | 6.6 / 10.1 | 162 MB |
| bbq_hnsw oversample 3 | 0.741 | 0.545 | 6.6 / 10.7 | 162 MB |
| bbq_hnsw oversample 5 | 0.822 | 0.645 | 6.4 / 11.9 | 162 MB |
Synthetic data is a worst-case proxy, so absolute recall is low; the point is the ordering: bbq needs oversample ~5 to
match int8 here, and costs ~2x latency, in exchange for ~5x less vector RAM. Therefore bbq is NOT the default. To adopt it:
set `ROR_VECTOR_INDEX_TYPE=bbq_hnsw` (and the oversample), re-embed into a new generation (`roracle` admin re-embed),
run `roracle eval run` / `compare` against the reference set on that generation, and promote only if the gate passes
(no significant nDCG@10 / Recall@10 loss); otherwise fall back to `int4_hnsw` or keep int8 and add nodes.

## Write path (H4)

Ingest bulks with no refresh and issues ONE `_refresh` before a version becomes searchable (a first index of a version
skips the stale-chunk delete, a version-1 document skips the is_latest demotion). The index `refresh_interval` stays 5s
for everyone else. ACL changes keep their forced refresh (a revocation must be visible at once); the daily reconcile
writes nothing when there is no drift (10.4). `ensure_index` metadata calls are memoized for 30 s per process.

## Upgrade notes (8.x -> 9.x)

Supported path for an existing cluster (ECK or not): 8.15 -> **8.19.x** (rolling, run the Upgrade Assistant) -> 9.5.x.
A direct 8.15 -> 9 jump is refused (ECK's webhook / the node itself); bump `elasticsearch.version` to 8.19.x first, let ECK
finish, then to 9.5.6. ECK must be a release whose support matrix lists 9.5 (3.x line). After the hop 9.x reads the
indices created on 8.x unchanged; the new index settings (`exclude_source_vectors`, shard count, `index_options`)
only apply to indices created afterwards (re-embed or rebuild into a new generation); `hnsw_filter_heuristic` is
dynamic and is applied to the active index at the next `ensure_index`. A fresh 9.5.6 cluster + rebuild is the
alternative (the dev compose uses a new `esdata9` volume because an 8.x data directory cannot be opened by 9 directly).
External Elasticsearch must be 9.x: the client sends `compatible-with=9` and these settings are 9.x-only.
The Python client is `elasticsearch` 9.x. The 9.0 breaking changes that touch this code: vectors absent from `_source`
(fixed in `cached_vectors` and the tests) and stricter bulk parsing. `bbq_disk`, native RRF/linear retrievers and
searchable snapshots need an Enterprise license and are not used.

## Postgres

Chart defaults (`postgres.*`, [Helm values](../reference/helm-values.md)): 3 instances (1 primary), 100Gi per instance, requests 2 CPU and 8Gi memory with a 8Gi limit, `max_connections` 200, `shared_buffers` 2GB, `work_mem` 16MB and `jit` off. The deployment guide sizes the target at 8 vCPU, 32 GiB and 500 GiB; metadata is about 100 GiB at the target.

- `jit = off` (the chart sets it; set it yourself on an external Postgres). With JIT on, the ACL queries are compiled on every statement (150-500 ms each under load) and `/search` is about 4x slower (perf report, F1).
- `shared_buffers` about 25% of memory: at the default 128 MB a search spends about 130 ms in Postgres, at 3 GB about 13 ms (F5).
- The 1M-document measurement used a 5.8 GB database (1M documents and versions, 2M tags, 5M citations, 3M finished jobs, 1M audit rows, 16M LSH rows). Plan disk for the audit log and `jobs`, which the maintenance job prunes (finished jobs after 7 days, failed after 30).
- Backups add WAL and base-backup volume to the S3 side, not to the cluster disk ([backup and DR](backup-dr.md)).

### Connection budget

Every pod holds pooled connections, so the budget must hold at full autoscale. The chart renders a failure with the numbers when it does not (and `NOTES.txt` prints the worst case for an external Postgres).

```text
clients = api_max_replicas x (ROR_DB_POOL_SIZE + ROR_DB_MAX_OVERFLOW)
        + worker_max_replicas x (ROR_WORKER_DB_POOL_SIZE + ROR_WORKER_DB_MAX_OVERFLOW)
direct:  clients + postgres.reservedConnections <= postgres.parameters.max_connections
pooled:  pooler.instances x defaultPoolSize + reserved <= max_connections;  clients <= pooler.instances x maxClientConn
```

Defaults: API 10 x (8 + 4) = 120, workers 12 x (3 + 1 + 1 heartbeat) = 60, reserved 15, total 195 against `max_connections` 200. Each idle backend costs about 10 MB of RAM and active ones add up to `work_mem` per sort or hash node. Past about 300 connections enable PgBouncer (`postgres.pooler.enabled`). One-off Jobs (migrate, eval and CLI runs) come out of `postgres.reservedConnections`. Derivation, the agentic extra connections and the PgBouncer rules: [connection budget](../perf/connection-budget.md).

## Application replicas

- **API:** one process sustains about 10 searches per second (5 filtered searches per second) before it is CPU-bound; size replicas as `rps / 10` for search and `rps / 5` for filtered (perf report, F3). Each in-flight chat stream holds one worker thread (`ROR_API_THREADS`, 256).
- **Workers:** parsing and models are remote, so workers wait on I/O. Ingest overhead in our own code is about 0.16 s per document (p95), against 15 s per document allowed by the throughput budget of 4 documents a minute per worker; real throughput is set by Docling, the summary model and embeddings. Scale on queue depth, not CPU ([incident runbook](runbook-incidents.md#2-job-leases-drain-and-worker-shutdown)).
- **Budgets** (`docs/perf/budgets.yaml`, milliseconds unless stated): search p95 400, filtered search p95 500, browse p95 100 at 10M documents, chat retrieval p95 800 (1100 with RAPTOR), MCP search p95 450, document read p95 80, queue claim p99 5, ingest overhead p95 2 s, near-duplicate p95 50, re-embed at least 1000 chunks/s, reconcile with no drift 0 Elasticsearch writes.

## Object store (Rook-Ceph RGW)

Only for `object_store = "rook-ceph"` ([Terraform guide](deployment-terraform.md)); with an external S3 the provider sizes it. Terraform defaults (`ceph.*`): 3 monitors, 2 managers, an erasure-coded data pool 2 data + 1 coding chunks (needs at least 3 failure domains, `failure_domain = "host"`; `replicated` with 3 replicas otherwise), replicated metadata (3), 2 RGW instances (request 1 CPU and 2Gi, limit 4Gi), OSDs request 1 CPU and 4Gi (limit 6Gi). Raw capacity is usable capacity times 1.5 for erasure 2+1, or times 3 replicated. The deployment guide's target is about 10 TiB usable (originals about 5 TiB plus derived PDFs and evidence).

Add the backup buckets: the object-locked buckets keep noncurrent versions for `ceph.object_lock.retention_days` plus one day, so they cost about twice the retention window of backup data ([backup and DR](backup-dr.md#3-object-lock-backup-buckets)). Backups on the same cluster as the data they protect are not a backup: replicate off-site.

## Inference

rOracle calls an OpenAI-spec endpoint (`ROR_LLM_BASE_URL`) for chat, summaries and embeddings, and optionally a reranker; it does not run models itself. Sizing inputs:

- **Chat:** a model with at least 32k context (default `qwen2.5:14b`). Peak concurrent chats equal API threads in use; the retrieval half of a chat turn is budgeted at 800 ms excluding the LLM.
- **Embeddings:** every chunk is embedded at ingest, and a re-embed copies all chunks: the 1000 chunks/s budget assumes four embedding replicas at about 250 chunks/s each. Set `ROR_EMBED_CONCURRENCY` (requests in flight per slice) and `ROR_REEMBED_SLICES` (default 4) to the replica count. Double the vector storage during a re-embed.
- **Small models:** `ROR_SUMMARY_MODEL` (default `qwen2.5:3b`) runs once per document; the agentic planner needs a small fast model (calls above `ROR_AGENTIC_CALL_TIMEOUT_S`, 1.5 s, drop the loop).
- **Reranker:** `bge-reranker-v2-m3`, up to 200 passages per chat request (`ROR_RERANK_DEPTH`) and 50 for `/search` (`ROR_SEARCH_RERANK_DEPTH`), at most 256 texts per call, timeout 3 s (`ROR_RERANK_TIMEOUT_S`); target p95 under 800 ms for 200 passages. A slower reranker adds its time to every search one for one.
- **In-cluster option:** the Terraform `inference` module (off by default) runs one GPU each for the LLM (defaults 4 CPU, 32Gi) and the reranker (2 CPU, 8Gi), with placeholder images ([mirror manifest](airgap-mirror-manifest.md)).

## Related

[Observability](observability.md), [upgrades](upgrades.md), [metrics reference](../reference/metrics.md), [settings reference](../reference/settings.md).
