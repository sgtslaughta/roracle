# Upgrades

Audience: operators and SRE. How to upgrade rOracle (Helm chart `deploy/helm/roracle`), the special cases (chart 0.1 to 0.2.0, Elasticsearch 8.x to 9.x, the 0.3.0 library search reindex), the database migrations with the risky ones flagged, and the re-embed after an embedding model change. Back to the [enterprise index](README.md); terms are in the [glossary](../glossary.md). Install is in the [deployment guide](deployment-guide.md); backups and restore are in the [operations runbook](operations-runbook.md).

Versioning is SemVer. `VERSION`, chart `version` and `appVersion` are the same number (`scripts/check-version.sh` enforces it in CI; for example chart 0.3.0 ships appVersion 0.3.0), and an empty image tag means `appVersion`. A patch release is drop-in, a minor release adds migrations and rolls without downtime, a major release comes with release notes and manual steps.

## 1. General order

1. **Read the release notes** and the migration list in section 4 for every revision between your version and the target. Note any "drain workers first" revision.
2. **Back up.** Check a recent CNPG backup (`kubectl get backup`) and an ES snapshot (`GET _snapshot/roracle-s3/_all`); trigger a base backup before a minor or major upgrade ([runbook](operations-runbook.md), section 2).
3. **Mirror** the new backend and web images (and operator charts, if they change) into the internal registry ([mirror manifest](airgap-mirror-manifest.md)). Set `image.backend` and `image.web` tags or digests (or leave the tag empty for `appVersion`). Run `helm diff` or `helm template` to read what changes.
4. **Drain the workers** when a migration in the range is marked "drain" below (and as a habit for any release that rebuilds job indexes). Scale the worker Deployment to zero: `kubectl -n roracle scale deploy/roracle-worker --replicas=0`. On SIGTERM a worker takes no new job; a running job gets `ROR_JOB_DRAIN_S` seconds (chart: 120) and is then released to the queue without spending an attempt. Ingest, rebuild pages and the re-embed copy resume where they stopped. A judged evaluation run and a single long model batch restart, so do not drain while an `eval_run` is running. Wait until the worker pods are gone (grace period 150 s).
5. **Migrate.** `helm upgrade roracle deploy/helm/roracle -n roracle -f my-values.yaml --timeout 30m` runs the `migrate` hook Job (`python -m roracle migrate --wait 600`, `backoffLimit: 0`) with the new image before the new api and worker roll. With external Postgres the hook is `pre-install,pre-upgrade`; with bundled CNPG it is `post-install,pre-upgrade`, and `post-install,post-upgrade` while `postgres.recovery.enabled` is on. A failed migration blocks the release and leaves the old pods running. Keep `--timeout` and `migrate.activeDeadlineSeconds` (1500 s) above the longest migration.
6. **Roll api and worker.** The chart's workers wait for the schema before they start claiming (bounded to 25 minutes), so they can be rolled in the same `helm upgrade`. If you scaled the workers to zero by hand, check `kubectl -n roracle get deploy roracle-worker` afterwards and scale back to your replica count if the upgrade did not.
7. **Resume and verify.** `GET /api/healthz` through the public address (or `/healthz` on the API pod) reports the new version; run the smoke test (upload a PDF until it is searchable, ask a question, open a citation). Watch the queue drain (`roracle_jobs` by kind and `roracle_jobs_oldest_queued_seconds`, see the [runbook](operations-runbook.md)); retry failures with `POST /api/admin/jobs/retry-failed`.

Do not run old and new workers side by side for longer than the drain after the 0016 migration: an old worker's completion is not fenced by the new claim token.

**Rollback.** `helm rollback` restores manifests but does not revert the database. Migrations are additive within a minor line, so the previous release normally still runs; otherwise restore Postgres by point-in-time recovery to just before the upgrade ([runbook](operations-runbook.md), section 3). Revision 0012 downgrades by dropping all labels, so never run `alembic downgrade` on a live system.

With Terraform: [Terraform deployment](deployment-terraform.md) (operators first with `deploy_app=false`, then the app).

