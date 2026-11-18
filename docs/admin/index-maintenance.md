# Index maintenance

The search index in Elasticsearch is a copy: Postgres and the object store hold the papers, and the index can always be rebuilt from them. This page covers the admin-level jobs that change or rebuild it: switching the embedding model, rebuilding derived fields, re-reading references, and cleaning up. How the index is built and named is in [search index lifecycle](../architecture/search-index-lifecycle.md). Cluster work (capacity, snapshots, worker scaling) is in the [operations runbook](../enterprise/operations-runbook.md). Terms are in the [glossary](../glossary.md).

All of these are admin-only and audited. Where each is done:

| Task | Admin UI | API | CLI |
|---|---|---|---|
| Change the embedding model (re-embed) | none | `POST /api/admin/reindex` | none |
| See re-embed progress | none | `GET /api/admin/reindex` | none |
| Delete old generations | none | `DELETE /api/admin/reindex/old` | none |
| Rebuild context notes or RAPTOR | none | `POST /api/admin/retrieval/rebuild` | `roracle rebuild --what ...` |
| Delete a shadow index | none | none | `roracle rebuild --drop NAME` |
| Re-extract references and identifiers | none | none | `roracle reparse-refs` |
| Watch and retry the jobs | **Admin, Jobs** | `/api/admin/jobs` | none |
| Purge, blob GC, tag reconcile | none | `POST /api/admin/maintenance/{op}` | none |

The admin screen does not start any of these. Use the API with an admin sign-in token, or the command line.

