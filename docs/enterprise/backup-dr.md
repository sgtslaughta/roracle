# Backup and disaster recovery

Audience: operators and SRE. Back to the [enterprise index](README.md) and the [operations runbook](operations-runbook.md). Commands assume namespace `roracle`, Helm release `roracle`, CNPG cluster `roracle-pg` (`<fullname>-pg`), ECK cluster `roracle` (service `roracle-es-http`). Adjust to your names. Key names are in the [Helm values reference](../reference/helm-values.md); the object-store layout is in [storage](../architecture/storage.md).

## 1. What is backed up, and how

| Store | Holds | Mechanism (chart defaults) | Needed for restore? |
|---|---|---|---|
| Postgres (system of record) | users, documents, versions, ACLs, labels, notes, jobs, audit log, settings | CNPG WAL archiving to S3 through the barman-cloud plugin (`postgres.backup.method: plugin`), `archive_timeout` forced to 5 min, base backup at install (`immediate: true`) and daily at 02:00 (`postgres.backup.schedule`), retention 30d (`postgres.backup.retentionPolicy`) | Yes |
| Object store, app bucket `roracle` | original blobs `blobs/`, parse output `derived/`, uploads in flight `tmp/` | none in the chart: Rook-Ceph replication (RGW multisite), `rclone` to a DR site, or your S3 provider's replication | Yes (originals cannot be recreated) |
| Object store, backup buckets `roracle-pg-backup`, `roracle-es-snapshots` | the two backups above | object lock with default retention, writer-only policy (section 3) | n/a |
| Elasticsearch | chunks, vectors, ACL copy | SLM policy `roracle-daily` at 01:30 into repository `roracle-s3`, `expire_after` 30d, `min_count` 7, `max_count` 60 (`elasticsearch.snapshot.*`); registered by the `*-es-snapshot` hook Job | No: rebuildable (section 2) |
| Keycloak | identities, groups, roles | your Keycloak policy (outside rOracle) | Yes, for logins |

Both backups are on by default (`postgres.backup.enabled`, `elasticsearch.snapshot.enabled`) and each needs an S3 endpoint and a credentials secret. With `object_store = "rook-ceph"` Terraform creates the buckets and writer identities; with an external S3 they are yours ([Terraform guide](deployment-terraform.md), section 10).

## 2. Elasticsearch: snapshot or rebuild, both are supported

Elasticsearch holds no unique data: every chunk can be recreated from Postgres and the object store. The chart supports both ways back:

- **Snapshot restore.** Fastest, because vectors are not recomputed. Recovers to the last snapshot (RPO up to 24 h), then documents ingested after it are re-queued (section 6).
- **Rebuild.** `POST /api/admin/reindex` with the current model builds a fresh generation from the parsed documents (embedding calls dominate the time) and swaps the read alias at the end. Use it when no usable snapshot exists. Sizing and speed: [capacity planning](capacity-planning.md); mechanics: [search index lifecycle](../architecture/search-index-lifecycle.md).

Choose rebuild when the snapshot is older than the last model or mapping change, or when the cluster is new. Search is down until versions are searchable again after a from-scratch rebuild on an empty cluster.

## 3. Object-lock backup buckets

`object_store = "rook-ceph"` runs a bucket-setup Job (the backend image, boto3, as the admin object-store user) for `roracle-pg-backup` and `roracle-es-snapshots`:

- S3 object lock enabled at bucket creation, with a default retention of `ceph.object_lock.retention_days` (30) in mode `ceph.object_lock.mode` (`GOVERNANCE` by default, `COMPLIANCE` optional). `GOVERNANCE` lets the admin bypass for break-glass; with `COMPLIANCE` nobody can shorten or delete before expiry, so a typo in `retention_days` becomes permanent storage. Keep it at or above `postgres.backup.retentionPolicy` and `elasticsearch.snapshot.retention`.
- A lifecycle rule `noncurrent-and-multipart`: noncurrent versions expire after `retention + 1` days, incomplete multipart uploads abort after 1 day. Locked buckets therefore cost about twice the retention window in storage: size the OSDs for it.
- A bucket policy: the per-bucket writer can get, put, list and delete (which creates a delete marker), and is denied version deletion, governance bypass, retention and lock changes, and lifecycle and policy changes. The app identity has no grant.
- The app bucket `roracle` has no object lock, because blob GC and the retention purge must be able to delete.

