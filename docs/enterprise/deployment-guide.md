# rOracle Enterprise Deployment Guide

Audience: operators and SRE deploying rOracle (Helm chart 0.2.0, `deploy/helm/roracle`) on an air-gapped Kubernetes cluster.
Read with: [Terraform deployment](deployment-terraform.md), [kind rehearsal](deployment-kind-rehearsal.md), [upgrades](upgrades.md), [Helm values reference](../reference/helm-values.md), [security](security.md), [operations runbook](operations-runbook.md),
[Keycloak setup](keycloak-setup.md), [mirror manifest](airgap-mirror-manifest.md). Terms are in the [glossary](../glossary.md).

## 1. Topology

```text
 users ──mTLS──► org proxy ──► ingress (TLS) ──► web (nginx, SPA)
 agents ─mTLS──► org proxy ─┘                 └► api (FastAPI: REST, SSE, /mcp) ──┬─► Postgres (CNPG, 3 inst.)
                                                                                  ├─► Elasticsearch (ECK, 3+ data)
 Keycloak (X.509 authenticator) ◄── JWKS ── api                                   ├─► S3 (Rook-Ceph RGW or external)
                                                                                  └─► OpenAI-spec inference
 worker (jobs) ──► Docling · GROBID · Gotenberg · ClamAV · latex-builder ──► same stores
```

Namespaces: `roracle` (app and bundled Postgres, Elastic, parsers; object storage is external or Rook-Ceph in `rook-ceph`); operators in their own namespaces.
Use `mode: external` for platform-provided data services. NetworkPolicies: default deny; allow only the edges above.

## 2. Prerequisites

| Item | Requirement |
|---|---|
| Kubernetes | ≥ 1.29, 3+ worker nodes across failure domains; CSI with SSD storage class and snapshots |
| Operators | CloudNativePG (+ barman-cloud plugin, cert-manager), ECK, (Rook-Ceph via Terraform), each optional with the matching `mode: external` (see section 4.1) |
| Ingress | TLS-terminating ingress reachable only from the org proxy |
| Keycloak | Realm with X.509 browser + direct-grant flows, groups, client `roracle-web` (see Keycloak guide) |
| Inference | OpenAI-spec endpoint: chat models, one embedding model, reranker (`/v1/rerank` or equivalent) |
| Registry | All images in the [mirror manifest](airgap-mirror-manifest.md) pushed to the internal registry |
| DNS / certs | `roracle.<domain>`; internal CA trusted by pods |
| Observability | Prometheus/Grafana/Loki/OTel collector: existing, or the separate releases in `deploy/observability` (no subchart) |

## 3. Sizing (target: 500 concurrent users, 1M docs ≈ 50M chunks, 10k docs/day)

| Component | Replicas | Per replica | Notes |
|---|---|---|---|
| api | 3 (HPA 3–10) | 2 vCPU, 4 GiB | CPU-bound on fusion/streaming; scale on p95 latency |
| worker | 4 (HPA on queue depth) | 4 vCPU, 8 GiB | Parsing is delegated; worker is mostly I/O |
| web | 2 | 0.25 vCPU, 128 MiB | static |
| Docling | 2–4 | 4 vCPU, 16 GiB (or GPU) | ~200k pages/day ≈ 2.3 pages/s sustained; GPU or the inference endpoint's granite-docling model recommended |
| GROBID | 2 | 4 vCPU, 8 GiB | |
| Gotenberg | 2 | 2 vCPU, 4 GiB | LibreOffice conversion of office uploads; one conversion at a time per pod |
| Draft Gotenberg (`draftgotenberg`) | 2 | 1 vCPU / 2 GiB req, 4 GiB limit | Chromium HTML to PDF for paper-draft builds; port 3000; called by the api only |
| ClamAV | 2 | 2 vCPU, 4 GiB | signature mirror required (`parsers.clamav.mirrorUrl`); chart sets `StreamMaxLength`, `MaxFileSize`, `MaxScanSize` = `parsers.clamav.limits` (1100M, render fails if below the upload/evidence caps) and `AlertExceedsMax yes` (fail closed) |
| latex-builder | 2 | 2 vCPU, 2 GiB per build | job concurrency limited |
| Postgres | 3 (1 primary + 2 sync/async replicas) | 8 vCPU, 32 GiB, 500 GiB SSD | metadata ≈ 100 GiB at target |
| Elasticsearch | 3 master (small) + 3–4 data | data: 16 vCPU, 64 GiB (31 GiB heap), 2 TiB SSD | see vector math below |
| Object storage (Rook-Ceph RGW or external S3) | 3+ nodes with raw disks (Ceph), erasure coding | 4 vCPU, 16 GiB, disks for ~10 TiB usable | originals ≈ 5 TiB + derived PDFs + evidence |

