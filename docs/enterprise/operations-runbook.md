# rOracle Operations Runbook

Audience: on-call operators. Commands assume namespace `roracle`, Helm release `roracle`,
CNPG cluster `roracle-pg`, ECK cluster `roracle` (service `roracle-es-http`). Adjust to your names.

## 1. Health at a glance

| Signal | Where | Healthy |
|---|---|---|
| API | `GET /healthz` | `{"status":"ok","version":"x.y.z"}` |
| Queue depth | `SELECT kind, status, count(*) FROM jobs GROUP BY 1,2;` | `queued` drains; few `failed` |
| Oldest queued job | `SELECT now() - min(run_after) FROM jobs WHERE status='queued';` | < 5 min |
| Search latency | Grafana: `roracle_search_seconds` p95 | < 500 ms |
| Chat TTFT | `roracle_chat_ttft_seconds` p95 | < 2 s |
| Ingest latency | `roracle_ingest_seconds` p95 (upload → searchable) | < 120 s |
| Elastic | `GET _cluster/health` | green |
| Postgres | `kubectl cnpg status roracle-pg` | primary + replicas streaming |

### Suggested alerts

Queue oldest job > 15 min; failed jobs > 50/h; search p95 > 1 s for 10 min; TTFT p95 > 4 s;
Elastic yellow > 15 min / red immediately; Postgres replication lag > 60 s; MinIO drive offline;
ClamAV signatures older than 3 days; 5xx rate > 1%; PAT use bursts.

## 2. Routine procedures

### Scale workers for a backlog
```bash
kubectl -n roracle scale deploy/roracle-worker --replicas=8
```
Workers claim jobs with `FOR UPDATE SKIP LOCKED`; adding replicas is always safe.

### Retry failed jobs
```sql
UPDATE jobs SET status='queued', attempts=0, run_after=now(), last_error=NULL
WHERE status='failed' AND kind='ingest' AND updated_at > now() - interval '1 day';
```
(The admin ingest dashboard does the same per job.)

### Evidence scans (`evidence_scan` job)
Each evidence upload queues a `jobs` row `kind='evidence_scan'`; the worker streams the blob to ClamAV and
sets `evidence.scan_status`: `pending` (queued), `clean`, `infected` (download blocked), `failed`
(blob missing or scanner rejected it, e.g. over `StreamMaxLength`) or `unscanned` (no `ROR_CLAMAV_HOST`).
Scanner outages retry like ingest jobs. Re-run a failed or stuck scan:
```sql
UPDATE jobs SET status='queued', attempts=0, run_after=now(), last_error=NULL
WHERE kind='evidence_scan' AND status='failed';
```
For `failed` rows caused by size, raise ClamAV limits (see deployment guide) and re-queue.

### Change the embedding model (zero downtime)
Planned flow (ADR-0006, P11b): the admin UI, `reembed` jobs and alias flip below are not implemented in the backend yet; use the downtime procedure in the next section until then.
1. Admin → Models → Embeddings → choose model → *Start re-embed*. A new index
   `chunks-<model>-<n>` is created and filled by `reembed` jobs.
2. Watch progress (admin dashboard or `SELECT count(*) FROM jobs WHERE kind='reembed' AND status<>'done'`).
3. When complete, rOracle flips the `chunks` alias. Old index is kept 7 days, then delete it.
Capacity: plan for double vector storage during the re-embed.

### Re-embed after embedding prompt/model change (pre-production only)
P2b put the document summary into chunk embedding prompts. Vectors indexed earlier are stale but
still functional, so this is optional. Downtime procedure:
1. Stop workers.
2. Drop the index: `DELETE /chunks-<slug>-1` (slug = lowercased model name, non-alphanumerics to `-`).
3. In Postgres (one statement; resets attempts, skips soft-deleted documents, enqueues only the reset rows):
   `WITH r AS (UPDATE document_versions dv SET stage='deduped', ingest_attempts=0, summary_embedding=NULL FROM documents d WHERE d.id=dv.document_id AND d.deleted_at IS NULL AND dv.stage='searchable' RETURNING dv.id) INSERT INTO jobs (kind, payload) SELECT 'ingest', jsonb_build_object('version_id', id::text) FROM r;`
4. Start workers.
`summary_embedding=NULL` is required: the stored summary vector is not model-stamped, so clearing it makes
`_index` lazily re-embed it with the new model. Near-duplicate detection compares summary vectors, which is
only meaningful within one embedding model.
Until each version is searchable again it is absent from search (non-owners lose visibility
first-hand; owners only via the document list). Do not use on a production corpus.

### Rebuild the search index from scratch
Planned (P11b): the admin *Maintenance* action does not exist yet; use the stage-reset statement above. Elastic holds no unique data. Create a fresh index and enqueue `index` jobs for every latest
version (admin → Maintenance → *Rebuild index*). Search stays on the old alias until done.

### Rotate secrets
- DB / S3 / Elastic credentials: update secret → `kubectl rollout restart deploy/roracle-api deploy/roracle-worker`.
- Keycloak signing keys: rotate in Keycloak; rOracle refetches JWKS on unknown `kid` automatically.
- PAT pepper: rotating invalidates all PATs — announce first.

