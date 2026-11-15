# rOracle Enterprise Deployment Guide

Audience: platform engineers deploying rOracle on an air-gapped Kubernetes cluster.
Read with: [spec](../spec.md), [security](security.md), [operations runbook](operations-runbook.md),
[Keycloak setup](keycloak-setup.md), [mirror manifest](airgap-mirror-manifest.md).

## 1. Topology

```
 users ──mTLS──► org proxy ──► ingress (TLS) ──► web (nginx, SPA)
 agents ─mTLS──► org proxy ─┘                 └► api (FastAPI: REST, SSE, /mcp) ──┬─► Postgres (CNPG, 3 inst.)
                                                                                  ├─► Elasticsearch (ECK, 3+ data)
 Keycloak (X.509 authenticator) ◄── JWKS ── api                                   ├─► MinIO (4+ nodes, EC)
                                                                                  └─► OpenAI-spec inference
 worker (jobs) ──► Docling · GROBID · Gotenberg · ClamAV · latex-builder ──► same stores
```

Namespaces: `roracle` (app), `roracle-data` (Postgres, Elastic, MinIO) — or your platform's existing
operators/instances. NetworkPolicies: default deny; allow only the edges above.

## 2. Prerequisites

| Item | Requirement |
|---|---|
| Kubernetes | ≥ 1.29, 3+ worker nodes across failure domains; CSI with SSD storage class and snapshots |
| Operators | ECK (Elastic Cloud on Kubernetes), CloudNativePG, MinIO Operator (or external S3) |
| Ingress | TLS-terminating ingress reachable only from the org proxy |
| Keycloak | Realm with X.509 browser + direct-grant flows, groups, client `roracle-web` (see Keycloak guide) |
| Inference | OpenAI-spec endpoint: chat models, one embedding model, reranker (`/v1/rerank` or equivalent) |
| Registry | All images in the [mirror manifest](airgap-mirror-manifest.md) pushed to the internal registry |
| DNS / certs | `roracle.<domain>`; internal CA trusted by pods |
| Observability | Prometheus/Grafana/Loki/OTel collector (existing or bundled sub-chart) |

## 3. Sizing (target: 500 concurrent users, 1M docs ≈ 50M chunks, 10k docs/day)

| Component | Replicas | Per replica | Notes |
|---|---|---|---|
| api | 3 (HPA 3–10) | 2 vCPU, 4 GiB | CPU-bound on fusion/streaming; scale on p95 latency |
| worker | 4 (HPA on queue depth) | 4 vCPU, 8 GiB | Parsing is delegated; worker is mostly I/O |
| web | 2 | 0.25 vCPU, 128 MiB | static |
| Docling | 2–4 | 4 vCPU, 16 GiB (or GPU) | ~200k pages/day ≈ 2.3 pages/s sustained; GPU or the inference endpoint's granite-docling model recommended |
| GROBID | 2 | 4 vCPU, 8 GiB | |
| Gotenberg | 2 | 2 vCPU, 4 GiB | |
| ClamAV | 2 | 2 vCPU, 4 GiB | signature mirror required; set `StreamMaxLength`, `MaxFileSize`, `MaxScanSize` to 600M (> the 500 MB upload limit) and `AlertExceedsMax yes` (fail closed: oversize files are reported FOUND, never partially scanned), else large uploads fail permanently |
| latex-builder | 2 | 2 vCPU, 2 GiB per build | job concurrency limited |
| Postgres | 3 (1 primary + 2 sync/async replicas) | 8 vCPU, 32 GiB, 500 GiB SSD | metadata ≈ 100 GiB at target |
| Elasticsearch | 3 master (small) + 3–4 data | data: 16 vCPU, 64 GiB (31 GiB heap), 2 TiB SSD | see vector math below |
| MinIO | 4–8 nodes, erasure coding | 4 vCPU, 16 GiB, disks for ~10 TiB usable | originals ≈ 5 TiB + derived PDFs + evidence |

Vector math (per embedding model index, 1 replica):
- 768-d (EmbeddingGemma): 50M × 768 × 4 B ≈ 154 GB float; `int8_hnsw` ≈ 39 GB + graph ≈ 7 GB.
- 4096-d (e5-mistral-7b): ≈ 820 GB float; `int8_hnsw` ≈ 205 GB → needs ~2× the data nodes above.
- Keep quantized vectors in page cache (off-heap RAM) for p95 < 500 ms; double during re-embed.