Vector math (per embedding model index, 1 replica):

- 768-d (EmbeddingGemma): 50M × 768 × 4 B ≈ 154 GB float; `int8_hnsw` ≈ 39 GB + graph ≈ 7 GB.
- 4096-d (e5-mistral-7b): ≈ 820 GB float; `int8_hnsw` ≈ 205 GB → needs ~2× the data nodes above.
- Keep quantized vectors in page cache (off-heap RAM) for p95 < 500 ms; double during re-embed.

## 4. Install (Helm chart `deploy/helm/roracle`)

### 4.1 Prerequisites in the cluster

| Item | Needed when | Namespace (default `networkPolicy.operatorNamespaces`) |
|---|---|---|
| CloudNativePG operator (>= 1.26) + barman-cloud plugin + cert-manager | `postgres.mode=cnpg` with `backup.method=plugin` (default); the plugin needs cert-manager | `cnpg-system` |
| ECK operator | `elasticsearch.mode=eck` | `elastic-system` |
| Object storage | always external: Rook-Ceph RGW (Terraform `object_store = "rook-ceph"`) or your own S3; no bundled MinIO | `rook-ceph` (Terraform) |
| prometheus-operator CRDs | `postgres.monitoring.enabled` (default true; PodMonitor) | `monitoring` |
| Ingress controller | `ingress.enabled`; default `ingressNamespaceSelector` = namespace `ingress-nginx` | `ingress-nginx` |

Operators are installed by the platform (charts and versions in the [mirror manifest](airgap-mirror-manifest.md)).
All images and charts are mirrored first; set `global.imageRegistry`. Create the app namespace (`roracle`);
bundled data services render into the same namespace as the release.

### 4.2 Secrets (create before install; the chart never holds values)

| Secret (values key) | Keys | Required when |
|---|---|---|
| `secrets.existingSecret` | `ROR_DATABASE_URL` (`postgresql+psycopg://roracle:<pw>@roracle-pg-rw:5432/roracle`), `ROR_ELASTIC_URL`, `ROR_S3_ACCESS_KEY`, `ROR_S3_SECRET_KEY`, `ROR_LLM_API_KEY` (unless `secrets.llmApiKeyEnabled=false`) | always |
| `postgres.appCredentialsSecret` | type `kubernetes.io/basic-auth`: `username: roracle`, `password` (same password as in `ROR_DATABASE_URL`) | `postgres.mode=cnpg` |
| `postgres.backup.credentialsSecret.name` | `ACCESS_KEY_ID`, `ACCESS_SECRET_KEY` (a dedicated identity: not the app key) | cnpg + `backup.enabled` |
| `postgres.backup.endpointCA.name` | `ca.crt` of the S3 endpoint CA (only for a private CA) | cnpg + backup + private-CA S3 |
| `elasticsearch.appUserSecret` | basic-auth: `username: roracle_app`, `password`, `roles: roracle_app` | `elasticsearch.mode=eck` |
| `elasticsearch.snapshot.credentialsSecret` | `s3.client.default.access_key`, `s3.client.default.secret_key` (a dedicated identity: not the app key) | eck + `snapshot.enabled` |

