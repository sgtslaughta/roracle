# Observability stack (optional, separate releases)

No subchart dependencies (air-gap: `helm dependency build` needs network). Mirror charts and images first, replace `MIRROR.example.com` in the values files.

```sh
helm upgrade -i kps prometheus-community/kube-prometheus-stack -n monitoring --create-namespace -f kube-prometheus-stack.values.yaml
helm upgrade -i loki grafana/loki -n observability --create-namespace -f loki.values.yaml          # needs Secret loki-s3
helm upgrade -i otel open-telemetry/opentelemetry-collector -n observability -f otel-collector.values.yaml
helm upgrade -i roracle ../helm/roracle ... --set observability.grafanaDashboard.enabled=true --set postgres.monitoring.enabled=true
```

Loki points at an external S3 placeholder (`s3.example.com`, credentials from Secret `loki-s3`); create the buckets (`loki-chunks`, `loki-ruler`, `loki-admin`) there first. The chart bundles no object store (the MinIO tenant was removed in 0.2.0), so Loki always needs an S3 endpoint you operate. For the Rook-Ceph RGW from the Terraform stack, create a dedicated user and the three buckets there and set the endpoint and CA in `loki.values.yaml`; the stack limits RGW ingress to the app namespace (`roracle-rgw-ingress`), so add your own policy for the observability namespace (see [network security](../../docs/enterprise/security/network.md)).

CI: empty `KUBECONFORM_SCHEMA_LOCATION` validates against online GitHub schemas; air-gapped runners must set it to a mirror including CRDs.

Allow Prometheus into the app namespace: `networkPolicy.monitoringNamespace` (default `monitoring`).

## Dashboard panels and sources

| Panel | Source |
|---|---|
| API p50/p95/p99, 5xx rate | api `/metrics`: `roracle_http_request_duration_seconds`, `roracle_http_requests_total` |
| Queue depth, oldest queued, failed jobs | api `/metrics`: `roracle_jobs`, `roracle_jobs_oldest_queued_seconds`; failed = rows in `failed` status |
| Job runs, ingest stage failures | worker `/metrics`: `roracle_job_runs_total`, `roracle_ingest_stage_failures_total` |
| Replication lag, WAL pending | CNPG built-ins `cnpg_pg_replication_lag`, `cnpg_collector_pg_wal_archive_status` |
| WAL archive age | custom query `roracle_archiver` (`cnpg_roracle_archiver_seconds_since_last_archive`) |

Both CNPG panels carry a 300 s line (RPO target). Elasticsearch health has no panel (no exporter bundled; deliberate deviation from the plan). WAL archive age shows no data until the first archive; on an idle database `archive_timeout` (5 min) only forces a segment switch when there is WAL activity, so the age can grow while idle.

## Application metrics and traces

api and worker pods carry `prometheus.io/*` annotations (`metrics.enabled`); add a scrape job or PodMonitor for them in the monitoring release. With `observability.otel.endpoint` set they also send OTLP/HTTP traces and metrics to the collector (no trace backend bundled). The CNPG custom query `roracle_jobs` is no longer used by the dashboard and can be dropped from the Postgres monitoring config.
