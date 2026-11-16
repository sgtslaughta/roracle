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

Namespaces: `roracle` (app and bundled Postgres, Elastic, MinIO, parsers); operators in their own namespaces.
Use `mode: external` for platform-provided data services. NetworkPolicies: default deny; allow only the edges above.

## 2. Prerequisites

| Item | Requirement |
|---|---|
| Kubernetes | ≥ 1.29, 3+ worker nodes across failure domains; CSI with SSD storage class and snapshots |
| Operators | CloudNativePG (+ barman-cloud plugin, cert-manager), ECK, MinIO Operator, each optional with the matching `mode: external` (see section 4.1) |
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
| ClamAV | 2 | 2 vCPU, 4 GiB | signature mirror required (`parsers.clamav.mirrorUrl`); chart sets `StreamMaxLength`, `MaxFileSize`, `MaxScanSize` = `parsers.clamav.limits` (1100M, render fails if below the upload/evidence caps) and `AlertExceedsMax yes` (fail closed) |
| latex-builder | 2 | 2 vCPU, 2 GiB per build | job concurrency limited |
| Postgres | 3 (1 primary + 2 sync/async replicas) | 8 vCPU, 32 GiB, 500 GiB SSD | metadata ≈ 100 GiB at target |
| Elasticsearch | 3 master (small) + 3–4 data | data: 16 vCPU, 64 GiB (31 GiB heap), 2 TiB SSD | see vector math below |
| MinIO | 4–8 nodes, erasure coding | 4 vCPU, 16 GiB, disks for ~10 TiB usable | originals ≈ 5 TiB + derived PDFs + evidence |

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
| MinIO Operator | `minio.mode=tenant` | `minio-operator` |
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
| `postgres.backup.credentialsSecret.name` | `ACCESS_KEY_ID`, `ACCESS_SECRET_KEY` (= `minio.users.pgBackup` keys) | cnpg + `backup.enabled` |
| `postgres.backup.endpointCA.name` | `ca.crt` = content of `kube-root-ca.crt` (render fails without it when MinIO auto-cert is on) | cnpg + backup + tenant + `requestAutoCert` |
| `elasticsearch.appUserSecret` | basic-auth: `username: roracle_app`, `password`, `roles: roracle_app` | `elasticsearch.mode=eck` |
| `elasticsearch.snapshot.credentialsSecret` | `s3.client.default.access_key`, `s3.client.default.secret_key` (= `minio.users.esSnapshot` keys) | eck + `snapshot.enabled` |
| `minio.configurationSecret` | `config.env` with `export MINIO_ROOT_USER=...` / `export MINIO_ROOT_PASSWORD=...`; charset `[A-Za-z0-9._~-]` only (e.g. `openssl rand -hex 32`) | `minio.mode=tenant` |
| `minio.users.app`, `.pgBackup`, `.esSnapshot` | `CONSOLE_ACCESS_KEY`, `CONSOLE_SECRET_KEY` (users are created by the `minio-policy` hook Job with one-bucket policies) | tenant |

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
| `cnpg` | `post-install,pre-upgrade`: the database does not exist before install. The Job waits up to 10 min for it (`migrate --wait 600`). api/worker may restart until migrations finish. Hooks (migrate, `minio-policy`, ES snapshot repo + SLM) wait on operator-managed services: keep `--timeout 30m`. |
| `external` | `pre-install,pre-upgrade`: failure blocks the release. |

Post hooks run in weight order: `minio-policy` (-10, creates users, policies, retention) then ES snapshot repo + SLM (0, retries repository registration up to 5 min until the MinIO user exists). `migrate` is -5 (`post-install` with cnpg, `pre-upgrade`).