`ROR_ELASTIC_URL` = `https://roracle_app:<password>@roracle-es-http.<ns>.svc:9200` (`<fullname>-es-http`; never the `elastic` user).
Optional: `caBundle.extraSecrets`, `elasticsearch.snapshot.extraCASecret` (private CAs), `imagePullSecrets`.

### 4.3 Install

```bash
helm install roracle deploy/helm/roracle -n roracle -f my-values.yaml --timeout 30m
```

Minimum `my-values.yaml`: `secrets.existingSecret`, `config.ROR_OIDC_ISSUER`, `config.ROR_OIDC_JWKS_URL`,
`config.ROR_LLM_BASE_URL`, `ingress.{enabled,host,className,tlsSecretName}`, the Secret names above, the
NetworkPolicy values in 4.6, and (external modes) the endpoints. Missing required values fail the render.

First-install ordering:

| `postgres.mode` | Migrate hook (`python -m roracle migrate --wait 600`, `backoffLimit: 0`) |
|---|---|
| `cnpg` | `post-install,pre-upgrade`: the database does not exist before install. The Job waits up to 10 min for it (`migrate --wait 600`). api/worker may restart until migrations finish. Hooks (migrate, ES snapshot repo + SLM) wait on operator-managed services: keep `--timeout 30m`. |
| `external` | `pre-install,pre-upgrade`: failure blocks the release. |

Post hooks run in weight order: ES snapshot repo + SLM (0, retries repository registration up to 5 min until the S3 bucket and snapshot identity exist). `migrate` is -5 (`post-install` with cnpg, `pre-upgrade`).

Render-time guards (the render fails, nothing is installed) cover: missing `secrets.existingSecret`, `ingress.host` (when ingress is on),
`config.ROR_OIDC_ISSUER` or `web.oidcAuthority`, `postgres.appCredentialsSecret` (cnpg), `postgres.backup.credentialsSecret.name` and
`postgres.backup.endpointURL` (backups on), `elasticsearch.appUserSecret` (eck), `elasticsearch.snapshot.credentialsSecret` and
`elasticsearch.snapshot.endpoint` (snapshots on); ClamAV `parsers.clamav.limits` below the upload and evidence caps or at 4G or more,
or a ClamAV `/tmp` smaller than limits x `maxThreads`, or `parsers.clamav.mirrorUrl` without `mirrorCIDRs`; the database connection budget
(replicas x pool + overflow, plus `postgres.reservedConnections`, above `postgres.parameters.max_connections` or the pooler budget);
`postgres.recovery.enabled` without backups or with a `postgres.backup.serverName` equal to the source; and the values removed in 0.2.0
(see [upgrades](upgrades.md)).

Smoke test: `GET /healthz`; sign in; upload a PDF until *Searchable*; ask a question and open a citation.
`helm test` is not provided.

### 4.3a Deploy with Terraform

`deploy/terraform` wraps the steps above (namespaces, pull secret, operator charts, Secrets, the `roracle` release) for an existing cluster
reached through a kubeconfig. It creates no cloud resources and does not replace the chart ([ADR-0016](../adr/0016-terraform-iac.md)).
Object storage is Rook-Ceph RGW (Terraform installs it, needs at least 3 nodes with raw disks) or your own S3; Terraform never deploys a MinIO tenant.
The full procedure, the placeholder gate, `allow_placeholders` and `protect_data` are in [Deploying with Terraform](deployment-terraform.md).

### 4.4 Ingress

| Path | Backend | Type |
|---|---|---|
| `/` | `web` :8080 (nginx SPA; proxies `/api/` to the api, prefix stripped, via `API_UPSTREAM`) | Prefix |
| `/mcp` | `api` :8000 | Exact |
| `/.well-known/oauth-protected-resource` | `api` :8000 | Prefix |

