# rOracle Operations Runbook

Audience: on-call operators. Commands assume namespace `roracle`, Helm release `roracle`,
CNPG cluster `roracle-pg`, ECK cluster `roracle-es`. Adjust to your names.

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

### Change the embedding model (zero downtime)
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
   `WITH r AS (UPDATE document_versions dv SET stage='deduped', ingest_attempts=0 FROM documents d WHERE d.id=dv.document_id AND d.deleted_at IS NULL AND dv.stage='searchable' RETURNING dv.id) INSERT INTO jobs (kind, payload) SELECT 'ingest', jsonb_build_object('version_id', id::text) FROM r;`
4. Start workers.
Until each version is searchable again it is absent from search (non-owners lose visibility
first-hand; owners only via the document list). Do not use on a production corpus.

### Rebuild the search index from scratch
Elastic holds no unique data. Create a fresh index and enqueue `index` jobs for every latest
version (admin → Maintenance → *Rebuild index*). Search stays on the old alias until done.

### Rotate secrets
- DB / S3 / Elastic credentials: update secret → `kubectl rollout restart deploy/roracle-api deploy/roracle-worker`.
- Keycloak signing keys: rotate in Keycloak; rOracle refetches JWKS on unknown `kid` automatically.
- PAT pepper: rotating invalidates all PATs — announce first.

### Release upgrade
1. Read release notes. 2. Snapshot Postgres (CNPG backup) and Elastic.
3. `helm upgrade roracle … --version X.Y.Z` (pre-upgrade hook runs migrations).
4. Verify `/healthz` version, upload + search + chat smoke test. Roll back with `helm rollback`
   (migrations are additive within a minor line).

## 3. Backup and restore

| Store | Method | RPO | Restore |
|---|---|---|---|
| Postgres | CNPG continuous WAL archiving + nightly base backup to object storage | ≤ 5 min | CNPG `Cluster` with `bootstrap.recovery` to a target time |
| MinIO | Bucket versioning + site replication to DR site | near-zero | fail over clients to DR endpoint |
| Elastic | Snapshot lifecycle (daily) to S3 repository | ≤ 24 h | restore snapshot **or** rebuild index from Postgres + MinIO |
| Keycloak | per your Keycloak backup policy | | |

Restore order: Postgres → MinIO (if needed) → Elastic (restore or rebuild) → scale API/workers up.
RTO target ≤ 4 h. Test a full restore into a scratch namespace every quarter.

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