Verify with the admin keys (writer keys cannot read the lock or versioning configuration):

```bash
aws s3api get-object-lock-configuration --bucket roracle-pg-backup --endpoint-url <s3>
aws s3api get-bucket-lifecycle-configuration --bucket roracle-es-snapshots --endpoint-url <s3>
```

With an external S3 the same properties are an operator duty (exact statements: [Terraform guide](deployment-terraform.md), section 10). A single-cluster object store holds the backups next to the data they protect: without replication to another site, a cluster loss loses data and backups together. Replicate the buckets (RGW multisite or scheduled `rclone sync`), or point `s3.pg_backup_bucket` and `s3.es_snapshot_bucket` at an S3 on another site.

## 4. RPO and RTO

| Store | RPO | Restore path | Time-to-restore driver |
|---|---|---|---|
| Postgres | <= 5 min while WAL is produced (`archive_timeout` 5 min; the dashboard draws a 300 s line) | PITR in place (section 5) | base backup size, WAL replay |
| Elasticsearch | <= 24 h from snapshots; none needed when rebuilt from Postgres + object store | snapshot restore (section 6) or rebuild (section 2) | snapshot size, or embedding throughput |
| Object store | near zero with multisite replication, otherwise the last `rclone` run; without replication a cluster loss is a loss of originals | section 7 | replica sync |
| Keycloak | your policy | your procedure | |

The RTO target is **4 hours** for Postgres, then object store (only if lost), then Elasticsearch, then scale api and worker up. The figure is an operator target stated by this documentation: no code measures or enforces it. Measure your own with the drill (section 8) and replace it. Alert on WAL archive age: the dashboard panel *WAL archive age (RPO <= 300s)* turns red at 300 s ([observability](observability.md)). The archive age shows no data until the first archive, and on an idle database it can grow because `archive_timeout` only forces a segment switch when there is WAL activity.

After the first install confirm that a base backup exists (`kubectl get backup`; trigger one with `kubectl cnpg backup <fullname>-pg --method plugin --plugin-name barman-cloud.cloudnative-pg.io`). The immediate backup can run before the backup bucket exists or its writer identity works (with rook-ceph the bucket-setup Job runs before the release; with external S3 the bucket and identity are yours). WAL archiving retries automatically.

## 5. Postgres point-in-time recovery (in place, Helm-managed name)

Under the default-deny NetworkPolicy only the Helm-managed cluster `<fullname>-pg` is reachable, so the restore reuses that name. The chart bootstraps it from the backups (`postgres.recovery`) instead of `initdb`. The new cluster must archive under a new `serverName`, otherwise it collides with the old WAL archive; the chart fails the render when `postgres.backup.serverName` is empty or equals the recovery source.

1. Confirm the backups exist and are recent: `kubectl get backup`, `kubectl cnpg status <fullname>-pg`. Deleting the Cluster deletes its PVCs.
2. Stop writers (disable the HPA first, or it scales back): `kubectl scale deploy/<fullname>-api deploy/<fullname>-worker --replicas=0`.
3. `kubectl delete cluster <fullname>-pg`.
4. Upgrade with recovery enabled:

```bash
helm upgrade roracle deploy/helm/roracle -n roracle -f my-values.yaml --timeout 30m \
  --set postgres.recovery.enabled=true \
  --set-string postgres.recovery.targetTime="2026-11-15 09:30:00+00" \
  --set postgres.backup.serverName=<fullname>-pg-r1
```

   Omit `targetTime` for the latest point; `postgres.recovery.sourceServerName` defaults to `<fullname>-pg`. The Cluster is re-created by this upgrade, so with recovery enabled the `migrate` hook runs as `post-upgrade` (after the cluster is up).
5. Verify: `kubectl cnpg status <fullname>-pg`, row counts of `documents` and `jobs`; trigger a base backup and confirm it completes (`kubectl get backup`).
6. Keep `postgres.backup.serverName` (the new archive path) in your values permanently, set `postgres.recovery.enabled=false` again so a later cluster re-creation does not replay old backups, and scale api and worker back up.