No controller-specific rewrites. Default annotation `nginx.ingress.kubernetes.io/proxy-body-size: 1100m` (keep >= `ROR_EVIDENCE_MAX_BYTES`;
replace it for other controllers). HSTS is set at the ingress/org proxy, not by the chart (the web pod sends the headers in [security](security.md) section 12).
The Keycloak realm CSP `frame-ancestors` must allow the app origin (silent renew uses an iframe at `/auth/silent`).

### 4.5 External vs bundled modes

| Service | Bundled | External |
|---|---|---|
| Postgres | `postgres.mode=cnpg` (CNPG `Cluster` `roracle-pg`, `ObjectStore`, daily `ScheduledBackup`) | `external`: nothing rendered; `ROR_DATABASE_URL` in the app Secret; egress via `networkPolicy.externalCIDRs.postgres` |
| Elasticsearch | `elasticsearch.mode=eck` (`Elasticsearch` `roracle`, snapshot hook Job) | `external`: `ROR_ELASTIC_URL` in the app Secret; `externalCIDRs.elasticsearch`; snapshots are yours |
| Object store | removed (use Rook-Ceph or an external S3) | `config.ROR_S3_ENDPOINT` (required), `postgres.backup.endpointURL`, `elasticsearch.snapshot.endpoint`; `networkPolicy.s3` (CIDRs and/or in-cluster peer) for egress, which also covers the CNPG/ES pods |
| Parsers | `parsers.{gotenberg,draftgotenberg,docling,grobid,clamav}.enabled` (Deployments, no egress) | `enabled=false` + `config.ROR_*_URL` (draft builds: `config.ROR_DRAFT_GOTENBERG_URL`) / `ROR_CLAMAV_HOST` + `ROR_CLAMAV_PORT` + `egressCIDRs.parsers` |

### 4.6 NetworkPolicy values you must set

`networkPolicy.enabled` (default true) denies all ingress and egress in the release namespace, then allows the edges in
[security](security.md) section 11. These stay denied until set:

| Value | Set to |
|---|---|
| `networkPolicy.ingressNamespaceSelector` | labels of the ingress controller namespace (hostNetwork controllers: add your own policy for the node CIDR) |
| `networkPolicy.egressCIDRs.{llm,oidc,otel,parsers}` | `cidrs` and/or `namespaceSelector`/`podSelector` + `ports` (pod/node/LB addresses, never Service ClusterIPs: policies apply after DNAT) |
| `networkPolicy.kubeAPI.cidrs` | apiserver endpoint IPs. Default empty = ports 443/6443 allowed to any destination (tighten in production) |
| `networkPolicy.operatorNamespaces` | namespaces of the ECK and CNPG operators if not the defaults |
| `networkPolicy.externalCIDRs.*`, `networkPolicy.s3`, `connectors.egressCIDRs` | only for external services / connector targets |
| `networkPolicy.monitoringNamespace`, `scrape.{enabled,ports}` | Prometheus namespace allowed to scrape api (8000) and worker (`worker.healthPort`, 8001) `/metrics` and Postgres; keep `ports` in step with `worker.healthPort` |

### 4.7 Web runtime environment

Set by the chart on the `web` Deployment (nginx substitutes placeholders in the SPA at start; no rebuild per site):

| Env | Source | Notes |
|---|---|---|
| `ROR_OIDC_AUTHORITY` | `web.oidcAuthority`, default `config.ROR_OIDC_ISSUER` | required; must be a URL |
| `ROR_OIDC_CLIENT_ID` | `web.oidcClientId` (`roracle-web`) | |
| `ROR_OIDC_ORIGIN` | derived from the authority | used in CSP `connect-src`/`frame-src`/`form-action` |
| `API_UPSTREAM` | `http://<fullname>-api:8000` | `/api/` proxy; unset = `/api/` returns 404 |

### 4.8 Values reference (top-level keys)

Every key with its default is in the generated [Helm values reference](../reference/helm-values.md). Overview:

