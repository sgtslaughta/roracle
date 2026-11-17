# ADR-0003: Own job table in Postgres instead of a message broker

Status: Accepted (2026-11-15)

## Context
Ingest is ~10k docs/day (~7/min). Reliability matters more than throughput: an upload must never
exist without its job, or a job without its upload. Libraries like procrastinate use their own
connection, so they cannot share the API's SQLAlchemy transaction.

## Decision
A `jobs` table (`id, kind, payload jsonb, status, attempts, run_after, last_error, timestamps`).
Enqueue = INSERT in the caller's transaction. Workers claim with
`SELECT … FOR UPDATE SKIP LOCKED LIMIT 1`, retry with exponential backoff, max 5 attempts.
Valkey is optional (cache/rate limits), never the queue.

## Consequences
- No broker to run HA. Queue depth is a SQL query (metrics, admin dashboard).
- Revisit (RabbitMQ) only if queue metrics show Postgres is the bottleneck.

## Addendum (2026-11-15, P1 final review)

- `run_once` claims, runs the handler inside a SAVEPOINT, and records `complete`/`fail` in one transaction. The claim's row lock is held for the whole job, so a crashed worker's transaction aborts and the job is `queued` again — no stale-job reaper.
- Handlers must never commit the session they are given. Progress that must survive a crash (ingest `stage`) is written through a separate short session.
- A process crash does not bump `jobs.attempts`; handlers that can crash the process bound retries themselves (ingest: `document_versions.ingest_attempts`).
- Trade-off: a job's transaction stays open for its duration (minutes for large PDFs). Revisit with leases + heartbeat if vacuum lag or connection count becomes a problem.

## Amendment (2026-11, Task 10.3): leases instead of a transaction held for the whole job

Why: with the P1 design every running job held one open transaction (an xid, row locks, an old snapshot-adjacent
`xact_start`) for minutes. With 4-12 always-busy workers the oldest transaction was always minutes old, so autovacuum
could not reclaim dead tuples in `jobs`, `notifications`, `votes`, `audit_log`, `document_versions` (perf audit H1), and
each worker pinned a connection for the job's duration.

Decision (migration 0015, `jobs.lease_until`, `ROR_JOB_LEASE_S=90`, heartbeat every 30 s):
1. **Claim commits at once.** `claim` sets `status='running'`, `attempts += 1` and `lease_until = now() + lease` and
   `run_once` commits: the row lock lasts milliseconds. `attempts` is also the **fencing token** of the claim.
2. **The handler runs in its own session/transaction**, inside a SAVEPOINT, with the same contract as before (it must
   not commit). A daemon thread extends the lease every `lease/3` (`heartbeat`, guarded by `status='running' AND
   attempts = n`).
3. **Completion is in the handler's transaction**: `complete` (guarded by status and attempts) then commit, so the
   handler's DB writes and "done" are atomic exactly as before. If the lease was lost (the job was requeued/reclaimed)
   the guard matches nothing and the whole handler transaction is rolled back: **DB side effects made through the
   handler session are applied exactly once**, never twice, even when a slow worker finishes after a reclaim.
4. **A handler exception** rolls back the handler transaction only; `fail` then records error/backoff in a fresh short
   transaction (same guard), so the attempt spent at claim survives.