## 4. Install

1. **Mirror** every artifact in the manifest; set `global.imageRegistry` in values.
2. **Data services** (skip any you provide externally):
   - Postgres: CNPG `Cluster` with 3 instances, WAL archiving to object storage (PITR), `pg_stat_statements`.
   - Elastic: ECK `Elasticsearch` with dedicated masters, data tier, snapshot repository (S3/MinIO).
   - MinIO: tenant with erasure coding, versioning on the `roracle` bucket, site replication for DR.
3. **Keycloak**: apply the realm settings in [keycloak-setup.md](keycloak-setup.md).
4. **Secrets** (from your secret manager / sealed secrets): DB credentials, S3 keys, inference API key,
   `ROR_PAT_PEPPER` (32 random bytes), Elastic credentials.
5. **Helm** (`deploy/helm/roracle`, delivered in roadmap plan 11):
   ```bash
   helm upgrade --install roracle deploy/helm/roracle -n roracle -f values-prod.yaml
   ```
   The chart runs `python -m roracle migrate` as a pre-upgrade hook Job before rolling `api`/`worker`.
6. **Smoke test**: `GET /healthz`; sign in through the proxy; upload a PDF; watch it reach
   *Searchable* in My uploads; ask a question and click a citation.

## 5. Configuration reference (environment, prefix `ROR_`)

| Variable | Example | Purpose |
|---|---|---|
| `ROR_DATABASE_URL` | `postgresql+psycopg://roracle:…@pg-rw:5432/roracle` | Postgres (read-write service) |
| `ROR_S3_ENDPOINT`, `ROR_S3_BUCKET`, `ROR_S3_ACCESS_KEY`, `ROR_S3_SECRET_KEY` | `https://minio.roracle-data:9000`, `roracle` | Blob store |
| `ROR_OIDC_ISSUER`, `ROR_OIDC_AUDIENCE`, `ROR_OIDC_JWKS_URL` | `https://kc/realms/research`, `roracle` | Token validation |
| `ROR_MAX_UPLOAD_BYTES` | `524288000` | Upload limit (500 MB) |
| `ROR_ELASTIC_URL`, `ROR_ELASTIC_API_KEY` | `https://es-http:9200` | Search index (plan 3) |
| `ROR_LLM_BASE_URL`, `ROR_LLM_API_KEY` | `https://inference/v1` | OpenAI-spec endpoint (plan 2+) |
| `ROR_EMBED_MODEL`, `ROR_RERANK_MODEL` | `embeddinggemma-300m` | Defaults; admins override in UI |
| `ROR_CHAT_MODELS` | `qwen2.5:14b` | Comma-separated allowlist of chat models; the first is the default |
| `ROR_CHAT_MIN_SIMILARITY` | `0.35` | Raw cosine floor on the kNN half of chat retrieval only (BM25 hits are not floored); `0` disables |
| `ROR_CHAT_PASSAGES` | `8` | Passages sent to the model per question; clamped to 4-12 |
| `ROR_DOCLING_URL`, `ROR_GROBID_URL`, `ROR_GOTENBERG_URL`, `ROR_CLAMAV_HOST` | service URLs | Ingest sidecars |

Runtime settings that admins change in the UI (model allowlist, thresholds, retention, profiles) live in
Postgres, not env.

## 6. High availability

- Stateless tiers (`web`, `api`, `worker`, sidecars): ≥ 2 replicas, PodDisruptionBudget `minAvailable: 1`,
  topology spread across zones, readiness on `/healthz`.
- Postgres: CNPG automatic failover; app connects via the `-rw` service.
- Elastic: 3 dedicated masters; ≥ 1 replica shard; shard size target 30–50 GB.
- MinIO: erasure coding tolerates node/disk loss; site replication for DR.
- Worker jobs are idempotent and retried (max 5, exponential backoff); a worker crash releases its
  row lock and the job is retried.
- Rolling upgrades: migrations are additive (expand → migrate → contract across releases).

## 7. Upgrade policy

SemVer: patch = drop-in; minor = additive migrations, rolling upgrade; major = release notes with
manual steps. Always snapshot Postgres and Elastic before a minor/major upgrade.