| Key | Purpose |
|---|---|
| `global.imageRegistry`, `imagePullSecrets` | mirror prefix; pull secrets |
| `image.{backend,web}` | repository/tag/digest; tag empty = Chart `appVersion`; digest wins |
| `secrets` | `existingSecret` and key names; `llmApiKeyEnabled` |
| `config` | non-secret `ROR_*` -> ConfigMap; empty URLs auto-filled for enabled bundled services |
| `serviceAccount` | chart SA |
| `observability` | `otel.endpoint` (OTLP/HTTP, see below), `grafanaDashboard` ConfigMap |
| `metrics.enabled` | `prometheus.io/{scrape,port,path}` pod annotations on api and worker (default on); no ServiceMonitor is shipped |
| `podSecurityContext`, `containerSecurityContext`, `topologySpread` | restricted baseline, zone spread |
| `api`, `worker`, `web` | replicas, resources, HPA, PDB, `tmpSize`, grace periods |
| `rerank` | reranker `url`, `api` (`cohere` or `tei`), `model`; `secrets.rerankApiKeyEnabled` injects `ROR_RERANK_API_KEY` |
| `migrate` | hook Job resources and deadline (1500 s) |
| `ingress` | host, class, TLS secret, annotations |
| `postgres` | CNPG mode, instances, storage, image, `backup` (plugin/legacy, schedule, retention) |
| `elasticsearch` | ECK mode, version, nodeSets, storage, TLS, `snapshot` (bucket, SLM schedule/retention) |
| `parsers` | gotenberg, draftgotenberg, docling, grobid, clamav (+ PDB) |
| `connectors` | worker egress CIDRs/ports for connectors |
| `caBundle` | interim CA bundle (see section 8) |
| `networkPolicy` | see 4.6, including `networkPolicy.s3` (egress to the object store: CIDRs and/or an in-cluster peer) |

Observability (Prometheus, Grafana, Loki, OTel collector) are separate releases: see
`deploy/observability/README.md` in the repository.
Probes and metrics (api and worker):

- api `readinessProbe` is `GET /readyz`: Postgres `SELECT 1`, Elasticsearch ping and S3 `head_bucket`, each limited to 2 s. It answers 503 with `{"checks":{"db","es","s3"}}` (`ok`/`fail`, no error text) while a dependency is down. `/healthz` stays static and is the startup and liveness probe.
- worker serves `/healthz` and `/metrics` on `ROR_WORKER_HEALTH_PORT` (chart `worker.healthPort`, default `8001`, `0` = off). `/healthz` answers 503 when the job loop has not iterated for 5 min (liveness restarts the pod). While a job is running the worker reports healthy regardless of duration (long ingests are expected), so a hung handler is not detected by liveness.
- `GET /metrics` on api and worker is unauthenticated Prometheus text. The ingress routes only `/mcp`, `/.well-known/oauth-protected-resource` and `/` (web), and the web pod's nginx returns 404 for `/api/metrics`, so it is reachable only inside the cluster (NetworkPolicy `scrape` admits the monitoring namespace).
- OTel is opt-in: set `observability.otel.endpoint` (OTLP/HTTP, e.g. `http://collector:4318`; open `networkPolicy.egressCIDRs.otel`, default port 4318). The image bundles the `otel` extra and the entrypoint re-execs `api`/`worker` under `opentelemetry-instrument` when `OTEL_EXPORTER_OTLP_ENDPOINT` is set (FastAPI, SQLAlchemy and httpx are instrumented). Without the variable nothing changes.

### 4.9 Keycloak and reverse proxy

Apply [keycloak-setup.md](keycloak-setup.md). The org proxy must not buffer below `ROR_MCP_MAX_UPLOAD_BYTES` x 4/3 for `/mcp`.

## 5. Configuration

