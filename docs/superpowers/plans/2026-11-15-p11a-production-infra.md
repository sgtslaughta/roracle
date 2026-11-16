# P11a Production Infrastructure Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** rOracle installs on an air-gapped Kubernetes cluster from one Helm chart, with a hardened web image, HA data services as operator CRs, backups meeting spec §12 RPO/RTO, and CI that lints/templates/validates it all.

**Architecture:** `deploy/helm/roracle` (ADR-0013). App workloads = one backend image run as `api` / `worker` / `migrate` (ADR-0009); web = nginx-unprivileged serving the SPA. Data = CRs for operators the platform installs (CloudNativePG, ECK, MinIO Operator), each switchable to external endpoints. Parsers (Gotenberg, docling-serve, GROBID, ClamAV) are optional in-chart Deployments with no egress. No subchart dependencies (air-gap: `helm dependency build` needs network); observability stacks get separate values files.

**Tech Stack:** Helm 3, kubeconform, nginx-unprivileged, CNPG `Cluster`/`ScheduledBackup` (barman-cloud), ECK `Elasticsearch`, MinIO `Tenant`, GitLab CI.

**Spec:** `docs/spec.md` §2, §12; ADR-0001/0003/0009/0013; `docs/enterprise/{deployment-guide,security,operations-runbook,airgap-mirror-manifest,keycloak-setup}.md`.

**Out of scope (P11b, backend):** rate-limit enforcement, retention, embedding-model switch, `/metrics` + OTel instrumentation in the backend. Never edit `backend/src` or `backend/tests`.

## Global Constraints

- Every template file < 500 lines; `helm lint --strict` and `helm template` clean with default values AND `ci/full-values.yaml` (all optional parts on).
- Images: `{{ .Values.global.imageRegistry }}` prefix; tag defaults to `.Chart.AppVersion`; optional `digest` wins over tag.
- `Chart.yaml` `version` and `appVersion` == `/VERSION` (CI check `scripts/check-version.sh`).
- Pod security: `runAsNonRoot`, `readOnlyRootFilesystem: true` (writable dirs = emptyDir), `allowPrivilegeEscalation: false`, `capabilities.drop: [ALL]`, `seccompProfile: RuntimeDefault`, `automountServiceAccountToken: false`.
- Secrets never in values: every credential is an `existingSecret` name + key (fail via `required` if missing when its feature is on).
- Backend env = `ROR_*` from `backend/src/roracle/config.py` (read only). Health: `GET /healthz` on :8000. Commands: `python -m roracle api|worker|migrate`.
- No invented metrics. Backend exposes none (P11b gap). Queue depth and failed jobs come from CNPG custom queries on the existing `jobs` table; API latency from ingress-nginx metrics.

## Review Focus

1. NetworkPolicy: default deny ingress+egress in namespace; DNS egress allowed; parsers have zero egress; worker connector egress only to `connectors.egressCIDRs` — Task 3.
2. CSP header on every response incl. SPA fallback and error pages; no `unsafe-inline` scripts — Task 1.
3. Migrations Job is `pre-install,pre-upgrade` hook, runs before new api/worker pods; failure blocks the upgrade — Task 2.
4. No secret value rendered in any manifest; barman/ES snapshot credentials via secret refs — Task 4.
5. CNPG `archive_timeout` ≤ 5 min so RPO ≤ 5 min holds on idle clusters — Task 4.

---

### Task 1: Web image

