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
| Gotenberg | 2 | 2 vCPU, 4 GiB | LibreOffice conversion of office uploads; one conversion at a time per pod |
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
   Elastic credentials.
5. **Helm** (`deploy/helm/roracle`, delivered in roadmap plan 11):
   ```bash
   helm upgrade --install roracle deploy/helm/roracle -n roracle -f values-prod.yaml
   ```
   The chart runs `python -m roracle migrate` as a pre-upgrade hook Job before rolling `api`/`worker`.
6. **Reverse proxy**: forward `/mcp` and `/.well-known/oauth-protected-resource` to the api (not the SPA),
   without buffering limits below `ROR_MCP_MAX_UPLOAD_BYTES` x 4/3.
7. **Smoke test**: `GET /healthz`; sign in through the proxy; upload a PDF; watch it reach
   *Searchable* in My uploads; ask a question and click a citation.

## 5. Configuration reference (environment, prefix `ROR_`)

| Variable | Example | Purpose |
|---|---|---|
| `ROR_DATABASE_URL` | `postgresql+psycopg://roracle:…@pg-rw:5432/roracle` | Postgres (read-write service) |
| `ROR_S3_ENDPOINT`, `ROR_S3_BUCKET`, `ROR_S3_ACCESS_KEY`, `ROR_S3_SECRET_KEY` | `https://minio.roracle-data:9000`, `roracle` | Blob store |
| `ROR_OIDC_ISSUER`, `ROR_OIDC_AUDIENCE`, `ROR_OIDC_JWKS_URL` | `https://kc/realms/research`, `roracle` | Token validation |
| `ROR_MAX_UPLOAD_BYTES` | `524288000` | Upload limit (500 MB) |
| `ROR_EVIDENCE_MAX_BYTES` | `1073741824` | Max evidence file size (default 1 GiB). Keep ClamAV `StreamMaxLength`/`MaxFileSize`/`MaxScanSize` >= this value (>= 1 GiB, with `AlertExceedsMax yes`) and the reverse proxy `client_max_body_size` (nginx; ingress `proxy-body-size`) >= this value, or evidence uploads fail or stay `failed`. Starlette spools multipart uploads to temporary disk before the app sees them: size `/tmp` (or mount an emptyDir) for the API pods at >= evidence limit x concurrent uploads |
| `ROR_MCP_MAX_UPLOAD_BYTES` | `26214400` | Max decoded file size for the MCP `upload` tool (25 MiB); also sizes the `/mcp` body cap |
| `ROR_PUBLIC_BASE_URL` | `https://roracle.example.org` | External origin used in OAuth metadata and handoff URLs; empty = derive from the request |
| `ROR_PATS_ENABLED` | `true` | Personal access tokens for agents (`false` rejects all PATs) |
| `ROR_PAT_MAX_IDENTITY_AGE_DAYS` | `30` | PAT rejected if its owner has not signed in via JWT within this many days |
| `ROR_ELASTIC_URL`, `ROR_ELASTIC_API_KEY` | `https://es-http:9200` | Search index (plan 3) |
| `ROR_LLM_BASE_URL`, `ROR_LLM_API_KEY` | `https://inference/v1` | OpenAI-spec endpoint (plan 2+) |
| `ROR_EMBED_MODEL`, `ROR_RERANK_MODEL` | `embeddinggemma-300m` | Defaults; admins override in UI |
| `ROR_CHAT_MODELS` | `qwen2.5:14b` | Comma-separated allowlist of chat models; the first is the default |
| `ROR_CHAT_MIN_SIMILARITY` | `0.35` | Raw cosine floor on the kNN half of chat retrieval only (BM25 hits are not floored); `0` disables |
| `ROR_CHAT_PASSAGES` | `8` | Passages sent to the model per question; clamped to 4-12 |
| `ROR_CHAT_NOTE_MIN_UPVOTES`, `ROR_CHAT_NOTE_MIN_RATIO` | `3`, `0.6` | Community notes by other users reach an LLM (chat, MCP `ask`/`search`) only with at least this many upvotes and a vote ratio above the floor (the asker's own notes always qualify); prompt-injection gate |
| `ROR_RERANK_DEPTH` | `50` | Top-N passages sent to the reranker (10-100) |
| `ROR_UPLOAD_QUOTA_BYTES` | `0` | Per-user quota: logical bytes of the user's document versions, including trash until purged and excluding rejected/cancelled versions; over = 413 `upload quota exceeded`; hard (uploads serialized per user); `0` = unlimited |
| `ROR_RATE_LIMIT_PER_MINUTE` | `0` | Per-user request budget; stored only, not enforced yet (ingress enforcement planned) |
| `ROR_CONNECTOR_FS_ROOTS` | empty | Comma or JSON list of directories filesystem connectors may read (a connector path must resolve, symlinks included, to a root or below it). Empty = filesystem connectors are rejected. Mount sources **read-only** into the worker (and the API, which validates paths at create), e.g. `volumes: [/srv/papers:/data/papers:ro]` with `ROR_CONNECTOR_FS_ROOTS=/data/papers`; give the mount nothing but the documents to ingest |
| `ROR_CONNECTOR_MAX_ITEMS` | `1000` | Items that need work (new, changed, too large; unchanged items do not count) per connector run; more = run ends `partial`, the rest is picked up next run |
| `ROR_CONNECTOR_MAX_RUN_S` | `3600` | Wall-clock cap per connector run (`partial` when hit). A run holds one database transaction (the job claim) for its whole duration: set Postgres `idle_in_transaction_session_timeout` above this value (or `0`) |
| `ROR_CONNECTOR_TICK_S` | `60` | Scheduler interval: the worker enqueues due scheduled connectors this often (a self-rescheduling `connector_tick` job, created at worker start) |
| `ROR_CONNECTOR_HTTP_ALLOW_CIDRS` | empty | Comma or JSON list of CIDRs (e.g. `10.20.0.0/16,fd12:3456::/32`) that HTTP crawl connectors may reach although they are not public addresses. Empty = only public internet hosts. Needed for air-gapped/internal crawling. Link-local (`169.254.0.0/16`, `fe80::/10`, cloud metadata), the IPv4 inside a mapped address when it is link-local/metadata, multicast and unspecified addresses are blocked even inside an allowed CIDR. IPv6 forms embedding IPv4 (mapped, NAT64, 6to4, Teredo) are never trusted by default: allowlist them explicitly (e.g. `::ffff:10.0.0.0/104`, or the NAT64 translator prefix `64:ff9b::/96` on NAT64-only networks). Invalid CIDR = startup error |
| `ROR_CONNECTOR_HTTP_TIMEOUT_S` | `60` | Per-request cap for HTTP crawl connectors, connect to last byte (also bounded by the remaining run time) |
| `ROR_DOCLING_URL`, `ROR_GROBID_URL`, `ROR_CLAMAV_HOST` | service URLs | Ingest sidecars |
| `ROR_GOTENBERG_URL`, `ROR_GOTENBERG_TIMEOUT_S` | `http://gotenberg:3000`, `300` | Office (doc/docx/odt/rtf/ppt/pptx/odp/xls/xlsx/ods) uploads are converted to PDF in the parse stage; the original is kept untouched, the rendition is stored at `derived/{version_id}/rendition.pdf`, and Docling/GROBID read the rendition (page/bbox provenance refers to it). Empty URL = office files go to Docling directly. Gotenberg down or 5xx = retryable (fails after retries are exhausted); invalid file = permanent failure |
| `ROR_NEAR_DUP_SUMMARY_THRESHOLD` | `0.97` | Cosine similarity of document summary embeddings at or above which a new upload is parked for a near-duplicate decision (in addition to the MinHash `ROR_NEAR_DUP_THRESHOLD`); only visible documents are compared |
| `ROR_MAX_RENDITION_BYTES` | `524288000` | Cap on the Gotenberg PDF rendition (streamed, aborted when exceeded); over = permanent failure. Page/size caps are enforced by Docling itself: set `DOCLING_SERVE_MAX_NUM_PAGES=2000` and `DOCLING_SERVE_MAX_FILE_SIZE=629145600` on the docling-serve container (over-limit documents fail; the error is not distinguishable, so they surface as a conversion failure). The ingest Gotenberg is a dedicated, isolated instance with chromium routes disabled (office → PDF only); the later paper-drafts HTML/Markdown → PDF build (spec §8) needs its own separate Chromium-enabled instance. Deploy Gotenberg isolated: own namespace, deny-all egress NetworkPolicy (ingress from worker only), no secrets, read-only root FS, command `gotenberg --api-disable-download-from --webhook-disable --chromium-disable-routes --pdfengines-disable-routes --api-timeout=300s` |

Runtime settings that admins change in the UI (model allowlist, thresholds, retention, profiles) live in
Postgres, not env. Admins can override `chat_models`, `chat_default_model`, `chat_passages`, `rerank_depth`,
`near_dup_threshold`, `near_dup_summary_threshold`, `max_upload_bytes`, `upload_quota_bytes`,
`rate_limit_per_minute` and `pats_enabled` at runtime via `GET`/`PUT /admin/settings` (the DB value wins over
the env value; `null` removes the override). Changes apply to new requests and jobs without a restart.

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

P2b changed chunk embedding prompts (summary context): existing vectors stay usable; optional re-embed in the operations runbook.