Smoke test: `GET /healthz`; sign in; upload a PDF until *Searchable*; ask a question and open a citation.
`helm test` is not provided.

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
| Object store | `minio.mode=tenant` (`Tenant` `roracle-minio`, svc `minio`) | `external`: set `config.ROR_S3_ENDPOINT`; `networkPolicy.s3Cidrs` for egress |
| Parsers | `parsers.{gotenberg,docling,grobid,clamav}.enabled` (Deployments, no egress) | `enabled=false` + `config.ROR_*_URL` / `ROR_CLAMAV_HOST` + `ROR_CLAMAV_PORT` + `egressCIDRs.parsers` |

### 4.6 NetworkPolicy values you must set

`networkPolicy.enabled` (default true) denies all ingress and egress in the release namespace, then allows the edges in
[security](security.md) section 11. These stay denied until set:

| Value | Set to |
|---|---|
| `networkPolicy.ingressNamespaceSelector` | labels of the ingress controller namespace (hostNetwork controllers: add your own policy for the node CIDR) |
| `networkPolicy.egressCIDRs.{llm,oidc,otel,parsers}` | `cidrs` and/or `namespaceSelector`/`podSelector` + `ports` (pod/node/LB addresses, never Service ClusterIPs: policies apply after DNAT) |
| `networkPolicy.kubeAPI.cidrs` | apiserver endpoint IPs. Default empty = ports 443/6443 allowed to any destination (tighten in production) |
| `networkPolicy.operatorNamespaces` | namespaces of ECK, CNPG, MinIO operators if not the defaults |
| `networkPolicy.externalCIDRs.*`, `s3Cidrs`, `connectors.egressCIDRs` | only for external services / connector targets |
| `networkPolicy.monitoringNamespace`, `scrape.{enabled,ports}` | Prometheus namespace allowed to scrape api (8000) and worker (`worker.healthPort`, 8001) `/metrics` and Postgres; keep `ports` in step with `worker.healthPort` |

### 4.7 Web runtime environment

Set by the chart on the `web` Deployment (nginx substitutes placeholders in the SPA at start; no rebuild per site):

| Env | Source | Notes |
|---|---|---|
| `ROR_OIDC_AUTHORITY` | `web.oidcAuthority`, default `config.ROR_OIDC_ISSUER` | required; must be a URL |
| `ROR_OIDC_CLIENT_ID` | `web.oidcClientId` (`roracle-web`) | |
| `ROR_OIDC_ORIGIN` | derived from the authority | used in CSP `connect-src`/`frame-src`/`form-action` |
| `API_UPSTREAM` | `http://<fullname>-api:8000` | `/api/` proxy; unset = `/api/` returns 404 |

### 4.8 Values reference (top-level keys; see `values.yaml` comments)

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
| `migrate` | hook Job resources and deadline (1500 s) |
| `ingress` | host, class, TLS secret, annotations |
| `postgres` | CNPG mode, instances, storage, image, `backup` (plugin/legacy, schedule, retention) |
| `elasticsearch` | ECK mode, version, nodeSets, storage, TLS, `snapshot` (bucket, SLM schedule/retention) |
| `minio` | tenant mode, servers/volumes, auto-cert, users, `backupRetentionDays`, `policyJob` |
| `parsers` | gotenberg, docling, grobid, clamav (+ PDB) |
| `connectors` | worker egress CIDRs/ports for connectors |
| `caBundle` | interim CA bundle (see section 8) |
| `networkPolicy` | see 4.6 |

Observability (Prometheus, Grafana, Loki, OTel collector) are separate releases: see
[`deploy/observability/README.md`](../../deploy/observability/README.md). 
Probes and metrics (api and worker):