### Release upgrade
1. Read release notes. 2. Check a recent CNPG backup (`kubectl get backup`) and ES snapshot (`GET _snapshot/roracle-s3/_all`); trigger `kubectl cnpg backup roracle-pg` before a minor/major upgrade.
3. Bump `/VERSION` (SemVer); `scripts/check-version.sh` (CI `helm` job) requires it to equal Chart `version` and `appVersion`. Tag `vX.Y.Z`; CI builds backend and web images.
4. `helm upgrade roracle deploy/helm/roracle -n roracle -f my-values.yaml --timeout 30m`. The `migrate` pre-upgrade hook runs first (`backoffLimit: 0`: a failed migration blocks the rollout and leaves the old pods running); then api/worker/web roll.
5. Verify `/healthz` version, upload + search + chat smoke test.
Rollback: `helm rollback` restores manifests but does NOT revert database migrations. Migrations are additive within a minor line, so the previous release normally still runs;
otherwise restore Postgres with PITR (3.1) to just before the upgrade. Embedding-model changes need a re-embed (ADR-0006; the automated flow is P11b, see the note below).
Also: after ECK CA rotation restart api/worker; update ClamAV signatures by refreshing the mirror (freshclam in the pod pulls from `parsers.clamav.mirrorUrl`; with no mirror configured signatures are only those baked into the image, so rebuild the image regularly).

## 3. Backup and restore

### RPO / RTO

| Store | Mechanism (chart defaults) | RPO | Restore |
|---|---|---|---|
| Postgres | CNPG WAL archiving to MinIO via barman-cloud plugin; `archive_timeout` forced to 5 min; immediate base backup at install + daily 02:00 (`postgres.backup.schedule`); retention 30d | <= 5 min (when WAL is being produced) | PITR into a new `Cluster` (3.1) |
| Elasticsearch | SLM `roracle-daily` 01:30 to repo `roracle-s3`, expire 30d (min 7, max 60) | <= 24 h; index is rebuildable from Postgres + MinIO | snapshot restore (3.2) or rebuild |
| MinIO | versioning; backup buckets with GOVERNANCE retention; site replication to a DR site (manual setup) | near-zero with replication, else loss of the cluster = loss of originals | 3.3 |
| Keycloak | your Keycloak policy | | |

RTO target <= 4 h. Order: Postgres -> MinIO (if lost) -> Elastic -> scale api/worker up. Alert on WAL archive age (Grafana panel, 300 s line).
A single-cluster MinIO also holds the backups: without replication to another site, a cluster loss loses data and backups together.

### 3.1 Postgres PITR into a new cluster

Restore never overwrites the live cluster. The new cluster reads the old one's backups (read-only) and must archive under a different `serverName`.

```yaml
apiVersion: postgresql.cnpg.io/v1
kind: Cluster
metadata: {name: roracle-pg-restore}
spec:
  instances: 3
  storage: {size: 100Gi}
  bootstrap:
    recovery:
      source: origin
      recoveryTarget: {targetTime: "2026-11-15 09:30:00+00"}   # omit recoveryTarget for latest
  externalClusters:
    - name: origin
      plugin:
        name: barman-cloud.cloudnative-pg.io
        parameters: {barmanObjectName: roracle-pg, serverName: roracle-pg}   # ObjectStore + original cluster name
  plugins:
    - name: barman-cloud.cloudnative-pg.io
      isWALArchiver: true
      parameters: {barmanObjectName: roracle-pg, serverName: roracle-pg-restore}
```

Add the same `imageName`, `postgresql.parameters`, `imagePullSecrets` and resources as the original (`kubectl get cluster roracle-pg -o yaml`).
If the app credentials Secret is not in the target namespace, restore it too (initdb is skipped: roles come from the backup).
Then:
1. Wait for `kubectl cnpg status roracle-pg-restore` healthy. Scale api/worker to 0 first (`kubectl scale deploy/roracle-api deploy/roracle-worker --replicas=0`; the HPA must be disabled or it scales back).
2. Update `ROR_DATABASE_URL` in `secrets.existingSecret` to `roracle-pg-restore-rw`, then `kubectl rollout restart` and scale back. (Or, once verified, delete the old cluster and recreate `roracle-pg` from the restore; keep a single owner of the Helm-managed name.)
3. Migrations are only needed when restoring across versions: `helm upgrade` runs them.
Rows written after the target time are lost; blobs in MinIO newer than the target are orphans (the GC job removes objects without a `blobs` row).

### 3.2 Elasticsearch restore

