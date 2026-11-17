# Elasticsearch capacity planning (Elasticsearch 9.5.6)

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

## Postgres settings that the performance budgets assume

- `jit = off` (the chart sets it; set it yourself on an external Postgres). With JIT on, the ACL queries are compiled on every
  statement (150-500 ms each under load) and `/search` is about 4x slower (`docs/perf/report-2026-11-17.md`, F1).
- `shared_buffers` ~ 25 % of memory: at the default 128 MB a search spends ~130 ms in Postgres, at 3 GB ~13 ms (F5).
- API replicas: one process sustains about 10 searches/s (5 filtered searches/s) before it is CPU-bound (F3).
