# Decision matrix: data tier

Areas 1-4 and 13 of the [decision matrix](decision-matrix.md). The method, weights and status words are defined there.
**Every rating is judgement** (1 worst, 5 best; for ops cost, 5 is cheapest to run). Rating cell: `A` accuracy ×3,
`S` security ×3, `G` air-gap fit ×3, `P` performance/scale ×2, `L` licence ×2, `O` ops cost ×1. The maximum weighted
total is 70. Numbers taken from evaluation or performance runs link their report. Licence notes are general knowledge,
not something this repository checks: confirm them with your legal team.

## 1. System of record and job queue

Postgres holds all metadata and the job queue; Elasticsearch is a rebuildable index ([ADR-0001](../adr/0001-system-of-record.md)).
Jobs are rows in a `jobs` table claimed with `FOR UPDATE SKIP LOCKED`, now with leases and a per-claim fencing token
([ADR-0003](../adr/0003-postgres-job-queue.md), [jobs and queue](../architecture/jobs-queue.md)).

| Option | Ratings | Total | Decision | Rationale | ADR | Status / trigger |
|---|---|---|---|---|---|---|
| Postgres `jobs` table, `SKIP LOCKED`, leases | A5 S5 G5 P4 L5 O5 | 68 | **Chosen** | Enqueue is an INSERT in the caller's transaction, so an upload never exists without its job. No broker to run. Claim p99 3.1 ms at 100k queued and 3M done jobs against a 5 ms budget ([perf report](../perf/report-2026-11-17.md), [budgets.yaml](../perf/budgets.yaml)). | [0001](../adr/0001-system-of-record.md), [0003](../adr/0003-postgres-job-queue.md) | Decided. Revisit (RabbitMQ) only if queue metrics show Postgres is the bottleneck (ADR-0003). |
| Redis / Valkey list or stream | A2 S3 G4 P5 L4 O3 | 48 | Rejected | Not in the Postgres transaction, so enqueue becomes a dual write. No Valkey setting exists in the backend settings today: the per-token rate limit is in-process, per replica. Redis and Valkey licences differ. | [0003](../adr/0003-postgres-job-queue.md) | Decided |
| RabbitMQ | A3 S4 G4 P5 L5 O2 | 55 | Rejected | Needs an outbox to be transactional. Another stateful HA service. Ingest is about 10k documents a day (ADR-0003), far below what Postgres handles. | [0003](../adr/0003-postgres-job-queue.md) | Revisit trigger as in row 1 |
| Kafka | A3 S4 G4 P5 L5 O1 | 54 | Rejected | Log semantics fit neither per-job retries nor backoff. Heaviest to operate. | none, see ADR-0003 context | Decided |

## 2. Search engine and version

The user decided to move to Elasticsearch 9.5.6 with BBQ and rescoring, eval-gated against int8, on a fresh cluster
rebuilt from Postgres and object storage (ledger `.worktrees/rq/.superpowers/sdd/rq/progress.md:41`, user decision
2026-11-16 21:05). The shipped chart pins `elasticsearch.version: 9.5.6`, and dev and CI run the same image.