- api `readinessProbe` is `GET /readyz`: Postgres `SELECT 1`, Elasticsearch ping and S3 `head_bucket`, each limited to 2 s. It answers 503 with `{"checks":{"db","es","s3"}}` (`ok`/`fail`, no error text) while a dependency is down. `/healthz` stays static and is the startup and liveness probe.
- worker serves `/healthz` and `/metrics` on `ROR_WORKER_HEALTH_PORT` (chart `worker.healthPort`, default `8001`, `0` = off). `/healthz` answers 503 when the job loop has not iterated for 5 min (liveness restarts the pod). While a job is running the worker reports healthy regardless of duration (long ingests are expected), so a hung handler is not detected by liveness.
- `GET /metrics` on api and worker is unauthenticated Prometheus text. The ingress routes only `/mcp`, `/.well-known/oauth-protected-resource` and `/` (web), and the web pod's nginx returns 404 for `/api/metrics`, so it is reachable only inside the cluster (NetworkPolicy `scrape` admits the monitoring namespace).
- OTel is opt-in: set `observability.otel.endpoint` (OTLP/HTTP, e.g. `http://collector:4318`; open `networkPolicy.egressCIDRs.otel`, default port 4318). The image bundles the `otel` extra and the entrypoint re-execs `api`/`worker` under `opentelemetry-instrument` when `OTEL_EXPORTER_OTLP_ENDPOINT` is set (FastAPI, SQLAlchemy and httpx are instrumented). Without the variable nothing changes.

### 4.9 Keycloak and reverse proxy

Apply [keycloak-setup.md](keycloak-setup.md). The org proxy must not buffer below `ROR_MCP_MAX_UPLOAD_BYTES` x 4/3 for `/mcp`.

## 5. Configuration reference (environment, prefix `ROR_`)

