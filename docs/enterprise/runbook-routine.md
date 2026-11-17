# Runbook: routine procedures

Audience: on-call operators. Part of the [operations runbook](operations-runbook.md). Commands assume namespace `roracle`, Helm release `roracle`, CNPG cluster `roracle-pg`, ECK cluster `roracle` (service `roracle-es-http`). Adjust to your names. Job kinds and retry rules: [jobs reference](../reference/jobs.md). Backups, restores and the drill: [backup and DR](backup-dr.md). Upgrades: [upgrades](upgrades.md).

## Scale workers for a backlog

```bash
kubectl -n roracle scale deploy/roracle-worker --replicas=8
```

Workers claim jobs with `FOR UPDATE SKIP LOCKED`; adding replicas is always safe.

## Retry failed jobs

```sql
UPDATE jobs SET status='queued', attempts=0, run_after=now(), last_error=NULL
WHERE status='failed' AND kind='ingest' AND updated_at > now() - interval '1 day';
```

The API does the same and also gives an ingest job a fresh per-version crash budget (`document_versions.ingest_attempts`, five starts): `POST /api/admin/jobs/{job_id}/retry` for one job, `POST /api/admin/jobs/retry-failed` with `{"kind": "ingest"}` for all of one kind (up to 1000 per call). Prefer them over SQL for `ingest`; a raw `UPDATE` leaves `ingest_attempts` as it was, so a version that crashed five times fails again at once. Retrying does not reset `released`. Kinds, payloads and retry rules: [jobs reference](../reference/jobs.md).

## Evidence scans (`evidence_scan` job)

Each evidence upload queues a `jobs` row `kind='evidence_scan'`; the worker streams the blob to ClamAV and
sets `evidence.scan_status`: `pending` (queued), `clean`, `infected` (download blocked), `failed`
(blob missing or scanner rejected it, e.g. over `StreamMaxLength`) or `unscanned` (no `ROR_CLAMAV_HOST`).
Scanner outages retry like ingest jobs. Re-run a failed or stuck scan:

```sql
UPDATE jobs SET status='queued', attempts=0, run_after=now(), last_error=NULL
WHERE kind='evidence_scan' AND status='failed';
```

For `failed` rows caused by size, raise the ClamAV limits (`parsers.clamav.limits`, [deployment guide](deployment-guide.md)) and re-queue.

## Change the embedding model (zero downtime)

Indices are `chunks-<model-slug>-<gen>`. Read alias `chunks` points at exactly one (the active) index; mutation
alias `chunks-all` points at every live generation, and every ACL/tag/delete/`is_latest` write goes through it, so
changes made during a re-embed reach both generations. Search and ingest use the active model persisted by cutover
(`app_settings` keys `embed_model`, `embed_dims`, `embed_index`); `ROR_EMBED_MODEL/_DIMS` is only the bootstrap default.

1. Make sure the new model is served by the embedding endpoint and ES has room for a second copy of the vectors.
2. `POST /api/admin/reindex {"model": "...", "dims": N}` -> `202 {job_id}` (409 if one is running). Same model is allowed
   (builds the next generation).
3. Follow `GET /api/admin/reindex` (`running.phase`: `copy` -> `cutover` -> `catchup`, `copied`/`total`). Search keeps
   using the old index until cutover; ingest during the copy keeps writing to the old index.
4. Cutover: slow cleanup first, then (under an advisory lock) persist the active model and do one atomic alias swap as the last ES step. `ensure_index` (ingest, worker start) moves `chunks` only to the Postgres-active index, so a stale worker never pulls it back; catch-up fails loudly if `chunks` drifts. It also nulls `document_versions.summary_embedding`
   when the model or dims changed (derived cache; re-created on re-ingest; near-duplicate summary matching is skipped
   for those versions meanwhile) and queues `reconcile_tags` (ACL/tags of every live document re-pushed from Postgres).
5. Two catch-up passes (300 s apart) copy chunks that reached the old index meanwhile. Ingests that straddle the
   cutover fail once and are retried with the new model (ingest always runs on the persisted active model/index).
   Known, accepted: summary vectors are cleared just before the cutover commit, so an old-settings ingest landing in
   that window can persist one stale same-dims summary vector; it only affects near-duplicate matching until that
   document is re-ingested.
   Copies are made safe against concurrent change: ACL, tags and title of every copied chunk are re-read from
   Postgres after the ES read (never copied from the old index); chunks of deleted documents, purged versions or
   shrunk re-ingests are skipped or removed from the new index after the write; `is_latest` is recomputed from
   Postgres. `reconcile_tags` is queued at the end of the copy, at cutover and after each catch-up pass.
   Known, accepted: readers that go through the `chunks` read alias (MCP, note anchoring, passage read) do not see
   chunks ingested during the copy until the first catch-up pass.