| Option | Ratings | Total | Decision | Rationale | ADR | Status / trigger |
|---|---|---|---|---|---|---|
| Elasticsearch Basic 9.5.6 (ECK), `int8_hnsw` default, BBQ selectable | A4 S4 G5 P5 L4 O4 | 61 | **Chosen** | BM25 and kNN in one engine, with the ACL filter inside the `knn` clause (ACORN). At 1M documents and 1M chunks: `/search` p95 335 ms (budget 400) and filtered p95 334 ms (budget 500) ([perf report](../perf/report-2026-11-17.md)). SciFact library-search baseline: document nDCG@10 0.770 ([baseline](../eval/scifact/baseline-search.md)). Basic has neither document-level security nor native RRF, so the API builds the ACL filter and fuses ranks (ADR-0002). | [0002](../adr/0002-elastic-basic-app-acl.md), [0015](../adr/0015-retrieval-quality.md) (outcomes) | Decided (user, 2026-11-16 21:05). Caveat: all four tfvars profiles pin ECK 2.16.0, but 9.5.6 needs an ECK 3.x release ([known limits](../enterprise/security/known-limits.md#platform-and-supply-chain)). |
| Elasticsearch Basic 8.15, `int8_hnsw` | A4 S4 G5 P4 L4 O4 | 59 | Superseded | The P1-P11 engine. No vectors-out-of-`_source`, which needs ES 9.2 or later (ADR-0015 outcomes). An existing cluster upgrades 8.15 to 8.19.x to 9.5.x ([upgrades](../enterprise/upgrades.md)). | [0002](../adr/0002-elastic-basic-app-acl.md) | Superseded by row 1 |
| OpenSearch | A4 S4 G5 P4 L5 O3 | 60 | Not adopted | Apache-2.0 licence, and the security plugin includes document-level security. The app-built filter and the R19 re-check would still be needed for labels and versions, so security rates the same. Not measured here. Scored with DLS as defence in depth (S5), it would total 63. | none | Revisit if licence policy rejects the Elastic licences, or if in-engine DLS becomes a requirement |
| Vespa | A4 S4 G4 P5 L5 O2 | 58 | Not adopted | Strong hybrid ranking. Different query and ops model, with a config server and content nodes; the team has no experience with it. | none | Decided |
| Qdrant or pgvector (with a separate BM25) | A3 S4 G5 P2 L5 O4 | 54 | Rejected | Weaker full-text side. ADR-0001 notes that at about 50M chunks, Postgres-only search would strain a single primary. | [0001](../adr/0001-system-of-record.md) | Decided |

The 1-point gap to OpenSearch is within judgement noise. Measured evidence and the cost of switching (not a criterion)
decided this row.

## 3. Vector quantisation

The backend writes `index_options` explicitly, because Elasticsearch 9.1 and later would otherwise pick `bbq_hnsw` on
its own. The default `ROR_VECTOR_INDEX_TYPE` is `int8_hnsw` (`backend/src/roracle/config.py:93-95`), and
`ROR_VECTOR_RESCORE_OVERSAMPLE` defaults to 3.0. The recall figures below are from a synthetic 50k-vector run on ES 9.5.6
(a worst-case proxy), recorded in [capacity planning](../enterprise/capacity-planning.md#choosing-the-vector-type-eval-gate).
Security is rated the same (S3) for every option, because quantisation does not change what a caller can see.

| Option | Ratings | Total | Decision | Rationale | ADR | Status / trigger |
|---|---|---|---|---|---|---|
| `int8_hnsw` | A4 S3 G5 P4 L5 O4 | 58 | **Chosen** (default) | recall@10 0.817, p95 6.1 ms, 196 MB. About 6 × 64 GiB data nodes for 50M chunks ([capacity planning](../enterprise/capacity-planning.md)). | [0006](../adr/0006-embedding-model-config.md), [0015](../adr/0015-retrieval-quality.md) (outcomes) | Decided |
| `bbq_hnsw` + float rescore | A3 S3 G5 P4 L5 O3 | 54 | Available, not default | Oversample 3 gives recall@10 0.741. Oversample 5 matches int8 (0.822), but p95 is about 2x (11.9 ms), in exchange for about 5x less vector RAM. Ledger: "bbq not default (needs oversample≈5 for int8 recall, 2x latency)" (`.worktrees/rq/.superpowers/sdd/rq/progress.md:72`). | [0015](../adr/0015-retrieval-quality.md) (outcomes) | Provisional, eval-gated. Revisit when a re-embed into a `bbq_hnsw` generation passes the gate on the own-corpus set ([eval README](../eval/README.md)) |
| `int4_hnsw` | A3 S3 G5 P4 L5 O4 | 55 | Fallback | recall@10 0.696. The store size measured was not comparable. | [0015](../adr/0015-retrieval-quality.md) (outcomes) | Fallback if BBQ fails its gate |
| Float32 HNSW (no quantisation) | A5 S3 G5 P1 L5 O3 | 54 | Not offered | Best recall, but about 4x the vector RAM of int8. The setting does not offer it. | none | Decided |

## 4. Object store

Community MinIO images ended in October 2025 (`quay.io/minio/{minio,mc}` answer 401). The user decided on Rook-Ceph RGW
as the in-cluster production default plus an external S3 option, removed the MinIO tenant, and kept the frozen
`bitnamilegacy/minio` image for dev and CI only (ledger `.worktrees/iac/.superpowers/sdd/iac/progress.md:9-11`, user
decision 2026-11-16 21:25). The chart only takes an S3 endpoint (`ROR_S3_ENDPOINT`). The Terraform default is
`object_store = "external"`, while `prod.example.tfvars` selects `rook-ceph` (ledger line 13;
`deploy/terraform/variables-ceph.tf:8`, `deploy/terraform/environments/prod.example.tfvars:37`).

| Option | Ratings | Total | Decision | Rationale | ADR | Status / trigger |
|---|---|---|---|---|---|---|
| Rook-Ceph RGW (in-cluster) | A5 S5 G5 P4 L4 O2 | 63 | **Chosen** (production in-cluster) | Full S3, including the object lock the backup and snapshot buckets use. TLS is required, there are per-bucket users, and upstream publishes images. Needs at least 3 nodes with raw disks and more RAM per OSD (ADR-0016). Live Rook-Ceph rehearsal on kind: [kind rehearsal](../enterprise/deployment-kind-rehearsal.md#3-live-rook-ceph-rehearsal-local-only). | [0016](../adr/0016-terraform-iac.md) | Decided (user, 2026-11-16 21:25) |
| External S3 (operated by the site) | A5 S4 G4 P4 L5 O5 | 62 | **Chosen** (option; Terraform default) | Costs nothing in-cluster. Security and object lock depend on the provider. Backups need separate identities (ADR-0016). | [0016](../adr/0016-terraform-iac.md) | Decided |
| MinIO tenant (operator) | A5 S2 G1 P5 L2 O3 | 41 | Ruled out | Images can no longer be pulled, so it cannot be mirrored or patched. AGPL. | [0016](../adr/0016-terraform-iac.md) | Removed from the chart and Terraform |
| `bitnamilegacy/minio` (frozen) | A5 S2 G3 P4 L3 O4 | 48 | Dev and CI only | Pullable but frozen: it gets no security fixes. Used by `.gitlab-ci.yml` and `deploy/dev/compose.yaml`. | none, see ledger (2026-11-16) | Never for production |
| SeaweedFS | A4 S3 G4 P4 L5 O3 | 54 | Not adopted | Apache-2.0. Object lock support and the operator path were not evaluated. | none, see ledger (2026-11-16) | Revisit if Ceph is too heavy for a site without an external S3 |
| Garage | A3 S3 G4 P3 L3 O4 | 46 | Not adopted | Lightweight, but implements a subset of S3; object lock was not evaluated. AGPL. | none, see ledger (2026-11-16) | Decided |

Rook-Ceph ranks only 1 point above external S3: the choice between them is per site, by node count and whether an S3
service already exists.

## 13. Ingestion stack, dedup and versioning

Stages run as jobs: ClamAV, exact dedup, Gotenberg, Docling and GROBID, summary, near-dup check, chunking, embedding and
indexing ([ADR-0005](../adr/0005-ingestion-pipeline.md), [ingest data flow](../architecture/ingest.md)). The parsers
are off-the-shelf images under the chart's `parsers` key.

| Option | Ratings | Total | Decision | Rationale | ADR | Status / trigger |
|---|---|---|---|---|---|---|
| Docling + GROBID + Gotenberg + ClamAV | A5 S4 G5 P3 L4 O2 | 58 | **Chosen** | Layout, OCR, tables, and page and bounding box for citations; GROBID adds metadata and references. Parsers are sandboxed, but they run without egress only where NetworkPolicy is enforced ([known limits](../enterprise/security/known-limits.md#platform-and-supply-chain)). Our own overhead is 0.16 s p95 per document; parse throughput was not measured ([perf report](../perf/report-2026-11-17.md)). Four services to run. | [0005](../adr/0005-ingestion-pipeline.md) | Decided |
| Apache Tika + Tesseract | A2 S4 G5 P4 L5 O4 | 55 | Not adopted | Flat text without layout or bounding boxes, so page-exact citations and table handling suffer. | none | Decided |
| Unstructured | A4 S4 G4 P3 L5 O3 | 55 | Not adopted | Similar coverage. Its model downloads must be mirrored, and it does not replace GROBID's reference extraction. | none | Decided |

Dedup and versioning ([ADR-0008](../adr/0008-dedup-versioning.md)):

| Option | Ratings | Total | Decision | Rationale | ADR | Status / trigger |
|---|---|---|---|---|---|---|
| SHA-256 blob dedup that never discloses an invisible match; near-dup prompt; versions; always PUT | A4 S5 G5 P4 L5 O4 | 64 | **Chosen** | A 409 `duplicate` only for a match the uploader can see. Always PUT, so timing does not reveal a match (ADR-0008 addendum). Near-dup p95 5.1 ms over 1M versions ([perf report](../perf/report-2026-11-17.md)). | [0008](../adr/0008-dedup-versioning.md) | Decided |
| Global dedup that reports any match | A5 S1 G5 P5 L5 O5 | 58 | Rejected | It would be an existence oracle for documents the caller cannot see. | [0008](../adr/0008-dedup-versioning.md) | Decided |
| No dedup | A2 S5 G5 P3 L5 O5 | 57 | Rejected | Duplicate storage and duplicate search hits. | none | Decided |