| Variable | Example | Purpose |
|---|---|---|
| `ROR_DATABASE_URL` | `postgresql+psycopg://roracle:…@pg-rw:5432/roracle` | Postgres (read-write service) |
| `ROR_S3_ENDPOINT`, `ROR_S3_BUCKET`, `ROR_S3_ACCESS_KEY`, `ROR_S3_SECRET_KEY` | `https://minio.roracle-data:9000`, `roracle` | Blob store |
| `ROR_OIDC_ISSUER`, `ROR_OIDC_AUDIENCE`, `ROR_OIDC_JWKS_URL` | `https://kc/realms/research`, `roracle` | Token validation |
| `ROR_MAX_UPLOAD_BYTES` | `524288000` | Upload limit (500 MB) |
| `ROR_EVIDENCE_MAX_BYTES` | `1073741824` | Max evidence file size (default 1 GiB). Keep ClamAV `StreamMaxLength`/`MaxFileSize`/`MaxScanSize` >= this value (>= 1 GiB, with `AlertExceedsMax yes`) and the reverse proxy `client_max_body_size` (nginx; ingress `proxy-body-size`) >= this value, or evidence uploads fail or stay `failed`. Uploads are streamed to object storage (`tmp/` prefix, then copied to `blobs/`): the API needs no large `/tmp` (`api.tmpSize` default `1Gi`). The bucket must allow multipart uploads and server-side copy for the app user, and should expire `tmp/` after 1 day (`mc ilm rule add --prefix tmp/ --expire-days 1 <alias>/<bucket>`; the bundled MinIO hook does this) |
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
| `ROR_DB_CONNECT_TIMEOUT_S` | `5` | Postgres connect timeout (seconds) for API, worker, `/readyz` and the metrics collector. Raise for cross-AZ/managed PG under load; a `?connect_timeout=` in `ROR_DATABASE_URL` overrides it |
| `ROR_RATE_LIMIT_PER_MINUTE` | `0` | Per-token request budget per API replica (token bucket, `0` = off): `429` + `Retry-After` when exceeded; PATs and JWTs are separate buckets; with N replicas the cluster-wide limit is about N x this value (use ingress limits for a hard cap). See security.md section 8 |
| `ROR_CONNECTOR_FS_ROOTS` | empty | Comma or JSON list of directories filesystem connectors may read (a connector path must resolve, symlinks included, to a root or below it). Empty = filesystem connectors are rejected. Mount sources **read-only** into the worker (and the API, which validates paths at create), e.g. `volumes: [/srv/papers:/data/papers:ro]` with `ROR_CONNECTOR_FS_ROOTS=/data/papers`; give the mount nothing but the documents to ingest |
| `ROR_CONNECTOR_MAX_ITEMS` | `1000` | Items that need work (new, changed, too large; unchanged items do not count) per connector run; more = run ends `partial`, the rest is picked up next run |
| `ROR_CONNECTOR_MAX_RUN_S` | `3600` | Wall-clock cap per connector run (`partial` when hit). A run holds one database transaction (the job claim) for its whole duration: set Postgres `idle_in_transaction_session_timeout` above this value (or `0`) |
| `ROR_CONNECTOR_TICK_S` | `60` | Scheduler interval: the worker enqueues due scheduled connectors this often (a self-rescheduling `connector_tick` job, created at worker start) |
| `ROR_CONNECTOR_HTTP_ALLOW_CIDRS` | empty | Comma or JSON list of CIDRs (e.g. `10.20.0.0/16,fd12:3456::/32`) that HTTP crawl connectors may reach although they are not public addresses. Empty = only public internet hosts. Needed for air-gapped/internal crawling. Link-local (`169.254.0.0/16`, `fe80::/10`, cloud metadata), the IPv4 inside a mapped address when it is link-local/metadata, multicast and unspecified addresses are blocked even inside an allowed CIDR. IPv4-mapped answers (`::ffff:a.b.c.d`) follow the IPv4 rules (allowlist `10.0.0.0/8`, not a mapped CIDR). Any IPv6 CIDR overlapping a translator/tunnel range (`::/96`, `64:ff9b::/96`, `64:ff9b:1::/48`, `2002::/16`, `2001::/32`) must be a `/96` (e.g. `64:ff9b::/96` for NAT64); its low 32 bits are vetted as IPv4. The translator CIDR must be the translator's real `/96` prefix (RFC 6052 `/96` layout); NAT64 deployments using `/32`-`/64` layouts are unsupported. IPv6 loopback (`::1`) cannot be allowlisted: use IPv4 `127.0.0.0/8` for local test crawls. Invalid CIDR = startup error |
| `ROR_CONNECTOR_HTTP_TIMEOUT_S` | `60` | Per-request cap for HTTP crawl connectors, connect to last byte (also bounded by the remaining run time) |
| `ROR_DOCLING_URL`, `ROR_GROBID_URL`, `ROR_CLAMAV_HOST` | service URLs | Ingest sidecars |
| `ROR_RETENTION_DELETED_DAYS` | `0` | Hard-purge documents soft-deleted more than this many days ago (chunks, versions, notes, evidence rows; blob refcounts decremented). `0` = never purge (trash kept forever). Irreversible: align with backup retention. Chart: `config.ROR_RETENTION_DELETED_DAYS` |
| `ROR_MAINTENANCE_INTERVAL_S` | `86400` | Period of the self-rescheduling `maintenance` job (purge, blob GC, ES tag reconcile); created at worker start. Chart: `config.ROR_MAINTENANCE_INTERVAL_S` |
| `ROR_WORKER_HEALTH_PORT` | `8001` | Worker `/healthz` + `/metrics` port; `0` disables the listener (set by the chart from `worker.healthPort`) |
| `ROR_CLAMAV_PORT` | `3310` | clamd port (chart: `parsers.clamav.port` when bundled; external clamd: set `config.ROR_CLAMAV_PORT`) |
| `ROR_ELASTIC_USERNAME`, `ROR_ELASTIC_PASSWORD` | empty | ES basic auth, instead of URL userinfo (secret keys `secrets.keys.elasticUsername/elasticPassword`, optional) |
| `ROR_DATABASE_PASSWORD` | empty | Injected into `ROR_DATABASE_URL` (URL-escaped for you; the URL must carry a username); secret key `secrets.keys.databasePassword`, optional. `ROR_DATABASE_URL` itself accepts `postgresql://`, `postgres://` or `postgresql+psycopg://` (driver normalised) |
| `ROR_ELASTIC_CA_CERTS`, `ROR_S3_CA_BUNDLE` | empty | PEM CA file paths for ES / S3 TLS; the chart sets both to the merged bundle when `caBundle` is active |
| `ROR_GOTENBERG_URL`, `ROR_GOTENBERG_TIMEOUT_S` | `http://gotenberg:3000`, `300` | Office (doc/docx/odt/rtf/ppt/pptx/odp/xls/xlsx/ods) uploads are converted to PDF in the parse stage; the original is kept untouched, the rendition is stored at `derived/{version_id}/rendition.pdf`, and Docling/GROBID read the rendition (page/bbox provenance refers to it). Empty URL = office files go to Docling directly. Gotenberg down or 5xx = retryable (fails after retries are exhausted); invalid file = permanent failure |
| `ROR_NEAR_DUP_SUMMARY_THRESHOLD` | `0.97` | Cosine similarity of document summary embeddings at or above which a new upload is parked for a near-duplicate decision (in addition to the MinHash `ROR_NEAR_DUP_THRESHOLD`); only visible documents are compared |
| `ROR_MAX_RENDITION_BYTES` | `524288000` | Cap on the Gotenberg PDF rendition (streamed, aborted when exceeded); over = permanent failure. Page/size caps are enforced by Docling itself: set `DOCLING_SERVE_MAX_NUM_PAGES=2000` and `DOCLING_SERVE_MAX_FILE_SIZE=629145600` on the docling-serve container (over-limit documents fail; the error is not distinguishable, so they surface as a conversion failure). The ingest Gotenberg is a dedicated, isolated instance with chromium routes disabled (office → PDF only); the later paper-drafts HTML/Markdown → PDF build (spec §8) needs its own separate Chromium-enabled instance. Deploy Gotenberg isolated: own namespace, deny-all egress NetworkPolicy (ingress from worker only), no secrets, read-only root FS, command `gotenberg --api-disable-download-from --webhook-disable --chromium-disable-routes --pdfengines-disable-routes --api-timeout=300s` |
| `ROR_DRAFT_GOTENBERG_URL`, `ROR_BUILD_TIMEOUT_S`, `ROR_BUILD_MAX_PDF_BYTES`, `ROR_BUILD_CONCURRENCY`, `ROR_LATEX_BUILDER` | empty, `120`, `104857600`, `2`, `disabled` | Paper-draft PDF build (`POST /drafts/{id}/build`, synchronous). Markdown goes through the bundled pandoc (pypandoc-binary wheel, run with `--sandbox`, rlimits, empty env, stdout cap) and a Python AST sanitizer, then to a **separate Chromium-enabled Gotenberg** (never the ingest instance): command `gotenberg --api-disable-download-from --webhook-disable --pdfengines-disable-routes --libreoffice-disable-routes --api-timeout=120s`, own namespace, deny-all egress NetworkPolicy (ingress from api only), no secrets, read-only root FS. `ROR_BUILD_TIMEOUT_S` is one deadline for the whole build (both pandoc passes and the Gotenberg response stream). `ROR_BUILD_CONCURRENCY` (default `2`) caps simultaneous builds per api process because builds are CPU-heavy; beyond it the route returns 503 `build service busy`; the cap is read once at first use, so changing it needs an api restart. Empty URL = build returns 503 `build service not configured`. The response is streamed and aborted past `ROR_BUILD_MAX_PDF_BYTES`; it must start with `%PDF-`. `ROR_LATEX_BUILDER=disabled` = `format: latex` drafts return 501; the optional service contract is in `latex-builder.md` |
| `ROR_DRAFT_MAX_BYTES`, `ROR_WRITING_MAX_CITATIONS`, `ROR_WRITING_VALIDATE_BUDGET_S`, `ROR_WRITING_MODEL` | `2097152`, `200`, `10`, empty | Paper drafts. `ROR_DRAFT_MAX_BYTES`: Markdown cap in UTF-8 bytes per save (over = 413). `ROR_WRITING_MAX_CITATIONS`: distinct citation keys per draft (over = blocking validation finding, fail closed). `ROR_WRITING_VALIDATE_BUDGET_S`: wall-clock budget in seconds for the deterministic checks (exceeded = blocking finding, fail closed). `ROR_WRITING_MODEL`: chat model for the style check and citation judge; empty = the first of `ROR_CHAT_MODELS`. Published PDFs name only `everyone`-visible searchable references; others render as "Restricted reference" |