**Files:** `frontend/Dockerfile`, `frontend/.dockerignore`, `frontend/nginx/default.conf.template`, `frontend/nginx/security-headers.conf`.
**Behaviour:** stage 1 `node:22-slim` + `corepack pnpm install --frozen-lockfile` (honours `NPM_CONFIG_REGISTRY` build arg) + `pnpm build` + `pnpm check:dist`; built with `VITE_OIDC_AUTHORITY=__ROR_OIDC_AUTHORITY__`, `VITE_OIDC_CLIENT_ID=__ROR_OIDC_CLIENT_ID__`. Stage 2 `nginxinc/nginx-unprivileged:1.27-alpine` on :8080; nginx `sub_filter` replaces placeholders in JS with env `ROR_OIDC_AUTHORITY`/`ROR_OIDC_CLIENT_ID` at runtime (envsubst templates, `NGINX_ENVSUBST_OUTPUT_DIR` on an emptyDir so readOnlyRootFS works). Optional `/api/` proxy to `${API_UPSTREAM}` (prefix stripped) when set. SPA fallback `try_files $uri /index.html`; hashed assets `immutable`, `index.html` `no-cache`; `/healthz` returns 200.
**Headers (always):** `Content-Security-Policy: default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; font-src 'self'; worker-src 'self' blob:; connect-src 'self' ${ROR_OIDC_ORIGIN}; frame-ancestors 'none'; base-uri 'self'; form-action 'self' ${ROR_OIDC_ORIGIN}; object-src 'none'`, `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`, `X-Frame-Options: DENY`, `Permissions-Policy: camera=(), microphone=(), geolocation=()`, `Cross-Origin-Opener-Policy: same-origin`; `server_tokens off`. LABEL `org.opencontainers.image.version` from build arg `VERSION`.
- [ ] Build: `docker build -f frontend/Dockerfile --build-arg VERSION=$(cat VERSION) -t roracle/web:dev .`
- [ ] Run with `--read-only --tmpfs /tmp` + envs; `curl -sI :8080/`, `/d/x` (fallback), `/healthz`, a hashed asset → headers present, placeholders replaced.
- [ ] Commit `feat(web): hardened nginx image with strict CSP`.

### Task 2: Chart core (api, worker, web, migrations)

**Files:** `deploy/helm/roracle/{Chart.yaml,values.yaml,.helmignore,values.schema.json?}`, `templates/{_helpers.tpl,configmap-env.yaml,api.yaml,worker.yaml,web.yaml,migrate-job.yaml,ingress.yaml,hpa.yaml,pdb.yaml,serviceaccount.yaml,NOTES.txt}`, `ci/full-values.yaml`, `scripts/check-version.sh`.
**Behaviour:** shared env ConfigMap from `config:` map (all `ROR_*` non-secret settings with endpoints defaulting to in-chart services when enabled, else `external.*`); secrets via `envFrom`/`secretKeyRef` to `secrets.existingSecret` (keys: `ROR_DATABASE_URL`, `ROR_S3_ACCESS_KEY`, `ROR_S3_SECRET_KEY`, `ROR_LLM_API_KEY`, …). OTel env (`OTEL_EXPORTER_OTLP_ENDPOINT`, `OTEL_SERVICE_NAME`, `OTEL_RESOURCE_ATTRIBUTES=service.version=<appVersion>`) when `observability.otel.endpoint` set. api: Deployment+Service :8000, startup/readiness/liveness `/healthz`, HPA 3–10 CPU; MCP served by api (`/mcp` ingress path). worker: separate Deployment, HPA on CPU (queue-depth external metric documented), no Service. web: Deployment+Service :8080. PDBs (`minAvailable: 1` when replicas>1); `topologySpreadConstraints` zone+host (`ScheduleAnyway`). Migrate Job: hook `pre-install,pre-upgrade`, weight -5, `backoffLimit: 0`, `hook-delete-policy: before-hook-creation`. Ingress: `/` → web, `/api` → api (rewrite documented) or via web proxy, `/mcp` → api; TLS secret ref. Resources from deployment-guide sizing.
- [ ] `helm lint --strict deploy/helm/roracle` and with `-f ci/full-values.yaml`; `helm template` both; `kubeconform -strict -ignore-missing-schemas -summary`.
- [ ] Commit `feat(helm): roracle chart with api, worker, web, migrations`.

### Task 3: Parsers + NetworkPolicies

**Files:** `templates/parsers.yaml` (range over `parsers.{gotenberg,docling,grobid,clamav}`: Deployment+Service, `enabled`, image, port, resources, probes, extra emptyDirs), `templates/networkpolicy.yaml`.
**Behaviour:** parser URL config (`ROR_GOTENBERG_URL`, `ROR_DOCLING_URL`, `ROR_GROBID_URL`, `ROR_CLAMAV_HOST`) auto-set when enabled, else from `external.*`. ClamAV signatures from `parsers.clamav.mirrorUrl` (freshclam private mirror) → that single egress allowed for clamav only when set. NetworkPolicies (when `networkPolicy.enabled`, default true): default-deny all; DNS egress (kube-dns, configurable selector); ingress-controller → web/api (namespaceSelector from values); web → api; api/worker → postgres/ES/MinIO (selectors or `external.*CIDRs`), inference/Keycloak/OTel CIDRs; worker → parsers; parsers: ingress from worker only, egress none; worker → `connectors.egressCIDRs` (default `[]`); Prometheus scrape ingress optional.
- [ ] lint/template/kubeconform both values sets; assert via `helm template | grep` that parser policies have `policyTypes: [Ingress, Egress]` and no `egress:` rules.
- [ ] Commit `feat(helm): optional parser services and default-deny network policies`.