## 2. Chart 0.1 to 0.2.0 (breaking)

Chart 0.2.0 removes the bundled MinIO tenant. Object storage is external only: Rook-Ceph RGW (Terraform `object_store = "rook-ceph"`) or your own S3. `helm upgrade` (or `terraform apply`) deletes the `Tenant` custom resource, so the MinIO pods go away (the PVCs stay, per the operator default).

1. **Provision the new store** and its buckets: `roracle`, `roracle-pg-backup`, `roracle-es-snapshots`, with the lifecycle, object lock and policies ([Terraform deployment](deployment-terraform.md), external S3 commands; Terraform does it for rook-ceph).
2. **Copy the data out first**: `rclone sync` or `aws s3 sync` of the three buckets to the new store, then verify object counts and sizes. Take a Postgres backup and an ES snapshot beforehand.
3. **Migrate the values.** The chart fails the render when a removed value is still set (`templates/validate.yaml`):

| Old | New |
|---|---|
| `minio.*` (mode, image, users, configurationSecret, policyJob, backupRetentionDays, ...) | removed (any `minio` key fails the render). Set `config.ROR_S3_ENDPOINT`, `postgres.backup.endpointURL` and `elasticsearch.snapshot.endpoint` (plus `elasticsearch.snapshot.protocol` and `extraCASecret`) |
| backup and snapshot Secrets = MinIO users | dedicated identities per bucket that you (or Terraform rook-ceph) create; the app identity must not be able to delete backups |
| `networkPolicy.s3Cidrs` | `networkPolicy.s3.cidrs` (plus `ports`, `namespaceSelector`, `podSelector`); an unmigrated value would silently drop S3 egress, so the render fails |
| `caBundle.kubeRootCa` | removed (render fails); add any CA you still need, including the S3 CA, through `caBundle.extraSecrets` |
| `networkPolicy.operatorNamespaces.minio` | removed |
| `minio-policy` hook (users, policies, `tmp/` lifecycle, backup retention) | gone: create the lifecycle, object lock and bucket policies yourself |
| optional | `secrets.s3Secret`: a separate Secret for `ROR_S3_ACCESS_KEY` and `ROR_S3_SECRET_KEY` |

4. **Point the app at the new store**, upgrade, and run the first backup (`kubectl get backup`; the immediate backup can run before the bucket exists).
5. After the checks, remove the old tenant's PVCs and the operator.

The 0.2.0 release also moves the Elasticsearch default to 9.5.6 and adds migrations up to 0019 (section 4). Its backend ships the elasticsearch-py 9.x client, which **cannot talk to an 8.x cluster**: on an existing 8.x cluster finish both Elasticsearch hops (section 3) **on your current chart first** (the old 8.x client keeps working against 9.x through REST compatibility), then upgrade to chart 0.2.0.

## 3. Elasticsearch 8.15 to 8.19 to 9.5.6

The chart default is `elasticsearch.version: 9.5.6` with ECK; external Elasticsearch must be 9.x (the client sends `compatible-with=9`, and `hnsw_filter_heuristic` and `exclude_source_vectors` are 9.x settings). For an existing 8.x cluster:

