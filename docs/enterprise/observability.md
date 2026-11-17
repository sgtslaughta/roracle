# Observability

Audience: operators and SRE. Back to the [enterprise index](README.md) and the [operations runbook](operations-runbook.md). What rOracle exports, the optional monitoring stack that ships as values files in `deploy/observability/`, the Grafana dashboard in the chart, and what is not provided. Series are defined in the [metrics reference](../reference/metrics.md); chart keys in the [Helm values reference](../reference/helm-values.md).

## 1. What is exported

| Signal | Source | How it leaves the pod |
|---|---|---|
| Prometheus metrics | api `GET /metrics` (port 8000) and worker `GET /metrics` (`ROR_WORKER_HEALTH_PORT`, 8001) | pull, but **nothing scrapes them out of the box**: with `metrics.enabled` (default true) the pods carry `prometheus.io/*` annotations, which the bundled kube-prometheus-stack (Prometheus Operator) ignores. You must add a `PodMonitor` (section 6) |
| Postgres metrics | CloudNativePG instance managers, a `PodMonitor`, and two custom queries | pull through the `PodMonitor` when `postgres.monitoring.enabled` (default true; needs the prometheus-operator CRDs) |
| OTLP traces and metrics | the api and worker, under `opentelemetry-instrument` | push, OTLP over HTTP (`http/protobuf`) to `observability.otel.endpoint`, empty by default |
| Logs | stdout and stderr of every pod | nothing in this repo collects them; the collector values have a logs pipeline (OTLP in, Loki out), and the chart sets only the OTLP endpoint, protocol, service name and resource attributes on the pods |

Health probes (`/healthz`, `/readyz`, the worker's `/healthz`) are described in the [health runbook](runbook-health-alerts.md). **The repo ships no `PodMonitor`, `ServiceMonitor` or scrape job for the api and worker** (only CNPG's own `PodMonitor` for Postgres). Until you add one, every `roracle_*` panel on the dashboard is empty. The `/metrics` endpoints are never routed through the ingress. With the application NetworkPolicy on (`networkPolicy.enabled`), `networkPolicy.scrape` (ports 8000 and 8001) allows only the namespace in `networkPolicy.monitoringNamespace` (default `monitoring`) to reach them; if your worker `healthPort` differs, change `networkPolicy.scrape.ports`. OTLP egress to the collector needs `networkPolicy.egressCIDRs.otel` (port 4318).

## 2. The optional stack (`deploy/observability/`)

Three separate Helm releases, no subchart dependencies (a `helm dependency build` would need the network). Mirror the charts and images first, and replace the `MIRROR.example.com` placeholder in the values files with your registry (Terraform does this for you with `mirror_registry`; the example prod tfvars enable `kube_prometheus_stack` 70.0.0, `loki` 6.30.0 and `otel_collector` 0.120.0, see [Terraform deployment](deployment-terraform.md)).

```bash
helm upgrade -i kps prometheus-community/kube-prometheus-stack -n monitoring --create-namespace -f deploy/observability/kube-prometheus-stack.values.yaml
helm upgrade -i loki grafana/loki -n observability --create-namespace -f deploy/observability/loki.values.yaml   # needs Secret loki-s3
helm upgrade -i otel open-telemetry/opentelemetry-collector -n observability -f deploy/observability/otel-collector.values.yaml
helm upgrade -i roracle deploy/helm/roracle ... --set observability.grafanaDashboard.enabled=true --set postgres.monitoring.enabled=true
```

| Release | Key settings in the values file |
|---|---|
| kube-prometheus-stack | `global.imageRegistry` (mirror); Prometheus retention 15d and `retentionSize` 40GB on a 50Gi `ReadWriteOnce` volume; `podMonitorSelectorNilUsesHelmValues` and `serviceMonitorSelectorNilUsesHelmValues` false, so `PodMonitor`s, `ServiceMonitor`s and rules in every namespace are picked up (the CNPG `PodMonitor` lives in the app namespace); `enableRemoteWriteReceiver: true` (target of the collector's remote-write exporter); Grafana sidecar loads dashboards labelled `grafana_dashboard: "1"` from all namespaces and has a `Loki` data source at `http://loki-gateway.observability.svc`; Alertmanager enabled |
| Loki | single-binary, one replica, filesystem TSDB index (schema v13, 24h period), chunks in an **external** S3 (`https://s3.example.com` placeholder, region `us-east-1`, path-style) in buckets `loki-chunks`, `loki-ruler` and `loki-admin` created beforehand; credentials from the existing Secret `loki-s3` (`AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`); `auth_enabled: false`; retention 30d; 20Gi persistence; caches off |
| OpenTelemetry collector | the contrib image (`otelcol-contrib`), deployment mode; receivers OTLP gRPC `0.0.0.0:4317` and HTTP `0.0.0.0:4318`; processors `memory_limiter` (75%, spike 20%) and `batch`; metrics go to Prometheus remote write (`http://kps-kube-prometheus-stack-prometheus.monitoring:9090/api/v1/write`), logs to Loki over OTLP HTTP (`http://loki-gateway.observability.svc/otlp`); **no trace pipeline** (`traces: null`) |

Point rOracle at the collector with `observability.otel.endpoint=http://otel-opentelemetry-collector.observability:4318`. Loki's S3 endpoint is a placeholder you must replace, and there is no bundled object store for it (Rook-Ceph in this repo backs only rOracle's own buckets). Create the three Loki buckets, a user and the Secret `loki-s3` yourself.