Use the `elastic` superuser (secret `roracle-es-elastic-user`), not `roracle_app`.
```bash
curl -s -u elastic:$PW "$ES/_snapshot/roracle-s3/_all?pretty" | grep snapshot\"   # list; repo is registered by the install hook
curl -s -u elastic:$PW -XDELETE "$ES/chunks-*"       # existing indices must be deleted/closed first
curl -s -u elastic:$PW -XPOST "$ES/_snapshot/roracle-s3/<snapshot>/_restore" \
  -H 'content-type: application/json' -d '{"indices":"chunks-*","include_global_state":false}'
curl -s -u elastic:$PW "$ES/_cluster/health?wait_for_status=yellow&timeout=30m"
```
On a rebuilt cluster re-run the hook (`helm upgrade`) first so repository `roracle-s3` exists. Documents ingested after the snapshot are missing:
re-queue them with the stage-reset statement of "Re-embed after embedding prompt/model change" (stops workers; no dedicated reindex CLI or job exists in the backend today).
Elastic holds no unique data, so when no usable snapshot exists use that same procedure to rebuild everything from Postgres + MinIO (search is down until versions are searchable again).

### 3.3 MinIO

- Lost objects/versions: restore the previous version (`mc undo` / `mc cp --version-id`) from the versioned bucket.
- Cluster loss: point `config.ROR_S3_ENDPOINT` (and `postgres.backup.endpointURL`, `elasticsearch.snapshot.endpoint`) at the DR site, which holds the replicated `roracle` bucket via site replication. Credentials/policies are not replicated unless you replicate IAM: create the `minio.users.*` there.
- Backup buckets (`roracle-pg-backup`, `roracle-es-snapshots`) are replicated the same way; GOVERNANCE retention blocks deletion by normal users for `minio.backupRetentionDays`.

### 3.4 Quarterly drill checklist

- [ ] Restore Postgres PITR to a time 1 h ago into a scratch namespace; compare row counts of `documents`, `jobs`.
- [ ] Restore the latest ES snapshot into a scratch ES; run a known query, compare hit counts.
- [ ] Verify MinIO replication status (`mc admin replicate status`) and open a replicated object.
- [ ] Point a scratch api/worker at the restored stores; smoke test upload, search, chat, citation.
- [ ] Record elapsed time against RTO 4 h and WAL archive age against RPO 5 min; file gaps.

## 4. Incident playbooks

**Uploads stuck in "Scanned/Parsed"** — check sidecar health (`kubectl get pods -l app=docling`),
then `last_error` on failed jobs. OCR timeouts on huge scans: raise the Docling timeout or split
the file; retry the job.

**Search slow** — Elastic: hot threads, heap pressure, page-cache misses (quantized vectors must
fit in RAM); reduce rerank depth temporarily (admin → Retrieval).

**Chat errors** — inference endpoint status; model removed from allowlist; check
`roracle_llm_errors_total{model=…}`.

**Suspected data exposure** — revoke PATs of the user (admin), set affected documents `private`,
export audit log for the window (`audit_log` by `target_id` and time), escalate to security.

**Malware detected** — job fails at *Scanned*, file quarantined (blob not linked), uploader
notified; security gets an audit event. Nothing to clean unless detection happened after
ingestion (signature update): admin → Documents → *Quarantine* removes it from search.

## 5. Admin API

All `/admin/*` routes need an admin JWT (PATs get 403) and every call is written to `audit_log`.
Lists page with `?before_id=&limit=` (default 50, max 200) and return `{items, next_before_id}`.

| Endpoint | Operator use |
|---|---|
| `GET /admin/health` | Dependency probe (db, elastic, storage), queue depth/age, connector scheduler tick. `degraded` = at least one check failed. |
| `GET /admin/jobs?status=failed&kind=` | Inspect failed jobs and their `last_error`. |
| `GET /admin/jobs/summary` | Jobs per kind/status and versions per pipeline stage. |
| `POST /admin/jobs/{id}/retry` | Retry one failed job (409 otherwise); ingest jobs get a fresh attempt budget. |
| `POST /admin/jobs/retry-failed {kind?}` | Requeue all failed jobs (optionally one kind) after fixing the cause, e.g. Ollama or ES outage. |
| `GET /admin/review?source=all\|upload\|connector&page=` | Versions parked for near-duplicate review (offset paged); decide via the near-dup endpoint. |
| `GET /admin/stats` | Document/blob counts, storage bytes, top uploaders. |
| `GET /admin/users`, `/admin/groups` | Users mirror (as of last login), per-user usage, group sizes. |
| `GET /admin/tags`, `POST /admin/tags/merge {sources, target}` | Consolidate duplicate tags; ES tags follow via `acl_sync`. |
| `GET /admin/audit?action=<prefix>&user_id=` | Audit trail search. |
| `GET/POST/PUT/DELETE /admin/connectors*` | Manage connectors, trigger runs, inspect `runs`; retrying a failed `connector_run` job returns 409 (skipped in bulk) while that connector has an active run. |
| `GET/PUT /admin/settings` | Runtime setting overrides. |
| `GET /admin/needs-title` | Documents awaiting a title. |
| `GET /admin/ratings` | Rating overview. |
| `GET /me/notifications`, `POST /me/notifications/read` | Caller's inbox (any user); admins also receive `ops.job_failed`; kinds also include `proposal.new`, `note.reply`, `note.on_document` (public notes only). |

Paging: `/admin/users` and `/admin/review` use offset `page=`; the other lists use keyset `before_id=`.