Every `ROR_*` setting, its default and whether an admin can override it at runtime is in the generated [settings reference](../reference/settings.md);
do not copy values from older notes. In Helm put non-secret settings under `config:` (ConfigMap), others in `api.extraEnv` or `worker.extraEnv`;
credentials only in the Secrets of section 4.2. The chart already sets the database pools, `ROR_JOB_*`, `ROR_API_THREADS`, `ROR_ES_*` and
`ROR_VECTOR_INDEX_TYPE` in `values.yaml`. Settings that admins change in the UI live in Postgres and win over the environment: see
[runtime settings](../admin/runtime-settings.md). Notes that affect how you deploy:

- **Elasticsearch credentials**: put them in `ROR_ELASTIC_URL` as userinfo, or leave them out and set `ROR_ELASTIC_USERNAME` and `ROR_ELASTIC_PASSWORD` (secret keys `secrets.keys.elasticUsername` and `elasticPassword`, optional). There is no API-key setting.
- **Database password**: `ROR_DATABASE_PASSWORD` (secret key `secrets.keys.databasePassword`, optional) is injected into `ROR_DATABASE_URL`, which must then carry a username. `ROR_DATABASE_URL` accepts `postgresql://`, `postgres://` or `postgresql+psycopg://`.
- **Upload and evidence caps**: `ROR_MAX_UPLOAD_BYTES` and `ROR_EVIDENCE_MAX_BYTES` (1 GiB by default). Keep ClamAV `StreamMaxLength`, `MaxFileSize` and `MaxScanSize` (`parsers.clamav.limits`, 1100M) and the reverse proxy body limit (ingress `proxy-body-size`, nginx `client_max_body_size`) at or above `ROR_EVIDENCE_MAX_BYTES`, or evidence uploads fail or stay `failed`. Uploads are streamed to object storage (`tmp/` prefix, then copied to `blobs/`), so the api needs no large `/tmp` (`api.tmpSize`, 1Gi). The bucket must allow multipart uploads and server-side copy for the app user. The api adds a 1-day `tmp/` expiry rule at start when it may; otherwise set it yourself (see [Terraform deployment](deployment-terraform.md), external S3 commands).
- **MCP uploads**: the org proxy must not buffer below `ROR_MCP_MAX_UPLOAD_BYTES` x 4/3 for `/mcp`.
- **Database**: `ROR_DB_CONNECT_TIMEOUT_S` applies to the api, worker, `/readyz` and the metrics collector; a `?connect_timeout=` in `ROR_DATABASE_URL` overrides it. `ROR_DB_DISABLE_PREPARED` is set by the chart with the PgBouncer pooler. Connection budget formula: `docs/perf/connection-budget.md`.
- **Jobs**: `ROR_JOB_LEASE_S` is 90, `ROR_JOB_DRAIN_S` is 120 in the chart. Keep `worker.terminationGracePeriodSeconds` at or above drain plus 30 (chart: 150).
- **Connectors**: `ROR_CONNECTOR_FS_ROOTS` is empty by default, so filesystem connectors are rejected. Mount sources read-only into the worker and the api (for example `/srv/papers:/data/papers:ro`). `ROR_CONNECTOR_MAX_RUN_S` (3600): a run holds one database transaction for its whole duration, so set Postgres `idle_in_transaction_session_timeout` above it (or `0`). Crawl targets outside the public internet need `ROR_CONNECTOR_HTTP_ALLOW_CIDRS` and, with NetworkPolicy on, `connectors.egressCIDRs`. See [connectors](../admin/connectors.md).
- **Retention and maintenance**: `ROR_RETENTION_DELETED_DAYS` (0 = never purge; irreversible, align it with backup retention) and `ROR_MAINTENANCE_INTERVAL_S` (86400), both under `config:`.
- **Office conversion** (`ROR_GOTENBERG_URL`, `ROR_GOTENBERG_TIMEOUT_S`): office uploads are converted to PDF in the parse stage; Docling and GROBID read the rendition. Gotenberg down or 5xx is retryable, an invalid file a permanent failure. `ROR_MAX_RENDITION_BYTES` caps the rendition. Docling enforces page and size caps itself: the chart sets `DOCLING_SERVE_MAX_NUM_PAGES=2000` and `DOCLING_SERVE_MAX_FILE_SIZE=629145600` (`parsers.docling.env`).
- **Gotenberg isolation**: the ingest instance runs `gotenberg --api-disable-download-from --webhook-disable --chromium-disable-routes --pdfengines-disable-routes --api-timeout=300s`. The draft-build instance (`parsers.draftgotenberg`, `ROR_DRAFT_GOTENBERG_URL`, Chromium enabled, called by the api only) runs `--api-disable-download-from --webhook-disable --pdfengines-disable-routes --libreoffice-disable-routes --api-timeout=120s`. Never share one instance: Chromium renders attacker-influenced HTML. Both get no egress, no secrets and a read-only root filesystem. External instances need the same hardening.
- **Draft builds**: `ROR_BUILD_CONCURRENCY` (2) caps simultaneous builds per api process (more is 503 `build service busy`; read once, so restart the api to change it). An empty draft Gotenberg URL gives 503 `build service not configured`. `ROR_LATEX_BUILDER=disabled` (default) makes `format: latex` drafts return 501; the optional service contract is in [latex builder](latex-builder.md).
- **Reranker**: `ROR_RERANK_URL` empty disables it. `ROR_RERANK_API` is `cohere` or `tei` (chart `rerank.api`); `ROR_RERANK_API_KEY` is an optional Bearer token. A slow or failed rerank keeps the fused order.
- **Rate limit**: `ROR_RATE_LIMIT_PER_MINUTE` (0 = off) is per api replica, so the cluster-wide limit is about N times the value; use ingress limits for a hard cap.
- **Elasticsearch index settings** (`ROR_ES_SHARDS`, `ROR_ES_REPLICAS`, `ROR_VECTOR_INDEX_TYPE`) apply when an index is created. Sizing: [capacity planning](capacity-planning.md). Do not change `ROR_READ_ALIAS`, `ROR_MUTATE_ALIAS` or `ROR_EMBED_INDEX` on a live system.