Runtime settings that admins change in the UI (model allowlist, thresholds, retention, profiles) live in
Postgres, not env. Admins can override `chat_models`, `chat_default_model`, `chat_passages`, `chat_note_min_upvotes`, `chat_note_min_ratio`, `rerank_depth`,
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
manual steps. Always take a Postgres backup and an ES snapshot before a minor/major upgrade (runbook section 2, Release upgrade).

P2b changed chunk embedding prompts (summary context): existing vectors stay usable; optional re-embed in the operations runbook.

## 8. TLS trust, hooks and ordering (bundled data services)

Secrets, modes and NetworkPolicy values are in section 4. Notes specific to bundled services:

- TLS: ES (ECK self-signed) and MinIO (`requestAutoCert`, signed by the cluster CA) are on by default. api/worker/migrate get an init container that builds a bundle (certifi + ES CA + `kube-root-ca.crt` + `caBundle.extraSecrets`),
  mounted over certifi's `cacert.pem` (`caBundle.certifiPath`, coupled to the backend image's Python layout; adjust if the image changes).
  `SSL_CERT_FILE`, `REQUESTS_CA_BUNDLE` and `AWS_CA_BUNDLE` point at it (httpx/openai), as do `ROR_ELASTIC_CA_CERTS` and `ROR_S3_CA_BUNDLE` (ES client, boto3).
  Clusters whose CSR signer differs from the kube root CA (e.g. EKS) add the signer CA via `caBundle.extraSecrets`.