Each admin API call also writes the generic `admin.<handler>` audit row ([audit](audit.md#what-is-recorded)), next to the specific action named below.

## Change the embedding model

Use this when the embedding model or its vector size changes, or to rebuild the index from scratch with the same model. Search stays up throughout.

1. Make sure the new model is served by the embedding endpoint and Elasticsearch has room for a second copy of the vectors.
2. `POST /api/admin/reindex` with `{"model": "<name>", "dims": <n>}` (`dims` from 1 to 4096). It answers `202` with a `job_id`. The same model name is allowed: it builds the next generation, copying each stored vector whose prompt is unchanged (same `prompt_sha`) and embedding only chunks whose title, summary, context note or prompt version changed since they were indexed. `409 reindex already running` is returned while a re-embed or a [rebuild](#rebuild-derived-index-fields) is queued or running.
3. Follow `GET /api/admin/reindex`:

   | Field | Meaning |
   |---|---|
   | `active` | The index, model and dimensions search uses now |
   | `running` | `target`, `copied` and `total` chunks (summed over slices), the `phase` and the number of `slices`; empty when nothing runs |
   | `failed` | When a slice failed for good: the target index and each failed slice's error |
   | `old` | Generations that still exist besides the active one |

4. Wait. The phases are `copy`, `converge`, `cutover` and `catchup`. Search keeps using the old index until the cutover, then switches in one step. Two catch-up rounds then copy chunks that reached the old index meanwhile, the first 300 seconds after the switch and the second 300 seconds after that.

What it does for you:

- Each generation is a new index named `chunks-<model-slug>-<n>`, where `n` counts up from 1. The copy is split into `ROR_REEMBED_SLICES` parallel chains (default 4, from 1 to 16).
- Access data is never copied from the old index. Each copied chunk gets its access fields and title freshly from Postgres, and the pass converges them against Postgres again before the switch. Papers deleted during the copy are dropped.
- A slice that fails permanently stops the switch: search never moves onto an incomplete index. Fix the cause (embedding endpoint, Elasticsearch), delete the half-built generation (below) and start again.
- When the last catch-up round ends, and `ROR_EVAL_ON_REINDEX` is on (the default), a judged [evaluation](eval.md#runs-and-the-trend) of the reference set starts, so you see the effect in the trend. A drop is the signal to re-embed again with the previous model.
- Ingest keeps writing to the old index while the copy runs. An ingest that straddles the switch fails once and retries with the new model.

Audit: `admin.reindex_start` with the model and dimensions.

### Delete old generations

After checking that search works, free the space: `DELETE /api/admin/reindex/old` answers `204`. The old generation keeps receiving every access and delete update until you do this, so do not leave it long. It removes every generation except the active one.

It answers `409` when a re-embed is running (`reindex running`), when a rebuild is running (`a rebuild is running`), and when the read alias does not point at exactly the active index (`read alias does not point at the active index; resolve first`). The last case means a switch half-failed: re-run the failed `reembed` job in **Admin, Jobs** (copy is safe to repeat) rather than deleting.

To abandon a failed or unwanted re-embed, delete the old generations the same way while the read alias still matches the active index. Audit: `admin.reindex_drop_old` with the removed indices.

## Rebuild derived index fields

Context notes (a short model-written note per chunk) and RAPTOR units (per-paper summaries) are written at index time. Rebuilding them re-runs indexing for every latest, searchable, not-deleted paper, in jobs of 25 papers. It does not touch the read alias, so live search is unaffected.

```text
roracle rebuild --what context[,raptor] [--into chunks-<slug>-shadow]
roracle rebuild --drop chunks-<slug>-shadow
```

API: `POST /api/admin/retrieval/rebuild` with `{"what": ["context", "raptor"], "into": "chunks-<slug>-shadow"}` (`into` is optional) answers `202` with the number of `jobs`. Audit: `retrieval.rebuild`.

- **Without `--into`** the rebuild goes into the active index. A RAPTOR-only rebuild writes just the summary units and leaves chunks and vectors alone.
- **With `--into`** it builds into a **shadow index**, a separate index that takes part in access and delete updates but is never read by search. Use it to judge a feature first: compare against it with `roracle eval compare --candidate-index`, see [evaluation](eval.md#compare-and-the-gates), and only when it passes set the feature and rebuild into the active index. The name must match `chunks-<slug>` (lower-case letters, digits and hyphens) and cannot be a re-embed generation. Naming the active index is the same as leaving `--into` out.
- **`context` needs `ROR_CONTEXT_MODEL`.** If it is empty the request is refused (`422`, or exit 2 on the command line). `raptor` uses `ROR_RAPTOR_MODEL`, falling back to the summary model.
- **Refusals.** It is refused (`409`, exit 2) while a re-embed runs, and a re-embed is refused while rebuild jobs are pending. The two never overlap.
- **Automatic evaluation.** After a rebuild into the active index (not a shadow), a judged evaluation of the reference set is queued, the same as after a re-embed.
- **Failures.** Each paper is rebuilt on its own. One that cannot be rebuilt is skipped and reported when its job ends as failed, naming the count. The rest are done. If a parsed copy of a paper is missing, the paper needs a re-ingest.
- **Dropping a shadow.** `roracle rebuild --drop NAME` deletes it. It refuses the active index, the index behind the read alias, re-embed generations (use delete-old above) and any moment a rebuild or re-embed job is queued or running. There is no API call for it.

## Re-extract references and identifiers

Papers ingested before identifier extraction existed have no identifiers, so their references are matched by title and year only. `roracle reparse-refs` re-runs GROBID on them.

```text
roracle reparse-refs [--limit N]
```

- It needs `ROR_GROBID_URL`. Without it the command prints an error and exits 2.
- It queues one `reparse_refs` job for each latest, searchable, not-deleted PDF paper that has no identifiers yet and no queued or running reparse job. `--limit N` queues at most `N` (0 or no flag means all, oldest first). It is safe to repeat, so run it again to pick up the rest or any that found nothing.
- Each job refreshes only the identifiers and the reference list. Titles and authors a person edited are left alone. An empty or failed GROBID answer never wipes references already stored. Links between papers are then re-evaluated in both directions.
- It prints `enqueued <n> reparse_refs job(s)`. Watch kind `reparse_refs` in **Admin, Jobs**.

## AI-suggested tags

New papers get up to 5 topical tags from their AI summary at ingest (shown dashed, "Suggested by AI from the summary"; users remove or adopt them like any tag). A paper's declared keywords and the arXiv category are hints only. Papers with an `exclude_llm` label or no summary get none. For papers ingested before this feature:

```text
roracle auto-tags [--limit N]
```

- It queues one bulk `auto_tags` job per latest, searchable paper with a summary, an LLM-allowed label and no earlier auto-tagging (`meta.auto_tagged`). Safe to repeat.
- Each job replaces only that paper's earlier AI tags; tags people added are never touched. Tags reach search through `acl_sync`.
- It prints `enqueued <n> auto_tags job(s)`.

## Watching and retrying jobs

**Admin, Jobs** shows counts by kind and status, the number of versions by stage, and a table filtered by status and kind. The kinds here are `reembed`, `rebuild_units`, `rebuild_watch`, `reparse_refs`, `eval_run` and `eval_generate`. **Retry** (on a row) and **Retry all failed** requeue failed jobs with a fresh attempt count. The API is `POST /api/admin/jobs/{id}/retry` (`409` unless the job failed) and `POST /api/admin/jobs/retry-failed`, which moves up to 1000 per call and reports `retried`. Audit: `admin.job_retry` and `admin.jobs_retry_failed`.

## Purge, blob GC and tag reconcile

`POST /api/admin/maintenance/{op}` with `op` of `purge`, `gc` or `reconcile` answers `202` with a `job_id`. `purge` hard-deletes papers soft-deleted longer than `ROR_RETENTION_DELETED_DAYS` ago (default 0, which means never). `gc` deletes unreferenced stored files older than a day. `reconcile` re-pushes access and tags from Postgres to the index; see [labels](labels.md#tags-and-index-drift). All three also run on their own every `ROR_MAINTENANCE_INTERVAL_S` seconds (default 86400). Audit: `admin.maintenance_<op>`.

## Draining workers

A worker told to stop (SIGTERM) claims no new jobs. A running job gets `ROR_JOB_DRAIN_S` seconds to finish (default 0, which means wait; the chart sets 120) and is then released to the queue without using an attempt. Ingest, rebuild pages and the re-embed copy resume where they stopped. A judged evaluation run and a single long model batch start over, so avoid rolling or scaling down workers while an `eval_run` is running. Scaling and rollouts are an operator task: see the [operations runbook](../enterprise/operations-runbook.md). Database migrations that rebuild a job index ask for workers to be drained first; the [upgrade guide](../enterprise/upgrades.md) says when.

## Related

- [Runtime settings](runtime-settings.md): the embedding model is not a free setting.
- [Evaluation](eval.md): judging a change before and after.
- [Audit log](audit.md): `admin.reindex_start`, `admin.reindex_drop_old`, `retrieval.rebuild`.