1. **Never jump straight.** A direct 8.15 to 9 jump is refused (by ECK's webhook or the node). Go 8.15 to 8.19.x first. Run both hops **before** the chart 0.2.0 upgrade (section 2), on the chart you run now: the 0.2.0 backend's 9.x client fails against 8.x, so pinning 8.x under 0.2.0 means a search outage.
2. **Hop 1, rolling.** Mirror the 8.19.x image first. Set `elasticsearch.version` to an 8.19.x release, let ECK finish the rolling restart, and run the Elasticsearch Upgrade Assistant (resolve what it reports). Snapshot before each hop.
3. **Hop 2, rolling.** Set `elasticsearch.version` to `9.5.6`. ECK must be a release whose support matrix lists 9.5 (the 3.x line); upgrade the operator before the cluster.
4. **After the hop** 9.x reads the indices created on 8.x unchanged. The new index settings (`exclude_source_vectors`, shard count, `index_options`) only apply to indices created afterwards. `hnsw_filter_heuristic` is dynamic and is re-applied to the active index at the next `ensure_index`.
5. **Re-index into a new generation** to adopt the new settings (including a different vector type such as `bbq_hnsw`): set `ROR_VECTOR_INDEX_TYPE` (and `ROR_VECTOR_RESCORE_OVERSAMPLE` for bbq), start a re-embed into the next generation (section 5; the same model is allowed), compare retrieval quality with the eval harness, and switch only if the gate passes. The switch is one atomic alias move of `chunks`; the old generation keeps receiving mutations until you drop it. `bbq_hnsw` is not the default (recall and latency trade-off in [capacity planning](capacity-planning.md)).
6. **Alternative:** a fresh 9.5.6 cluster plus a rebuild from Postgres and object storage (search is down until versions are searchable again); an 8.x data directory cannot be opened by 9 directly.

Plan for double vector storage during a re-embed. Details: [search index lifecycle](../architecture/search-index-lifecycle.md).

**ECK 3.x and older clusters.** ECK refuses a 9.x version on a cluster below 8.18.0; upgrade the cluster to ≥ 8.18 first or create a new one.

## 4. Alembic migrations

`roracle migrate` runs `alembic upgrade head` (the chart does it in the hook Job). The chain is linear, from `backend/migrations/versions`:

| Revision (file) | Purpose | Risk |
|---|---|---|
| 0001 `0001_foundation.py` | users, blobs, documents, document_versions, document_shares, jobs | none |
| 0002 `0002_ingest.py` | ingest state: version `stage`, summary, meta, references, MinHash, `is_latest`; user `groups` and `is_admin` | none |
| 0003 `0003_library.py` | votes, tags, collections, collection items | none |
| 0004 `0004_citations.py` | `doc_citations` | none |
| 0005 `0005_chats.py` | chats, messages, shares, answer ratings | none |
| 0006_p2b `0006_p2b_summary_embedding.py` | `document_versions.summary_embedding` | none; vectors indexed earlier stay usable, see section 5 |
| 0007 `0007_mcp_handoff.py` | personal access tokens, handoffs and shares, `audit_log`, `users.last_login_at` | none |
| 0008_p8 `0008_p8.py` | annotations (and shares, votes, reports), proposals, evidence | none |
| 0009 `0009_p10.py` | connectors, runs, items, notifications, `app_settings`, `documents.connector_id` | none |
| 0010 `0010_p9.py` | paper drafts, draft versions, writing profiles | none |
| 0011 `0011_coauthors.py` | `document_coauthors` | **data migration**: backfills co-authors from drafts of published papers, repairs `is_latest` (one per document, its highest searchable version) and queues one `acl_sync` job per backfilled document; idempotent |
| 0012 `0012_labels.py` | sensitivity labels, `label_id` on documents, annotations and connectors, `label` proposal kind | **data migration, locks**: one transaction; seeds the default label `General`, rewrites every `documents` row and sets `label_id` NOT NULL (exclusive lock on `documents` until commit, WAL and bloat grow with row count); queues an `acl_sync` job per live document. Run off-peak and raise `migrate.activeDeadlineSeconds` and `--timeout`. Until the jobs drain, Elasticsearch lacks `label_id` and the Postgres post-filter is the authority (search may over-fetch briefly, never leaks). Downgrade drops all labels |
| 0013 `0013_eval.py` | eval sets, queries, runs, results | none |
| 0014 `0014_perf_jobs.py` | jobs: split partial claim indexes (user and bulk), `(kind, status)` index; drops the old one | **drain workers**: `CREATE INDEX CONCURRENTLY` waits for every open transaction, and a claim, its handler and its completion shared one transaction. Re-runnable (drops an invalid leftover first, verifies validity) |
| 0014b `0014b_bulk_reconcile.py` | `reconcile_tags` becomes a bulk kind (claim indexes recreated with the wider predicate) | **drain workers**: concurrent index swap (build under a temporary name, validate, drop old, rename) |
| 0014c `0014c_perf_indexes.py` | hot-path indexes: browse, tag filter, shares lookup, org visibility, `label.view` audit | **drain workers**: concurrent index builds |
| 0014d `0014d_jobs_autovacuum.py` | aggressive autovacuum settings on `jobs` | none |
| 0014e `0014e_version_lsh.py` | `version_lsh` (near-duplicate LSH bands) | **backfill job**: enqueues one-off `lsh_backfill` for existing versions with a MinHash; ingest uses the exhaustive check until the `lsh_backfilled` marker is written by the last batch, so recall never drops. On a fresh install the marker is written at once |
| 0015 `0015_job_lease.py` | `jobs.lease_until` and index: lease-based execution | **drain workers first**: concurrent index build; an old worker holds its claim in an open transaction, a new one claims through the lease |
| 0016 `0016_job_claim_token.py` | `jobs.claim_token` and `jobs.released` (fencing token, refunds tracked separately) | **roll workers with the drain** (`ROR_JOB_DRAIN_S`): a job claimed by a pre-0016 worker has no token, the lease sweep still reclaims it, but that worker's completion is unfenced, so do not mix old and new workers beyond the drain. Re-runnable |
| 0017 `0017_jobs_due_reconcile.py` | partial index for the `reconcile_tags` claim | **drain workers first**: `CREATE INDEX CONCURRENTLY` waits for open transactions; re-runnable |
| 0018 `0018_chat_hidden_at.py` | `chats.hidden_at` (owner's soft remove) | none |
| 0019 `0019_reparse_refs_bulk.py` | `reparse_refs` becomes a bulk kind (claim indexes recreated) | **drain workers first**: same concurrent swap as 0014b |
| 0020 `0020_saved_searches.py` | `saved_searches` (owner-only saved library searches, unique name per owner) | none; 0.3.0 also needs the search reindex in section 6 |
| 0021 `0021_tag_source.py` | `tags.source`, `documents.rejected_tags`; `auto_tags` becomes a bulk kind (claim indexes recreated) | **drain workers first**: same concurrent swap as 0019 |
| 0022 `0022_onboarding.py` | `onboarding_progress` (Get started checklist) | none |
| 0023 `0023_pat_manage_scope.py` | adds the `manage` scope and backfills it onto every token that holds `write` | none; see section 7 |

Concurrent index migrations cannot run inside a transaction, so they use Alembic's autocommit block. If one is interrupted it can leave an invalid index; the migrations drop an invalid leftover and verify validity, so fixing the cause and re-running `roracle migrate` is safe. A migration that waits does so on long-running transactions: drain the workers (step 4 of section 1) and check `pg_stat_activity` for long transactions on the primary.

### After 0019: backfill identifiers (optional)

Migration 0019 prepares the `reparse_refs` job kind. To backfill identifiers (DOI and others, used for reference linking) on papers ingested before it, run once after the upgrade, with `ROR_GROBID_URL` set (GROBID bundled or external):

```sh
kubectl -n roracle exec deploy/roracle-worker -- roracle reparse-refs --limit 500
```

It enqueues a `reparse_refs` job for each latest searchable PDF version that has no identifiers and no queued or running reparse job (`--limit 0`, the default, means all). Without `ROR_GROBID_URL` it exits with status 2 and a message. The jobs re-run GROBID on the stored PDF, refresh `ids`, `doi` and `references` (user-edited title and authors are untouched) and relink citations in both directions. They are bulk jobs, so user-facing work claims first. Re-running is safe: versions already done are skipped.

## 5. Re-embed after an embedding model change

Search and ingest use the active model persisted in Postgres by the last cutover; `ROR_EMBED_MODEL` and `ROR_EMBED_DIMS` are only the bootstrap defaults. Changing the embedding model, its dimensions or the vector index type needs a new index generation:

1. Serve the new model on the embedding endpoint and make sure Elasticsearch has room for a second copy of the vectors.
2. `POST /api/admin/reindex` with `model` and `dims` (an admin; the same model is allowed and builds the next generation). It answers 409 while a re-embed or a rebuild is queued or running. Follow `GET /api/admin/reindex`.
3. The copy runs as `ROR_REEMBED_SLICES` parallel chains while readers stay on the old index; a convergence step makes the new index agree with Postgres on access, then one atomic alias swap moves `chunks`, followed by two catch-up passes 300 s apart. The cutover clears the derived summary-embedding cache when the model changed.
4. Check quality: an `eval_run` is queued after cutover when `ROR_EVAL_ON_REINDEX` is on and a reference set exists ([evaluation](../admin/eval.md)). A drop is the signal to re-embed again with the previous model.
5. Re-tune the score floors: `ROR_SEARCH_MIN_SIMILARITY` and `ROR_CHAT_MIN_SIMILARITY` are raw cosine floors and are model-specific.
6. Free the space with `DELETE /api/admin/reindex/old` once search is verified (409 while a re-embed or rebuild runs, or when `chunks` does not point at exactly the active index).

If a copy slice fails permanently the cutover is refused and the old index stays active; a new re-embed starts from scratch with a new generation number. Full mechanics: [search index lifecycle](../architecture/search-index-lifecycle.md); admin view: [index maintenance](../admin/index-maintenance.md). The pre-production alternative that drops the index and resets ingest stages (downtime) is in the [runbook](operations-runbook.md).

## 6. 0.2.x to 0.3.0: library search reindex (required)

0.3.0 adds advanced library search: include and exclude filters, match any or all, query syntax (phrases, `-word`, field scope), venue, journal and identifier filters, year, page, added-date, vote and citation ranges, owner and citing filters, more sorts and saved searches. Migration 0020 adds the `saved_searches` table. The new search reads fields that only exist in Elasticsearch after **one reindex with the current model**.

1. Upgrade as in section 1. The new fields are additive: `ensure_index` adds them to the mapping of the active index, and papers ingested from now on fill them at once.
2. Read the current model and dimensions: `GET /api/admin/reindex` (`active`).
3. Start a same-model generation: `POST /api/admin/reindex {"model": "<current model>", "dims": <current dims>}`. A same-model generation copies the stored vectors whose prompt is unchanged (same `prompt_sha`; only chunks whose title, summary or context note changed since indexing are embedded), writes the search fields from Postgres (venue, abstract, journal ISSNs in `issn`, `ids_present`, page count, added date, votes, citation upper bounds) and marks the new index with `_meta.search_fields`. Follow it with `GET /api/admin/reindex` as in section 5; plan for double vector storage.
4. At cutover the `chunks` alias moves to the new index. API processes check the marker at most every 30 seconds, so the new search turns on within 30 seconds of the swap, with no restart.
5. After verifying search, free the space: `DELETE /api/admin/reindex/old`.

Until the cutover:

- `GET /api/search` with only the parameters 0.2 knew (`q`, the include filters `tags`, `author`, `file_type`, `org_unit`, `collection`, `label`, and the sorts `relevance`, `newest`, `votes`, `title`) runs the previous search and adds `"upgrading": true` to the response. The web app then shows "Some filters will be available when the search index upgrade finishes." and disables the new controls.
- A request with any new parameter, and `GET /api/search/facets/{name}`, answer `503 {"detail": "search index upgrade in progress"}`.

Journal and has-identifier results are only correct for papers written by that reindex or ingested after it. Papers ingested before identifiers were extracted get them from `roracle reparse-refs` (section 4, "After 0019"); run it **before** the reindex, or run another same-model reindex after it, so their journals and identifiers reach the index. The same holds for any later `reparse-refs`: it refreshes citation counts in the index at once, but a paper's journal ISSNs (`issn`), identifier filters (`ids_present`, `has_doi`) and `has_refs` only change in the index after the next same-model reindex.

## 7. 0.3.x to 0.4.0: agent hardening

0.4.0 is the security-hardening release. There is no reindex. Upgrade as in section 1; three migrations run in the `migrate` hook: 0021 `tag_source` (auto-tags; recreates the claim indexes, so **drain workers first**, as for 0019), 0022 `onboarding` (the Get started checklist table) and 0023 `pat_manage_scope` (the `manage` backfill below).

- **Auto-tags.** 0021 adds `tags.source` and `documents.rejected_tags`; new papers are tagged at ingest. Tag papers already in the library with `roracle auto-tags` (see [index maintenance](../admin/index-maintenance.md)); it is optional and resumable.

- **`manage` scope.** Token scopes are now `read` ⊂ `write` ⊂ `manage`. `manage` gates sharing and ownership: changing a document's visibility, org unit, shares or co-authors, deleting or transferring it, submitting a draft, changing draft co-authors (`PUT /api/drafts/{id}/coauthors`), sharing a chat, and handoffs for anyone but the creator. Without it the API answers 403 `insufficient scope: manage`. Migration 0023 **backfills `manage` onto every existing token that holds `write`**, so nothing breaks on upgrade; tokens minted afterwards with Read and write do not have it. Review your agents' tokens and mint narrower ones where manage is not needed.
- **Upload default.** An upload (REST or MCP) by a token without `manage` that omits visibility is now `private`; an explicit non-private visibility needs `manage`. Write-only tokens that relied on the old `everyone` default for REST uploads need a token with manage, or their owner can share the papers afterwards.
- **OAuth agent clients are agents everywhere.** New setting `ROR_OIDC_AGENT_CLIENTS` (default `roracle-mcp`): a token whose `azp` is listed is an agent on `/mcp` and on REST (`/api`), never admin, without `manage`, and it cannot mint PATs. If you named your agent client differently, add it here; the web client must not be listed. A Keycloak/OAuth token on `/mcp` is also treated as a non-admin agent: `block_agents` and `exclude_llm` apply to it, it never has admin or `manage`, and handoff loads redact for it like a PAT. Admin-only tools stop working through `/mcp`; use the web app or REST with a JWT.
- **Audit.** Scope denials, rate-limit denials, failed PAT requests and admin 403s now leave durable rows (see [audit](security/audit.md#what-is-and-is-not-audited)). `admin.<endpoint>` rows keep only query parameter names.
- **Helm values (all off by default).** `observability.podMonitor.enabled` and `observability.prometheusRule.enabled` (both need the prometheus-operator CRDs). The worker is scraped on its `health` port.
- **ECK 3.3.2.** The ECK operator is pinned to 3.3.2 (its support matrix lists Elasticsearch 9.5). Mirror `docker.elastic.co/eck/eck-operator:3.3.2` and Elasticsearch 9.5.6 before upgrading the Terraform operators module ([mirror manifest](airgap-mirror-manifest.md)).

## Release notes

### 0.4.0

- Security hardening: one agent principal for PATs and `/mcp`, the `manage` token scope, quoted prompt metadata, durable audit rows for denials and failures, handoff redaction of papers a loader cannot see, a shorter [known limits](security/known-limits.md).
- Upgrade: migrations 0021 `tag_source` (**drain workers first**; then optionally run `roracle auto-tags` to backfill tags), 0022 `onboarding` and 0023 (backfills `manage` onto `write` tokens); notes in section 7.

### 0.3.0

- Advanced library search: query syntax with exact phrases, exclusions and field scope; include and exclude per filter value with match any or all for tags and authors; venue, journal (by ISSN) and has-identifier filters; year histogram with a range; date added, page count, votes and cited-by ranges; owner and citing filters; ten sorts; list, compact and table views with a column picker; the query sentence; saved searches ([library search](../user/library-search.md)).
- API: new `GET /api/search` parameters, `GET /api/search/facets/{name}` and `/api/me/saved-searches` ([agent integration](agent-integration.md#library-search-api)). The `year` list parameter is replaced by `year_min` and `year_max`.
- Browse runs from Elasticsearch: counts and facets cover every visible paper up to 10,000 (`total_capped` above), and the `after` cursor works for every browse sort.
- AI tag suggestions: the LLM suggests tags from the AI summary at ingest; `roracle auto-tags [--limit N]` backfills existing papers through the `auto_tags` bulk job. Migration 0021 recreates the claim indexes: drain workers first.
- Get started checklist and help window (migration 0022).
- Upgrade: migrations 0020 to 0022 and the required same-model reindex (section 6).