Each further restore needs another new `serverName` (`-r2`, ...). Rows written after the target time are lost; blobs newer than the target are orphans that the blob GC removes ([routine runbook](runbook-routine.md#maintenance-retention-purge-blob-gc-tag-reconcile-maintenance-job)). Migrations run on the upgrade when you restore across versions. After a Postgres restore run the tag reconcile (`POST /api/admin/maintenance/reconcile`) so Elasticsearch ACLs converge to the restored database.

## 6. Elasticsearch restore from a snapshot

Use the `elastic` superuser (secret `roracle-es-elastic-user`), not `roracle_app`.

```bash
curl -s -u elastic:$PW "$ES/_snapshot/roracle-s3/_all?pretty" | grep snapshot\"   # list; the repo is registered by the install hook
curl -s -u elastic:$PW -XDELETE "$ES/chunks-*"       # existing indices must be deleted or closed first
curl -s -u elastic:$PW -XPOST "$ES/_snapshot/roracle-s3/<snapshot>/_restore" \
  -H 'content-type: application/json' -d '{"indices":"chunks-*","include_global_state":false}'
curl -s -u elastic:$PW "$ES/_cluster/health?wait_for_status=yellow&timeout=30m"
```

On a rebuilt cluster re-run the hook (`helm upgrade`) first so repository `roracle-s3` exists. Documents ingested after the snapshot are missing: re-queue them with the stage-reset statement in [runbook-routine](runbook-routine.md#re-embed-after-embedding-promptmodel-change-pre-production-only) (it stops workers), or run `POST /api/admin/reindex` with the current model to rebuild into a fresh generation. After restoring a pre-cutover snapshot check that `chunks` points at the index Postgres records as the active one (`GET /api/admin/reindex`); if not, run a reindex.

## 7. Object-store loss or damage

- **Lost objects or versions.** The app bucket is not versioned unless you enable it: restore from the replicated copy, or from a noncurrent version if versioning is on (`aws s3api list-object-versions --bucket roracle --prefix <key>`, then `aws s3api copy-object` with `--copy-source <bucket>/<key>?versionId=<id>`).
- **Cluster loss.** Point `config.ROR_S3_ENDPOINT` (and `postgres.backup.endpointURL`, `elasticsearch.snapshot.endpoint`) at the DR site that holds the replicated buckets. Identities and bucket policies are not replicated: recreate the per-bucket users and re-run the bucket setup (object lock, retention, policy) there.
- **Ceph health.** `radosgw-admin bucket stats --bucket <name>`, `radosgw-admin sync status` (multisite) and `ceph health detail` from the Rook toolbox.
- **RGW TLS.** The RGW certificate is valid for 1 year (renewed 30 days before expiry); after each renewal restart RGW (`kubectl -n rook-ceph rollout restart deploy -l app=rook-ceph-rgw`; Rook does not necessarily reload a changed certificate Secret: verify with `openssl s_client`). The self-signed CA lives 10 years; after a CA renewal re-run `terraform apply` so the app namespace copy (`roracle-s3-ca`) is refreshed, then restart api and worker.
- **Pre-GA check.** The rehearsal in the [kind rehearsal guide](deployment-kind-rehearsal.md) proves barman-cloud and the ES S3 repository work against the locked buckets (Content-MD5, delete markers during retention cleanup).

## 8. Quarterly restore drill

- [ ] Restore Postgres PITR (section 5) to a time 1 h ago in a scratch namespace and release; compare row counts of `documents` and `jobs`.
- [ ] Restore the latest ES snapshot into a scratch ES; run a known query and compare hit counts.
- [ ] Verify object-store replication (`radosgw-admin sync status` for Ceph multisite, or the last `rclone` job result) and open a replicated object.
- [ ] Point a scratch api and worker at the restored stores; smoke test upload, search, chat and a citation.
- [ ] Record elapsed time against the RTO and the WAL archive age against the 5 min RPO; file gaps.

Related: [upgrades](upgrades.md) (take a backup first), [incident runbook](runbook-incidents.md).