- ES snapshots over https: an init container builds a JVM truststore (JDK cacerts + `kube-root-ca.crt` + optional `elasticsearch.snapshot.extraCASecret`); `ES_JAVA_OPTS` points at it.
  The post-install hook Job registers repository `roracle-s3` and SLM policy `roracle-daily`.
- `minio-policy` hook Job: creates the three MinIO users (not Tenant `users:`, which grants consoleAdmin), attaches one-bucket policies,
  sets GOVERNANCE default retention `minio.backupRetentionDays` on the backup buckets (keep >= backup/snapshot retention). Versioning and object lock come from the Tenant `buckets[].objectLock` (set at bucket creation, not changeable later). It runs on every upgrade, first (hook weight -10).
- api/worker wait for the ECK CA Secret; after ECK CA rotation run `kubectl rollout restart deploy/roracle-api deploy/roracle-worker`.
- ES nodes run with `node.store.allow_mmap: false` (avoids ECK's privileged sysctl init container); drop it if a node-tuning DaemonSet sets `vm.max_map_count>=262144`.
- Proxies in front of the web pod must allow bodies >= the evidence cap (`ROR_EVIDENCE_MAX_BYTES`, 1 GiB); the web pod's nginx allows 1100m. Ingress annotations: `nginx.ingress.kubernetes.io/proxy-body-size: 1100m`.
- Pre-GA checklist: against the mirrored MinIO release, test that the ES S3 repository (repository verification and a snapshot) and barman-cloud (base backup + WAL) can write into the object-lock buckets (they require Content-MD5/checksum headers on PUTs).
- Backups land on the bundled MinIO by default. For DR replicate the backup buckets to another site (MinIO site replication, not automated by the chart)
  or point `postgres.backup.*` / `elasticsearch.snapshot.*` at an external store. See the [runbook](operations-runbook.md) section 3.