### Task 4: Data services as CRs + backups

**Files:** `templates/data-postgres.yaml`, `templates/data-elasticsearch.yaml`, `templates/data-minio.yaml`, `templates/es-snapshot-job.yaml`.
**Behaviour:** `postgres.mode: cnpg|external`. CNPG `Cluster` (3 instances, storage class/size, `postgresql.parameters.archive_timeout: 5min`, `backup.barmanObjectStore` to S3/MinIO with creds secret ref, `retentionPolicy: 30d`, `monitoring.customQueriesConfigMap` → ConfigMap with `roracle_jobs` query: `count(*) by status, kind` and oldest queued age) + `ScheduledBackup` daily base backup; app user secret consumed as `ROR_DATABASE_URL` guidance. `elasticsearch.mode: eck|external`: ECK `Elasticsearch` 3 nodes (version, storage, `secureSettings` for S3 snapshot creds), post-install/upgrade hook Job (curl image) that PUTs snapshot repo `roracle-s3` + SLM policy `roracle-daily` (`0 30 1 * * ?`, retention 30d) using elastic user secret. `minio.mode: tenant|external`: MinIO `Tenant` (4 servers × 4 volumes, EC), creds secret ref; replication documented, not automated.
- [ ] lint/template/kubeconform (CRDs → `-ignore-missing-schemas` locally; CI uses `KUBECONFORM_SCHEMA_LOCATION` mirror incl. CRD catalog); `helm template | grep -iE 'password|secret_key' ` shows only refs.
- [ ] Commit `feat(helm): cnpg, eck and minio resources with backups`.

### Task 5: Observability + CI

**Files:** `deploy/helm/roracle/templates/{servicemonitor.yaml,grafana-dashboard.yaml}`, `deploy/helm/roracle/dashboards/roracle.json`, `deploy/observability/{kube-prometheus-stack.values.yaml,loki.values.yaml,otel-collector.values.yaml,README.md}`, `.gitlab-ci.yml`.
**Behaviour:** dashboard (ConfigMap label `grafana_dashboard: "1"`) panels: API p50/p95/p99 + 5xx rate (`nginx_ingress_controller_request_duration_seconds_bucket`, filtered by ingress name), queue depth by kind/status and oldest queued age (CNPG custom query metrics), failed jobs by kind (ingest stage failures), CNPG archive lag/replication, ES cluster health. `PodMonitor` for CNPG when `monitoring.enabled`. CI: `frontend` job (`node:22-slim`, corepack pnpm, `NPM_CONFIG_REGISTRY`; `tsc --noEmit` as lint, `vitest run`, `build`, `check:dist`); `helm` job (`alpine/helm` mirror image: `check-version.sh`, lint --strict ×2, template ×2 | kubeconform with `-schema-location $KUBECONFORM_SCHEMA_LOCATION`); `build-web` kaniko job mirroring `build`. Backend jobs unchanged.
- [ ] `python -m json.tool dashboards/roracle.json`; lint/template; CI YAML parses (`python -c 'import yaml…'`).
- [ ] Commit `feat(ci): frontend, helm and web image jobs` and `feat(helm): grafana dashboard and observability values`.

### Task 6: Docs

**Files:** `docs/enterprise/{deployment-guide,operations-runbook,airgap-mirror-manifest,security}.md`, `docs/adr/0013-repo-ci-release.md` (addendum only).
**Behaviour:** deployment-guide: Helm install steps, required secrets table, values reference (top-level keys), ingress paths, external vs bundled modes. Runbook: backup/restore drills (CNPG PITR restore into new Cluster via `bootstrap.recovery`, ES snapshot restore, MinIO site replication), RPO/RTO table, upgrade path (`helm upgrade` → migrate hook → rollout; embedding change ⇒ re-embed per ADR-0006, P11b), version bump procedure. Mirror manifest: every image/chart in the chart with pinned tag and "pin digest in mirror" column; operators' charts. Security: CSP as implemented, NetworkPolicy matrix.
- [ ] All files < 500 lines; links resolve.
- [ ] Commit `docs(enterprise): helm deployment, backups, mirror manifest`.