5. **Expired leases are requeued** by `requeue_expired` (every idle worker, at most every 30 s, plus any caller):
   `running AND lease_until < now()` -> `queued` with the usual backoff, or `failed` at `MAX_ATTEMPTS` (+ the failure
   notification). Because `attempts` is incremented at claim, a worker that dies mid-job **spends an attempt**: a
   poison job that kills workers now stops after 5 tries (before: it looped forever, "a process crash does not bump
   attempts"). Recovery latency after a crash: `lease_s` (+ up to 30 s sweep).
6. No xid-holding long transaction in the common case: the claim transaction is gone and a read-then-wait handler
   transaction is `idle in transaction` WITHOUT an xid or snapshot (READ COMMITTED), so vacuum is not blocked. Known
   exceptions that do hold an xid/locks for part of a job: `maintenance` purge (writes PG rows per document through the
   handler session while making ES calls) and the re-embed cutover (`ALIAS_LOCK` advisory lock across ES alias calls);
   both predate this amendment. Under PgBouncer transaction mode an idle-in-transaction handler still pins one server
   connection; `idle_in_transaction_session_timeout` must stay unset on the worker role (connection-budget.md).
7. **A lost lease no longer implies a dead worker.** A worker can lose its lease while alive (partition, long pause,
   shutdown release). Then two runs may overlap. Protection, in layers: (a) the heartbeat sets `lost` and handlers abort
   cooperatively at checkpoints (`queue.check_lease()`: ingest stage boundaries, reembed/rebuild/connector/eval loops)
   by raising `LeaseLost` (the transaction rolls back, nothing is recorded); (b) every write made through an OWN short
   session (the R9 helpers in `ingest/state.py`, the near-dup decision, connector items) is FENCED: `queue.fence(session)`
   takes `FOR SHARE` on the job row of this claim (`id`, `attempts`, `status='running'`) inside the same transaction
   and raises `LeaseLost` otherwise; `FOR SHARE` also keeps `requeue_expired` from stealing the job between the check and
   the write; (c) `mark_searchable_and_enqueue` is a compare-and-set on `stage <> 'searchable'` (a repeat enqueues no
   second `citations` job and no second notification); (d) completion is fenced by `attempts`. So the earlier statement
   "stages are resumable and monotonic" holds for sequential re-runs, and for overlapping runs the stale one is stopped
   by (a)-(c). External effects (ES writes, S3, LLM spend) of a stale run can still happen once more; they are
   idempotent by design.
8. **Latency of recovery and shutdown.** Crash recovery latency is the lease (90 s) plus up to one sweep interval
   (idle workers every 30 s, busy workers on every heartbeat tick). The lease is deliberately short; the heartbeat is
   one single-row UPDATE per 30 s on its own connection (bounded by statement/lock timeouts and TCP keepalives).
   On SIGTERM a worker stops claiming and lets the running job finish for `ROR_JOB_DRAIN_S` (3300 s in the chart, below
   the 3600 s pod grace period); at the deadline it RELEASES the job (`release_active`: the handler is told to abort,
   the job goes back to `queued` with its attempt refunded, no lease wait): a rolling deploy therefore does not spend
   attempts. A SIGKILL/OOM/node loss is the only path that spends an attempt (the claim already counted it).

Guarantees per handler (the contract is **at-least-once execution, exactly-once for DB writes made through the handler
session, idempotent for everything else**). A crash after an external effect and before completion re-runs the job:

| handler | re-run is safe because |
|---|---|
| `ingest` | stages are resumable (`set_stage`/`update_version` use their own short sessions, are fenced to the claim, and commit at once); ES chunk ids are deterministic (`<version>:<n>`), stale chunks are pruned; `mark_searchable_and_enqueue` is one transaction (stage + `citations` job) and a re-run on a terminal stage returns early; `ingest_attempts` bounds crash loops independently |
| `citations`, `evidence_scan`, `acl_sync`, `lsh_backfill`, `rebuild_units`, `rebuild_watch` | recompute from Postgres and overwrite (`write_bands` = delete+insert; `acl_sync` converges ES to PG; edges/units are replaced) |
| `reconcile_tags`, `maintenance` | follow-up jobs are enqueued through the handler session (atomic with completion); reconcile dedups queued `acl_sync` per document; purge/gc are idempotent deletes (ES first, then Postgres) |
| `reembed` | progress is the `cursor` payload of the next job, enqueued in the handler transaction; the copy indexes by `_id` (overwrite) |
| `connector_run` | per-item sessions commit at once; `ConnectorItem(etag, sha256)` makes a repeat a no-op `unchanged` |
| `connector_tick` | enqueues runs guarded by the active-run check |
| `eval_run`, `eval_generate` | NOT exactly-once: the run row is stored through its own session, so a crash mid-run leaves a partial run and the retry makes a new one (a failed run is `NonRetryable` by design); acceptable, they are operator-triggered |

Notifications/audit rows written through the handler session are atomic with completion (a reclaimed job's are
rolled back, not duplicated); rows written through another session (`sf`) are not, and today only ingest stage
writes do that.

Operations: workers must be drained before applying migration 0015 (it takes `lock_timeout 10s` on `jobs` and is re-runnable) (a pre-0015 worker holds its claim in an open
transaction and has no lease; new workers simply claim through leases). `ROR_JOB_LEASE_S` must exceed the longest
gap between two heartbeats a stalled process could have (a stop-the-world pause longer than the lease makes the
job be requeued; the fencing token still prevents double completion). The invariants doc
(`.superpowers/teams/README.md`) mentions "claim/handler/complete in one transaction": update it to this amendment.

