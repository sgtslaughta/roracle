# Security: data retention and deletion

Part of the [security architecture](../security.md). Storage layout and the job model are in
[architecture: storage](../../architecture/storage.md) and [jobs and queue](../../architecture/jobs-queue.md); backup
retention is in [backup and DR](../backup-dr.md). Settings: [settings reference](../../reference/settings.md).

## What is kept, and for how long

| Data | Lifetime | Mechanism |
|---|---|---|
| Soft-deleted documents | Forever by default; purged after `ROR_RETENTION_DELETED_DAYS` days when that is above 0 | Maintenance `purge` step; default `0` = off |
| Blob objects (`blobs/<sha[:2]>/<sha>`) | Until no version or evidence file references them and the object is a day old | Maintenance `gc` step |
| Derived objects (`derived/<version id>/...`: Docling JSON, rendition, notes, units) | Until their document is purged | Deleted by the purge |
| Upload leftovers (`tmp/<uuid>`, incomplete multipart uploads) | 1 day | Bucket lifecycle rule `roracle-tmp-expire` |
| Finished jobs | `done` 7 days, `failed` 30 days; `queued` and `running` never touched | Maintenance `prune_jobs` step |
| Handoffs | Expire after 1 to 90 days (default 7); the row is **not** deleted when it expires | Checked at load: `410` after expiry or revocation |
| Audit log | **Forever**: no retention and no deletion code | See [below](#audit-log) |
| Postgres and ES backups | Per backup policy (chart defaults: Postgres 30 d `retentionPolicy`; ES snapshots `expireAfter` 30d, min 7, max 60) | [Backup and DR](../backup-dr.md) |

The maintenance job runs every `ROR_MAINTENANCE_INTERVAL_S` (default 86400 seconds) and can be started on demand with
`POST /api/admin/maintenance/{purge|gc|reconcile}` (admin JWT, audited as `admin.maintenance_<op>`).

## Deleting a document

1. **Soft delete** (`DELETE /api/documents/{id}`, owner or admin; drafts use the drafts endpoints). Postgres sets
   `deleted_at`, so the document disappears from every Postgres-backed read at once (R19). An `acl_sync` job is
   queued and the chunks and summary units are removed from Elasticsearch immediately (`delete_by_query` on the
   document id). If that ES call fails it is logged; the purge removes them later **only when purge is enabled** (`ROR_RETENTION_DELETED_DAYS` > 0, default off), and `acl_sync` skips deleted documents, so otherwise the chunks stay in ES ([known limits](known-limits.md#retrieval-and-label-timing)). The action is audited as
   `document.delete`.
2. **Quota.** The per-user upload quota (`ROR_UPLOAD_QUOTA_BYTES`) still counts a soft-deleted document's version
   sizes until it is purged ([known limits](known-limits.md#quota-and-observability)).
3. **Purge** (only when `ROR_RETENTION_DELETED_DAYS` > 0): up to 100 documents per run whose `deleted_at` is older
   than the cutoff. Per document, in one savepoint (a failing document is skipped and retried next run):
   - **Elasticsearch:** delete every chunk and summary unit of the document (units are ES documents with the same
     `doc_id`). A missing index is tolerated only when no index or alias exists at all.
   - **Blob refcounts:** `blobs.refcount` is decremented once per version and per evidence file of the document or its notes.
   - **Postgres:** near-duplicate links to its versions are cleared, shares, versions and the document row are
     deleted; notes, tags, votes and citations cascade with the document.
   - **Object store:** every `derived/<version id>/` prefix is deleted. Blob objects are **not** deleted here.
4. **Blob GC** later removes a `blobs` row and its object when nothing references it (no version, no evidence),
   the row is older than a day and the object has not been written for a day. The reference check is repeated under a
   row lock right before the S3 delete, so a blob that an upload is re-referencing is skipped. Up to 500 per run.
5. **Objects on a versioned bucket:** deleting leaves noncurrent versions until a noncurrent-expiry rule removes them (see the
   [runbook](../runbook-routine.md)). Backups age out per the backup policy, so a purged document can still exist in a backup.

Chats and handoffs keep their stored citations as JSON. They are re-checked per reader on every read, so a
purged or hidden source shows as "source not available" rather than text. Embeddings are derived data and leave with the chunks;
summaries written by an LLM before a label was raised are not deleted by relabelling.

## Uploads and `tmp/`

`POST /api/documents` and evidence uploads are streamed from the request body into object storage
(S3 multipart, 8 MiB parts, SHA-256 computed on the fly) under `tmp/<uuid>`; nothing is spooled to the pod's disk.
The size cap is enforced while streaming (413, upload aborted); after authorisation, quota and duplicate checks the
object is server-side copied to `blobs/<sha[:2]>/<sha>` (skipped when that content already exists) and `tmp/` is
deleted. Any error aborts the multipart upload.

Orphans from a crashed pod are expired by the bucket lifecycle rule `roracle-tmp-expire` on `tmp/` (1 day) and by
aborting incomplete multipart uploads after 1 day. The API ensures that rule at startup (idempotent; on
`AccessDenied` it only logs a warning). Terraform `object_store=rook-ceph` sets it on the app bucket (OBC lifecycle); on
external S3 it is an operator duty (`aws s3api put-bucket-lifecycle-configuration`, see the Terraform guide,
"External S3 commands"). A blob object written before the database commit can orphan on failure; blob GC removes it
once it is a day old and unreferenced. Blob GC never lists or touches `tmp/`.

## Handoffs

A handoff's contents are **frozen at creation** (there is no live mode): transcript, passages, document ids,
brief and goal do not change when the source chat, collection or documents change. The creator chooses an expiry of 1 to
90 days (default 7) and can change it or revoke the handoff. After expiry or revocation a load answers `410`, but the
row and its frozen content stay in Postgres: the application never deletes handoffs. They are removed only when
their creator's user row is deleted (`ON DELETE CASCADE`). Each load re-evaluates the audience, expiry, document
visibility and label gates for that loader ([access control](access-control.md#3-authorization)).

## Temporary files outside the object store

Parsing happens in sidecar pods whose only writable paths are sized `emptyDir` volumes
(`parsers.*.writableDirs`), cleared when the pod is replaced. API and worker pods have a read-only root filesystem
with `emptyDir` for writable paths. The draft PDF build uses a fresh temporary directory per build.

## Audit log

The `audit_log` table has no retention: nothing deletes rows, and no maintenance step touches it, so it grows for as
long as the database exists. If a user row is removed, their audit rows stay with the user cleared. A site that
needs a limit must archive and trim rows itself, outside rOracle, and keep what its policy requires: the log is
the only record of label changes and token revocations. See [audit](audit.md#what-the-log-guarantees).
