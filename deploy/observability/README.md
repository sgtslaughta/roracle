# Observability stack (optional, separate releases)

No subchart dependencies (air-gap: `helm dependency build` needs network). Mirror charts and images first, replace `MIRROR.example.com` in the values files.

```sh
helm upgrade -i kps prometheus-community/kube-prometheus-stack -n monitoring --create-namespace -f kube-prometheus-stack.values.yaml
helm upgrade -i loki grafana/loki -n observability --create-namespace -f loki.values.yaml          # needs Secret loki-s3
helm upgrade -i otel open-telemetry/opentelemetry-collector -n observability -f otel-collector.values.yaml
helm upgrade -i roracle ../helm/roracle ... --set observability.grafanaDashboard.enabled=true --set postgres.monitoring.enabled=true
```

Create the Loki buckets (`loki-chunks`, `loki-ruler`, `loki-admin`) in MinIO first with `mc mb` (the roracle chart only creates its own buckets).

CI: empty `KUBECONFORM_SCHEMA_LOCATION` validates against online GitHub schemas; air-gapped runners must set it to a mirror including CRDs.

Allow Prometheus into the app namespace: `networkPolicy.monitoringNamespace` (default `monitoring`).

## Dashboard panels and sources

| Panel | Source |
|---|---|
| API p50/p95/p99, 5xx rate | ingress-nginx metrics (other controllers: edit queries) |
| Queue depth, oldest queued, failed jobs | CNPG custom query `roracle_jobs` (`cnpg_roracle_jobs_*`); failed = rows in `failed` status |
| Replication lag, WAL pending | CNPG built-ins `cnpg_pg_replication_lag`, `cnpg_collector_pg_wal_archive_status` |
| WAL archive age | custom query `roracle_archiver` (`cnpg_roracle_archiver_seconds_since_last_archive`) |

Both CNPG panels carry a 300 s line (RPO target). Elasticsearch health has no panel (no exporter bundled; deliberate deviation from the plan). WAL archive age shows no data until the first archive; on an idle database `archive_timeout` (5 min) only forces a segment switch when there is WAL activity, so the age can grow while idle.

## P11b gap

The backend has no `/metrics` and no OTel instrumentation. Until P11b the collector receives nothing; no API/worker application metrics, traces or structured-log shipping exist.