### 5.1 GPU host services (reranker, context-note model, planner)

- **Reranker (TEI)**: `deploy/game-01/reranker.sh` (pull on a connected host, `install` on the GPU host, `run`, `health`) serves `BAAI/bge-reranker-v2-m3` with `ghcr.io/huggingface/text-embeddings-inference:89-1.9` on `127.0.0.1:28080`, offline (`HF_HUB_OFFLINE=1`, model mounted read-only). Point the app at it with `ROR_RERANK_URL=http://<host>/rerank ROR_RERANK_API=tei ROR_RERANK_MODEL=bge-reranker-v2-m3` through an SSH tunnel or mesh address. TEI has no authentication: put a proxy in front if it is reachable beyond that and set `ROR_RERANK_API_KEY`. Budget: p95 of 800 ms for 200 passages.
- **Context-note model**: an Ollama variant with `PARAMETER num_ctx 16384` (the default 4096 silently truncates the document window); recipe in `deploy/game-01/README.md`. Set `ROR_CONTEXT_MODEL` to its name.
- **Planner model** for `ROR_AGENTIC_PLANNER_MODEL`: any small fast chat model on the same OpenAI-spec endpoint.

## 6. High availability

- Stateless tiers (`web`, `api`, `worker`, sidecars): ≥ 2 replicas, PodDisruptionBudget `minAvailable: 1`,
  topology spread across zones, readiness on `/readyz` (api).