6. After verifying search, free the space: `DELETE /api/admin/reindex/old` (204; 409 while running, or 409 when the
   `chunks` alias does not point at exactly the active index). Until then the old generation stays in `chunks-all`
   and keeps receiving mutations. It drops every generation except the active one (per Postgres).
Failure: a failed `reembed` job (see `GET /api/admin/jobs?kind=reembed`) can be retried; copy is idempotent. To abandon,
`DELETE /api/admin/reindex/old` drops the half-built generation while the alias still matches the active index. If a
cutover failed permanently after the alias swap (`GET /api/admin/reindex` shows `old` containing the index that `chunks`
points at), the delete is refused with 409: re-run the failed job (idempotent) or point `chunks` back at the active
index before abandoning.
Upgrade note: the worker creates `chunks-all` and adopts a legacy concrete index named `chunks` (reindex +
atomic alias swap) at start. Roll out workers first; until a worker has started, ACL/tag updates find no `chunks-all`
and are no-ops, healed by the next `reconcile` maintenance run (run it after the upgrade).
Capacity: plan for double vector storage during the re-embed.
Library search upgrade (0.3.0): one same-model reindex writes the new search fields; until its cutover the library
runs the previous search (`upgrading: true`) and new search parameters answer 503. Steps: [upgrades, section 6](upgrades.md#6-02x-to-030-library-search-reindex-required).

## Re-embed after embedding prompt/model change (pre-production only)

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

## Delete or transfer a document

`DELETE /api/documents/{doc_id}` (owner or admin, `204`) soft-deletes: PG hides it at once, ES chunks are removed best effort
(the retention purge below finishes the job), an `acl_sync` is queued and the owner is notified when an admin deletes.
`POST /api/documents/{doc_id}/transfer {"user_id": ...}` (owner or admin) changes the owner and re-syncs ES ACLs; both are
audited (`document.delete`, `document.transfer`). Drafts use `/api/drafts` instead (`409`).

## Maintenance: retention purge, blob GC, tag reconcile (`maintenance` job)

A self-rescheduling `maintenance` job (every `ROR_MAINTENANCE_INTERVAL_S`, default daily; created at worker start)
runs four steps (purge, job pruning, gc, reconcile); trigger one on demand with `POST /api/admin/maintenance/{purge|gc|reconcile}` (admin JWT, audited,
`202 {job_id}`; follow it in `GET /api/admin/jobs?kind=maintenance`).

- **purge**: hard-deletes documents soft-deleted more than `ROR_RETENTION_DELETED_DAYS` days ago (ES chunks by
  `doc_id`, versions, notes/evidence rows including relation notes from other documents targeting it, shares) in batches of 100 (a full batch queues a follow-up job).
  `0` (default) = off: the endpoint then does nothing. Also deletes the version's `derived/<version_id>/` objects
  (docling JSON, rendition). Irreversible; restore from backup only. On a versioned bucket, noncurrent versions
  of purged `blobs/` and `derived/` objects remain until a noncurrent-expiry rule removes them (the chart
  sets one only for `tmp/`; add a `NoncurrentVersionExpiration` rule for `blobs/` and `derived/` with `aws s3api put-bucket-lifecycle-configuration` for real erasure). A document
  whose purge fails (e.g. ES error) is logged and skipped; the rest of the batch proceeds.
  If the periodic job ever ends `failed`, restart a worker to recreate it.
- **prune_jobs** (periodic only, no manual endpoint): deletes `done` jobs older than 7 days and `failed` jobs older than 30 days from `jobs`, up to 10,000 per run (a full batch queues a follow-up). `queued` and `running` rows are never touched. Failed jobs therefore disappear from `GET /api/admin/jobs` after 30 days.
- **gc**: deletes `blobs/` objects and `blobs` rows that no document version or evidence row references and
  that are older than 1 day. Uploads lock the `blobs` row before copying the object and GC skips
  locked rows (`SKIP LOCKED`), re-checks references under the lock and spares objects written in the last
  day, so an upload of the same content is not left pointing at a deleted object. `tmp/` is never touched
  (in-flight uploads; see "Clean up `tmp/`"). A blob shared with a live version is always kept.
- **reconcile**: walks the live documents in batches of 500 (`reconcile_tags` job chain), compares each document's Postgres
  `acl_hash` (ACL, title, tags) with the hashes in ES and queues an `acl_sync` ONLY for documents that differ (deduplicated
  against already queued ones); an in-sync corpus costs no ES writes. Use after an ES restore or suspected tag drift.

## Rebuild the search index from scratch

Elastic holds no unique data. `POST /api/admin/reindex {"model": "<current model>", "dims": N}` (admin JWT, audited, `202 {job_id}`;
`GET /api/admin/reindex` shows progress) rebuilds into the next index generation; with the same model it is a plain rebuild.
Search stays on the old generation until the cutover swaps the read alias.

## Clean up `tmp/` upload leftovers

API uploads stage in the blob bucket under `tmp/<uuid>` and are deleted after the copy to `blobs/`. A crash
mid-upload leaves an object or an incomplete multipart upload behind. The API ensures the bucket lifecycle rule `roracle-tmp-expire` (`tmp/`, 1 day, incl. stale multipart uploads) at startup
(idempotent; on `AccessDenied` it only logs a warning and the operator sets it). The rule
removes objects; check it with `aws s3api get-bucket-lifecycle-configuration --bucket <bucket> --endpoint-url <s3>` and add it if missing (`aws s3api put-bucket-lifecycle-configuration`: a rule with `Filter.Prefix=tmp/` and `Expiration.Days=1`, plus `AbortIncompleteMultipartUpload.DaysAfterInitiation=1`; Terraform rook-ceph sets both on the app bucket, external S3 is an operator duty). Stale incomplete multipart uploads are only removed by that abort rule (not automatically on RGW or AWS). Manual: `aws s3api list-multipart-uploads --bucket <bucket> --prefix tmp/` and `aws s3api abort-multipart-upload`. Never touch `blobs/`.

## Rotate secrets

- DB / S3 / Elastic credentials: update secret → `kubectl rollout restart deploy/roracle-api deploy/roracle-worker`.
- Keycloak signing keys: rotate in Keycloak; rOracle refetches JWKS on unknown `kid` automatically.
- PATs have no pepper or signing key (plain SHA-256 of the token): there is no secret to rotate. To invalidate tokens, users revoke their own tokens, an admin can revoke one user's tokens (`DELETE /api/admin/users/{id}/pats`) or turn PATs off for everyone (`ROR_PATS_ENABLED`, runtime setting `pats_enabled`).

## Sensitivity labels

Background: [security, section 13](security.md#13-sensitivity-labels).

- **Admin.** Web app, Admin, **Labels** (API `GET/POST /api/admin/labels`, `PATCH/DELETE /api/admin/labels/{id}`,
  `PUT /api/admin/labels/order {ids}` with every label id, least sensitive first). Max 20 labels; a gate must
  be on for every label ranked above one that has it (422 otherwise). Exactly one default (setting a new
  one unsets the old one). A label in use or the default cannot be deleted (409). Reordering or editing
  gates needs no reindex and takes effect on the next request. Each admin change is audited
  (`label.create|update|delete|reorder`).
- **Rename the seeded `General`** label rather than deleting it; it is the default and holds every
  pre-existing document.
- **Connector default.** A connector may name a label; otherwise its documents get the global default.
- **Export-blocked and inline-view audit queries** (`audit_log.detail` is JSONB):

```sql
-- blocked download attempts, last 7 days
SELECT at, user_id, target AS target_id, detail->>'route' AS route
FROM audit_log WHERE action = 'label.export_blocked' AND at > now() - interval '7 days' ORDER BY at DESC;
-- inline reads of block_export documents (one row per user, document and UTC day)
SELECT at, user_id, target AS document_id, detail->>'route' AS route
FROM audit_log WHERE action = 'label.view' ORDER BY at DESC LIMIT 200;
-- label changes for one document, then for its notes (annotation.label targets the note id)
SELECT at, user_id, detail FROM audit_log WHERE action = 'document.label' AND target = '<doc id>' ORDER BY at;
SELECT at, user_id, detail FROM audit_log WHERE action = 'annotation.label'
  AND target IN (SELECT id::text FROM annotations WHERE document_id = '<doc id>') ORDER BY at;
```

  `target_id` is the document id, except `chat.export` rows where it is the chat id.

  `label.*` events are reachable via `GET /api/admin/audit?action=label.` (a prefix match); `document.label` and
  `annotation.label` via `action=document.label` and `action=annotation.label`. For `document.label`, `from`/`to`
  are label ids.

- **Elasticsearch drift.** After a label change `acl_sync` converges the index; a stuck job shows as a
  failed `acl_sync` in `GET /api/admin/jobs?status=failed&kind=acl_sync` (retry with `retry-failed`). Until it
  converges the Postgres post-filter enforces the new label.

## Evaluate retrieval quality (eval CLI, reference set, trend)

Everything here is admin-only and reads private data through the production path per principal (see [evaluation](../eval/README.md) and [admin evaluation](../admin/eval.md)).

- **CLI** (a one-off pod/Job with the app env; counts against `postgres.reservedConnections`): `roracle eval run --set <name> [--path chat|search] [--judge]`
  scores a stored set; `roracle eval compare --set <name> --baseline "k=v" --candidate "k=v" [--scope all|fact|multihop|global] --out docs/eval/<file>.md`
  writes the gate report (PASS / FAIL / INSUFFICIENT); `roracle eval import-beir <dir> --set scifact --owner <non-admin user>` seeds the offline
  SciFact baseline; `roracle eval ci` is the CI fixture job. Exit code 1/2 and a message name refused input (a set with only admin principals).
- **UI/API**: Admin -> Eval (`/api/admin/eval/*`): create sets, add or import queries, generate draft questions from the corpus
  (`POST /api/admin/eval/sets/{id}/generate` -> `eval_generate` job; an admin reviews the drafts), run a set (`POST /api/admin/eval/runs` -> `eval_run`
  job on the worker), read the trend (`GET /api/admin/eval/runs?set_id=`). One set is the **reference set** (`PATCH /api/admin/eval/sets/{id} {"is_reference": true}`).
  Curate it to >= 200 active queries with >= 3 principals (>= 1 non-admin): below that every gate verdict is INSUFFICIENT.
- **Automatic runs**: when the final catch-up round of a re-embed finishes (no fixed delay) and after a rebuild into the active index an `eval_run` is queued on the
  reference set (`ROR_EVAL_ON_REINDEX=true`); results land in the trend. A run with any ACL violation is `failed`: treat it as a
  security finding.
- **Reading the trend**: compare `p_ndcg@10`, `p_recall@10/100` and the judge keys (`faithfulness`, `citation_support`) between runs of the same set;
  a drop after a model change or rebuild is the signal to roll back (re-embed again with the previous model). Latency p50/p95 per run is stored too.
- **Reranker**: `roracle_rerank_fallback_total{reason}` counts requests that kept the fused order (`reason` = `timeout`, `http` or `malformed`); the reranker is off until its
  gate passes, so a non-zero counter with `ROR_RERANK_URL` empty is a bug.

## Reference identifiers (reparse)

Citations link by DOI, arXiv, PMID, PMCID, ACL Anthology and OpenReview ids as well as title+year (ISBN, ISSN and eISSN are stored and shown but never matched). Documents ingested before this feature lack `meta.ids`. Run `roracle reparse-refs [--limit N]` (a one-off pod or Job with the app env) to enqueue one bulk-priority `reparse_refs` job per latest searchable PDF version that has no `ids` and no queued or running reparse job (idempotent; re-run after a partial pass; `--limit 0` is all).

- **`ROR_GROBID_URL` must be set where the CLI runs.** Without it the command prints `error: GROBID is not configured (ROR_GROBID_URL)` and exits with status 2, and nothing is enqueued. The chart fills it for the api and worker when `parsers.grobid.enabled` is true; a one-off Job needs the same ConfigMap.
- Each job re-runs GROBID (`ROR_GROBID_TIMEOUT_S`), updates only `ids`/`doi` and `references`, then relinks both directions. A job on a worker without `ROR_GROBID_URL`, or on a non-PDF, deleted or not-searchable version, finishes without doing anything. A GROBID answer with no parsed ids changes nothing, so the version stays eligible for another run, and an empty reference list never wipes existing references.
- Migration 0019 recreates the claim indexes for the new bulk kind: drain workers first (as 0014b; [upgrades](upgrades.md#4-alembic-migrations)).

## Rebuild derived index fields and shadow indices

Context notes and RAPTOR units are index-time. `roracle rebuild --what context[,raptor] [--into chunks-<slug>-shadow]` (or
`POST /api/admin/retrieval/rebuild {"what": [...], "into": "..."}`) enqueues `rebuild_units` jobs of 25 latest searchable, non-deleted versions each, plus a
`rebuild_watch` job that queues the eval run when the batch is done (not for a shadow). `rebuild_units` and `reconcile_tags` are claimed only when no user job is due, so
a backfill never delays ingest. It is refused (exit 2 / `409`) while a re-embed runs, and a re-embed is refused while rebuild jobs are pending.

- **Shadow workflow** (judge a feature before it touches search): `roracle rebuild --what context --into chunks-<model-slug>-shadow` creates the shadow index
  (it joins `chunks-all`, so ACL syncs and deletes reach it, but never the read alias `chunks`); `roracle eval compare ... --candidate-index
  chunks-<model-slug>-shadow` judges it; on a PASS set `ROR_CONTEXT_NOTES=true` (and `ROR_CONTEXT_MODEL`) and rebuild into the ACTIVE index (omit
  `--into`); finally `roracle rebuild --drop chunks-<model-slug>-shadow` (refuses the active index, the read-alias holder, re-embed generations and any moment a rebuild or re-embed job is queued or running).
- Requires the notes model for `context` (`ROR_CONTEXT_MODEL`; refused up front, exit 2 / 422, and a job that still sees it unset fails). Each version is idempotent; a broken version is skipped and the
  job fails at the end naming the count, the rest are done. Progress: `SELECT status, count(*) FROM jobs WHERE kind='rebuild_units' GROUP BY 1;`.

## Re-embed at scale (slices, failure, convergence)

`POST /api/admin/reindex` now enqueues `ROR_REEMBED_SLICES` (default 4) independent copy chains over disjoint `chunk_id` ranges; each page is 1,000 chunks;
`ROR_EMBED_CONCURRENCY` embedding requests are in flight per slice (size `slices x concurrency` to the embedding replicas; batch embedding calls retry
429/5xx with backoff and a shared pause). The target index is created with a 30 s refresh interval and the configured replicas.

- `GET /api/admin/reindex`: `running` = `{target, copied, total, phase, slices}` (progress is the sum over slices; `phase` of the newest slice job),
  **`failed`** = `{target, slices: [{i, error}]}` when a slice failed permanently (and nothing is running), `old` = leftover generations.
- **A failed slice refuses the cutover** (the read alias never moves onto an incomplete index; the last finishing slice and the cutover job both check).
  Fix the cause (embedding endpoint, ES), then `DELETE /api/admin/reindex/old` to drop the half-built generation and start again with `POST /api/admin/reindex`.
- **Cutover** order: restore the target's refresh interval and replicas, refresh it, then a resumable convergence phase against Postgres (`converge` jobs of ~60 s each, checkpointed by stage and id) (ACL/tag/title drift
  of every live document through `acl_hash`; chunks of every soft-deleted document removed; superseded versions demoted; orphan documents
  purged), then the alias swap, then `reconcile_tags` and two catch-up rounds per slice (300 s apart). The pass reads Postgres for every live document:
  expect minutes per million documents before the swap, with no ES writes when everything is in sync.

## Router, global answers and agentic retrieval (when enabled)

Off by default (`ROR_ROUTER`, `ROR_AGENTIC_RETRIEVAL`, admin runtime overrides). When on: busy admission falls back to the plain path (SSE `meta` says
`global: {skipped: busy}` or `agentic: false`), so a burst degrades answer quality, not availability. An agentic loop holds up to `ROR_AGENTIC_MAX_PARALLEL` extra
pooled DB connections per API process; watch pool waits (`ROR_DB_POOL_TIMEOUT_S`) before raising it. The planner needs a small fast model
(`ROR_AGENTIC_PLANNER_MODEL`); with a slow one calls hit `ROR_AGENTIC_CALL_TIMEOUT_S` (1.5 s) and the loop silently stops.

## Release upgrade and rollback

The full procedure, the chart 0.1 to 0.2.0 break, the Elasticsearch 8 to 9 hop and the migration risk table (0011 and 0012 backfills, 0014 concurrent index builds, 0019, the 0.3.0 search reindex) are in [upgrades](upgrades.md). Before a minor or major upgrade take a base backup (`kubectl cnpg backup <fullname>-pg --method plugin --plugin-name barman-cloud.cloudnative-pg.io`) and check the latest ES snapshot ([backup and DR](backup-dr.md)). `helm rollback` restores manifests but does not revert database migrations; if the previous release cannot run, restore Postgres to just before the upgrade (PITR, [backup and DR](backup-dr.md#5-postgres-point-in-time-recovery-in-place-helm-managed-name)).

## Other routine items

- After an ECK CA rotation restart api and worker.
- ClamAV signatures: freshclam in the pod pulls from `parsers.clamav.mirrorUrl`. With no mirror configured the signatures are only those baked into the image, so rebuild the image regularly ([mirror manifest](airgap-mirror-manifest.md)).
- Embedding-model changes need a re-embed ([ADR-0006](../adr/0006-embedding-model-config.md); the automated flow is `POST /api/admin/reindex`).