## 3. The Grafana dashboard

`observability.grafanaDashboard.enabled=true` renders a ConfigMap (`<fullname>-dashboard`) with the label `grafana_dashboard: "1"` (key set by `observability.grafanaDashboard.label`; it must match the Grafana sidecar's label). The file is `deploy/helm/roracle/dashboards/roracle.json`: dashboard *rOracle overview* (uid `roracle-overview`, 1 min refresh). Variables: `datasource`, `namespace` (from `roracle_jobs`) and `ingress` (a text box). Panels:

| Panel | Query source | Notes |
|---|---|---|
| API latency p50/p95/p99 | `roracle_http_request_duration_seconds_bucket` | all routes together, 5 min rate |
| API 5xx rate | `roracle_http_requests_total` | 5xx rate and total rate, requests per second |
| Queue depth by kind and status | `roracle_jobs` | `max by (kind, status)` |
| Oldest queued job age | `roracle_jobs_oldest_queued_seconds` | `max by (kind)` |
| Failed jobs by kind | `roracle_jobs{status="failed"}` | rows in `failed` status |
| Job runs and ingest stage failures | `roracle_job_runs_total`, `roracle_ingest_stage_failures_total` | rates per second; needs the worker scraped |
| CNPG replication lag (RPO <= 300s) | `cnpg_pg_replication_lag` | red above 300 s |
| WAL archive age (RPO <= 300s) | `cnpg_roracle_archiver_seconds_since_last_archive` | red above 300 s; the chart's custom query over `pg_stat_archiver`; no sample until the first archive |
| WAL segments pending archive | `cnpg_collector_pg_wal_archive_status{value="ready"}` | |

Not on the dashboard: Elasticsearch health (no exporter is bundled, a deliberate gap), search latency by route, rerank fallbacks, Ceph health. Add them in your own dashboards from the [metrics reference](../reference/metrics.md) and your exporters.

## 4. Alert rules

**The repo ships no alert rules**: there is no `PrometheusRule` in the chart or in `deploy/observability/`. Alertmanager is enabled by the stack values but has no rules from rOracle. The conditions to start from are in the [health runbook](runbook-health-alerts.md#2-suggested-alerts); implement them as a `PrometheusRule` in your monitoring release.

## 5. Custom Postgres queries

With `postgres.monitoring.enabled` the chart creates the ConfigMap `<fullname>-pg-queries` (CNPG custom queries): an archiver query (seconds since the last WAL archive, from `pg_stat_archiver`, run on the `postgres` database; exposed as `cnpg_roracle_archiver_seconds_since_last_archive`) and a jobs query (jobs per kind and status with the oldest queued age, run on the `roracle` database; exposed under the `cnpg_roracle_jobs_` prefix). The dashboard no longer uses the jobs query, because the backend exposes the same data as `roracle_jobs` at `/metrics`; the CNPG one can be dropped from the Postgres monitoring config. CNPG names its series `cnpg_<query>_<column>`.

## 6. Wiring checklist

1. api and worker scraped. The annotations are not enough with the bundled stack: apply a `PodMonitor` like this one in the app namespace (labels and port names are from the chart templates: pods carry `app.kubernetes.io/name: roracle`, `app.kubernetes.io/instance: <release>` and `app.kubernetes.io/component: api|worker`; the api container port is named `http` (8000) and the worker's is named `health` (`worker.healthPort`, 8001)). The stack's selectors accept any namespace.

```yaml
apiVersion: monitoring.coreos.com/v1
kind: PodMonitor
metadata: {name: roracle, namespace: roracle}
spec:
  selector:
    matchLabels: {app.kubernetes.io/name: roracle, app.kubernetes.io/instance: roracle}
    matchExpressions: [{key: app.kubernetes.io/component, operator: In, values: [api, worker]}]
  podMetricsEndpoints:
    - port: http
      path: /metrics
    - port: health
      path: /metrics
```

   Each endpoint matches only the pods that have a port of that name. The scrape also needs `networkPolicy.monitoringNamespace` to match Prometheus's namespace.
2. `networkPolicy.monitoringNamespace` matches the namespace Prometheus runs in.
3. `postgres.monitoring.enabled` only with the prometheus-operator CRDs present (it renders a `PodMonitor` through the CNPG cluster).
4. `observability.grafanaDashboard.label` matches the Grafana sidecar label.
5. Optional OTLP: `observability.otel.endpoint` and `networkPolicy.egressCIDRs.otel`.
6. Alert rules created ([section 4](#4-alert-rules)).