- Postgres: CNPG automatic failover; app connects via the `-rw` service.
- Elasticsearch: the chart default is one nodeSet of 3 nodes with roles master, data and ingest; add dedicated masters and data nodes through `elasticsearch.nodeSets`. One replica shard (`ROR_ES_REPLICAS`); shard size target 30–50 GB.
- Object storage: Ceph erasure coding tolerates node/disk loss; replicate buckets to another site (RGW multisite or external S3 elsewhere) for DR.
- Worker jobs run under a lease (`ROR_JOB_LEASE_S`, renewed by a heartbeat): a crashed worker's job is requeued after the lease expires.
  On SIGTERM a running job gets `ROR_JOB_DRAIN_S` seconds, then is released without spending an attempt.
- Rolling upgrades: some migrations rebuild job indexes and need workers drained first; see [upgrades](upgrades.md).

## 7. Upgrades

SemVer: patch is drop-in, minor adds additive migrations with a rolling upgrade, major comes with release notes and manual steps. The procedure,
the chart 0.1 to 0.2.0 migration (MinIO removal), Elasticsearch 8.x to 9.x, the migration list with risky ones flagged, and re-embedding are in
[upgrades](upgrades.md). Take a Postgres backup and an ES snapshot before every minor or major upgrade ([runbook](operations-runbook.md) section 2).

### Upgrading to 0.2.0 (breaking)

Chart 0.2.0 removes the bundled MinIO tenant and the render fails on removed values. The migration steps and the value mapping are in [upgrades, chart 0.1 to 0.2.0](upgrades.md).

## 8. TLS trust, hooks and ordering (bundled data services)

Secrets, modes and NetworkPolicy values are in section 4. Notes specific to bundled services:

- TLS: ES (ECK self-signed) is on by default; the S3 endpoint should be HTTPS (Terraform requires TLS on Rook-Ceph RGW), its CA reaching the app via `caBundle.extraSecrets`. api/worker/migrate get an init container that builds a bundle (certifi + ES CA + `caBundle.extraSecrets`, e.g. the S3 CA),
  mounted over certifi's `cacert.pem` (`caBundle.certifiPath`, coupled to the backend image's Python layout; adjust if the image changes).
  `SSL_CERT_FILE`, `REQUESTS_CA_BUNDLE` and `AWS_CA_BUNDLE` point at it (httpx/openai), as do `ROR_ELASTIC_CA_CERTS` and `ROR_S3_CA_BUNDLE` (ES client, boto3).
  Clusters whose CSR signer differs from the kube root CA (e.g. EKS) add the signer CA via `caBundle.extraSecrets`.
- ES snapshots over https: an init container builds a JVM truststore (JDK cacerts + optional `elasticsearch.snapshot.extraCASecret`, the S3 CA); `ES_JAVA_OPTS` points at it.
  The post-install hook Job registers repository `roracle-s3` and SLM policy `roracle-daily`.
- Backup/snapshot buckets: create them with a dedicated identity per bucket (write-only for backups where the store supports it) and, where available, object lock/retention (keep >= backup/snapshot retention); the chart no longer creates users or retention.
- api/worker wait for the ECK CA Secret; after ECK CA rotation run `kubectl rollout restart deploy/roracle-api deploy/roracle-worker`.
- ES nodes run with `node.store.allow_mmap: false` (avoids ECK's privileged sysctl init container); drop it if a node-tuning DaemonSet sets `vm.max_map_count>=262144`.
- Proxies in front of the web pod must allow bodies >= the evidence cap (`ROR_EVIDENCE_MAX_BYTES`, 1 GiB); the web pod's nginx allows 1100m. Ingress annotations: `nginx.ingress.kubernetes.io/proxy-body-size: 1100m`.
- Pre-GA checklist: against your S3 implementation, test that the ES S3 repository (repository verification and a snapshot) and barman-cloud (base backup + WAL) can write into the object-lock buckets (they require Content-MD5/checksum headers on PUTs).
- Backups land on the configured S3. For DR replicate the backup buckets to another site (site replication or multisite sync, not automated by the chart)
  or point `postgres.backup.*` / `elasticsearch.snapshot.*` at an external store. See the [runbook](operations-runbook.md) section 3.
