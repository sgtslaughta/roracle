# P8 Notes & Evidence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Researchers annotate documents (comments, findings, corrections, questions, relations) with per-note visibility, threads, votes, reports and moderation; agents only *propose* notes over MCP and humans approve them; findings carry RO-Crate evidence packages (stored, scanned, never executed); public notes on retrieved passages surface in search and chat.

**Architecture:** New package `roracle/notes/` (`access.py` read rule, `service.py` CRUD/threads/votes/moderation, `anchor.py` re-anchoring, `proposals.py`, `evidence.py`, `community.py`) + `roracle/bibtex.py`. REST in `api/routes/annotations.py`, `proposals.py`, `evidence.py`, `export.py`; MCP tools in `mcp/tools_notes.py` registered from `build_mcp`. Postgres is the only store for notes (no ES notes index in P8). Evidence blobs reuse `BlobStore` + a new `evidence_scan` queue job using `ingest.scan.scan`.

**Tech Stack:** P7 stack, no new dependencies.

**Spec:** `docs/spec.md` §5 (community notes), §7 (Annotations, Agent proposals, Evidence, MCP tools); ADR-0010.

## Global Constraints

- Migration `0008_p8.py`: `revision = "0008_p8"`, `down_revision = "0007"`.
- **Read rule** (`notes.access.readable_notes(p)`), evaluated on the thread *root* (a reply is readable iff its root is, plus the reply's own status rule):
  1. note `status != 'removed'`; `hidden` only for its author, the document owner, admins;
  2. document passes `visible_documents(p)` and, for non-owner non-admins, has a `searchable` version (same rule as `library._visible`);
  3. relation notes: the target document passes the same rule (else the note is invisible — no existence leak);
  4. author, or admin, or `visibility='everyone'`, or `visibility='shared'` with an `annotation_shares` row for the user id or one of `p.groups`.
  Anything unreadable → 404 `{"detail":"not found"}`.
- Defaults: human-created notes `everyone`; agent proposals `private`.
- **Agents only propose**: every notes/proposals/evidence *write* REST route depends on `require_human` (new, in `auth.py`: `p.via == "jwt"` else 403 `{"detail":"agents must propose"}`). PATs reach notes only via MCP `propose_annotation`/`reply_to_annotation` (write scope).
- Limits: body ≤ 10 000 chars; anchor `{quote ≤ 1000, prefix ≤ 200, suffix ≤ 200, page ≥ 1, bbox {x0,y0,x1,y1} | null}` (anchor optional = document-level note); shares ≤ 50; list ≤ 500 notes per document (oldest first, no pagination); pending proposals ≤ 50 per `(user_id, client)` ("session" = PAT/client identity, MCP is stateless); bulk-approve ≤ 100 ids; evidence file ≤ `settings.evidence_max_bytes` (env `ROR_EVIDENCE_MAX_BYTES`, default 1 GiB = 1073741824).
- Votes: `PUT /annotations/{id}/vote {value: 1|-1|0}`; score = `retrieval.vote_ratio(up, down)` (smoothed `(up+1)/(n+2)`).
- Audit (`audit.audit`) for: `annotation.hide`, `annotation.unhide`, `annotation.remove` (admin), `proposal.approve`, `proposal.reject`, `evidence.delete` (by admin). Detail never contains note body.
- Note JSON (REST + MCP): `{id, document_id, version_id, version, parent_id, author: {id, username}, kind, body, anchor, relation: {type, target_document_id} | null, visibility, shares (author only, else []), status, agent_assisted, may_not_apply_to_version: int | null, votes: {up, down, mine, score}, evidence: {supports, refutes, inconclusive}, created_at, updated_at}`.

## Review Focus

1. A reader without access to the document *or* the note (or a relation's target doc) gets nothing: not in lists, not by id (404), not in search/chat context, not in shared-chat/handoff citations — Tasks 2, 6.
2. Agents cannot publish: PAT → 403 on every write route incl. approve; MCP only creates `pending` proposals; 51st pending → error — Tasks 2, 4.
3. Evidence files are never served inline/executable; infected/pending never downloadable — Task 5.
4. Hide/remove permissions + audit rows — Task 3.

## Rulings (plan-level)

- R1: Notifications deferred to P10 (P10 owns the notifications table in parallel; two tables would collide). Pending count is `GET /me/proposals?status=pending` → `total` — cost: no bell badge until P10.
- R2: Re-anchoring is lazy on read (`GET /documents/{id}/annotations`, MCP `list_annotations`): a root note whose `version_id` ≠ the doc's latest searchable version is re-anchored by an ES `match_phrase` of `anchor.quote` within the new version; found → move `version_id` + page/bbox, clear flag; not found → `stale_version = N`; ES error → leave untouched. No ingest-pipeline change (R9) — cost: first reader after a new version pays ≤ 50 ES lookups.
- R3: All note write REST routes are JWT-only (`require_human`) — agents can't bypass proposals with a PAT — cost: scripts cannot bulk-import notes; JWT-holding agents are trusted as their user.
- R4: Admins read all non-removed notes (moderation) — cost: none (admins already see all docs).
- R5: No ES index for notes in P8; community notes are attached from PG to already-R19-checked hits — cost: notes are not full-text searchable on their own (later phase).
- R6: Proposal kinds `note`, `relation` only; `evidence` is in the CHECK for ADR-0010 but not creatable; evidence upload is human REST — cost: agents hand crates to humans.
- R7: Evidence requires a `finding` note; download only when `scan_status in ('clean','unscanned')` (`unscanned` = ClamAV disabled, same policy as ingest), always `application/octet-stream`, `attachment`, `nosniff`, `CSP: sandbox` — cost: notebooks not rendered (UI phase).
- R8: Community note eligibility: root note, `visibility='everyone'`, `status='visible'`, `stale_version IS NULL`, score ≥ 0.4, reports < 3, readable by the caller; ≤ 2 per doc, ≤ 6 per response; anchor page ∈ passage pages ranks first — cost: tuning constants.
- R9: Chat cites notes as passages with `chunk_id = "note:<id>"` and `note_id`; `chat.access.recheck` treats items carrying `note_id` as unavailable unless the id is in a passed `notes` set (default empty ⇒ fail closed) — cost: handoff transcripts show note citations as unavailable until handoff passes the set (it is not required to).

---

### Task 1: Storage

**Files:** `migrations/versions/0008_p8.py`; `models.py` (or `models_notes.py` imported by `models.py` if > 500 lines — Base must stay shared); `config.py` (`evidence_max_bytes: int = 1073741824`); Test `tests/test_p8_schema.py`.
Tables (all UUID PKs default uuid4, `created_at` server_default now):
- `annotations(id, document_id→documents CASCADE, version_id→document_versions CASCADE, parent_id→annotations CASCADE null, author_id→users CASCADE, kind CHECK comment|finding|correction|question|relation, body Text, anchor JSONB null, relation_type CHECK extends|contradicts|replicates|supersedes null, target_document_id→documents CASCADE null, visibility CHECK private|shared|everyone, status CHECK visible|hidden|removed default visible, agent_assisted bool default false, stale_version int null, created_at, updated_at)`; CHECK `(kind='relation') = (relation_type IS NOT NULL AND target_document_id IS NOT NULL)`; indexes `(document_id, created_at)`, `(parent_id)`.
- `annotation_shares(annotation_id CASCADE, principal_kind CHECK user|group, principal)` PK all three.
- `annotation_votes(annotation_id CASCADE, user_id CASCADE, value CHECK -1|1)` PK (annotation_id, user_id).
- `annotation_reports(annotation_id CASCADE, user_id CASCADE, reason CHECK spam|abusive|wrong|other, created_at)` PK (annotation_id, user_id).
- `proposals(id, user_id→users CASCADE, client String default '', kind CHECK note|relation|evidence, payload JSONB, status CHECK pending|approved|rejected default pending, annotation_id→annotations SET NULL null, created_at, decided_at null)`; index `(user_id, status, created_at)`.
- `evidence(id, annotation_id CASCADE, creator_id→users CASCADE, stance CHECK supports|refutes|inconclusive, crate JSONB, blob_sha256→blobs null, filename String default '', size BigInteger null, uri String null, uri_sha256 String(64) null, scan_status CHECK none|pending|clean|infected|failed|unscanned default none, created_at)`; CHECK `NOT (blob_sha256 IS NOT NULL AND uri IS NOT NULL)`; index `(annotation_id)`.
- `evidence_reproductions(evidence_id CASCADE, user_id CASCADE, outcome CHECK reproduced|failed, note String(2000) default '', created_at)` PK (evidence_id, user_id).
- [ ] Tests: each CHECK rejects a bad value; relation consistency CHECK; delete document cascades annotations→votes/shares/evidence; `alembic downgrade 0007` then `upgrade head` works.
- [ ] **Commit** `feat(notes): add annotation, proposal and evidence storage`

### Task 2: Annotation ACL, CRUD, threads, re-anchoring (opus review)

**Files:** `roracle/notes/__init__.py`, `notes/access.py`, `notes/service.py`, `notes/anchor.py`, `auth.py` (`require_human`), `api/routes/annotations.py`, `api/app.py`; Test `tests/test_notes.py`, `tests/test_notes_anchor.py`.
**Interfaces:** `readable_notes(p) -> ColumnElement` (+ `get_readable(session, p, note_id) -> Annotation` raising 404); `create(session, p, doc_id, body: NoteIn, *, agent_assisted=False, parent_id=None) -> Annotation` (doc must be readable per rule 2; relation target per rule 3 and ≠ doc; `version_id` = latest searchable version (owner/admin: latest version); replies: `parent_id` must be readable, replies to a reply attach to its root, replies have no anchor/relation and copy root visibility); `to_json(session, p, notes) -> list[dict]` (batched: authors, votes, evidence counts, shares only for author); `reanchor(session, es, doc_id, notes) -> None` (R2; `find_quote(es, version_id, quote) -> (page, bbox) | None` via `search_index.ALIAS`, `NotFoundError`/transport errors → skip).
Routes: `GET /documents/{doc_id}/annotations` → `{items}` (re-anchors first), `POST /documents/{doc_id}/annotations` (201, `require_human`; default visibility `everyone`), `GET /annotations/{id}`, `PATCH /annotations/{id}` (author: body, visibility, shares), `DELETE /annotations/{id}` (author → 204, hard delete), `POST /annotations/{id}/replies` (201, `require_human`).
- [ ] Tests (ACL matrix): reader without doc access → 404 on list/get even for an `everyone` note; private note invisible to others, visible to author + admin; shared via user id and via group; relation whose target is invisible → hidden from that reader; non-owner sees nothing while doc only `parsed`; reply to unreadable note → 404; reply readable iff root readable; PAT → 403 on POST/PATCH/DELETE; other user PATCH/DELETE → 404 if unreadable else 403; kind/relation validation 422; body > 10 000 → 422.
- [ ] Tests (anchor): new version with quote present → `version_id` moved, page updated, flag null; quote absent → `may_not_apply_to_version == 2`; ES error → unchanged; doc-level note just moves version.
- [ ] **Commit** `feat(notes): annotations with visibility, threads and re-anchoring`

### Task 3: Votes, reports, moderation (opus review)

**Files:** `notes/service.py` (or `notes/moderation.py` if > 500 lines), `api/routes/annotations.py`, `api/routes/admin.py`; Test `tests/test_notes_moderation.py`.
Routes (all `require_human`, note must be readable): `PUT /annotations/{id}/vote {value}` → `{up, down, mine, score}`; `POST /annotations/{id}/report {reason}` → 204 (idempotent per user); `POST /annotations/{id}/hide {hidden: bool}` (document owner or admin else 403; audited `annotation.hide`/`unhide`) → note JSON; `DELETE /admin/annotations/{id}` (admin else 403; `status='removed'`, audited `annotation.remove` with `{document_id}`) → 204.
- [ ] Tests: score = smoothed ratio (0 votes → 0.5, 1 up → 0.667); vote 0 clears; hidden note invisible to third party, visible to author/doc owner/admin with `status:"hidden"`; non-owner hide → 403; removed → 404 for everyone incl. author; audit rows present without body; report twice → one row.
- [ ] **Commit** `feat(notes): votes, reports, hide and admin removal`

### Task 4: Proposals + MCP note tools + BibTeX (opus review)

**Files:** `notes/proposals.py`, `roracle/bibtex.py`, `api/routes/proposals.py`, `api/routes/export.py`, `mcp/tools_notes.py`, `mcp/server.py` (register), `api/app.py`; Test `tests/test_proposals.py`, `tests/test_bibtex.py`, `tests/test_mcp_notes.py`.
**Interfaces:** `propose(session, p, kind, payload) -> Proposal` (validates exactly like `service.create` but writes nothing else; payload visibility default `private`; `pg_advisory_xact_lock(hashtext('proposals:'||user_id))` then count pending for `(user_id, p.client)` ≥ 50 → 429 `{"detail":"too many pending proposals"}`); `approve(session, p, id, edits) -> Annotation` (owner only else 404; pending else 409; re-runs `service.create` with `agent_assisted=True`, author = approver); `reject`; `bibtex.export(session, p, doc_ids, collection_id) -> str` (only docs passing the visible+searchable rule, silently omitting the rest; key `<lastname><year><firstword>` ASCII-folded, de-duplicated with a/b; escape `{}\\%&$#_`).
Routes: `GET /me/proposals?status=pending|approved|rejected` → `{total, items: [{id, kind, client, payload, status, created_at, document: {id, title} | null}]}` (document null if no longer visible); `POST /proposals/{id}/approve {body?, visibility?, shares?}` → note JSON; `POST /proposals/{id}/reject` → 204; `POST /proposals/bulk-approve {ids}` → `{approved: [{proposal_id, annotation_id}], failed: [{proposal_id, detail}]}` (each in a SAVEPOINT) — all `require_human`. `GET /export/bibtex?doc_id=…&collection=…` (≤ 200 ids) → `text/x-bibtex`.
MCP (via `server._run`, audited): `list_annotations(doc_id)` (re-anchors), `propose_annotation(doc_id, kind, body, quote?, prefix?, suffix?, page?, relation_type?, target_doc_id?, visibility="private")` (write) → `{proposal_id, status}`, `reply_to_annotation(annotation_id, body)` (write; a `note` proposal with `parent_id`) → `{proposal_id, status}`, `export_bibtex(doc_ids?, collection?)` → `{bibtex, count}`.
- [ ] Tests: proposal invisible to other users (404 on approve); 51st pending → error, other client unaffected; approve → note `agent_assisted`, author = user, default private; approve with edits; doc became invisible → approve 404; PAT approve → 403; bulk mix of ok/failed; MCP read PAT → propose "insufficient scope"; `tools/list` includes the 4 tools; bibtex omits invisible doc, escapes, unique keys.
- [ ] **Commit** `feat(notes): agent proposals, MCP note tools and BibTeX export`

### Task 5: Evidence (opus review)

**Files:** `notes/evidence.py`, `api/routes/evidence.py`, `worker.py` (`evidence_scan` handler), `api/app.py`; Test `tests/test_evidence.py`.
**Interfaces:** `Provenance` pydantic model (required: `code {repo, commit (hex 7–64)}`, `environment {image_digest "sha256:<64 hex>"}`, `inputs`/`outputs` lists ≤ 100 of `{name, uri?, sha256?}`, `parameters` dict (≤ 64 KB as JSON), `seed` int|str|null, `agent {client, model}`, `started_at` ≤ `ended_at`; optional `description`, `uri` + `uri_sha256` for external packages); `crate(prov) -> dict` RO-Crate 1.1 (`@context` string `https://w3id.org/ro/crate/1.1/context` — never fetched; `@graph`: metadata descriptor, root Dataset, `CreateAction` with instrument `SoftwareSourceCode`, agent, start/endTime, object/result); `handle_evidence_scan(session, job, *, settings, store)` (scan → `clean`/`infected`; `NonRetryable` → `failed`; `ConnectionError` propagates for retry; empty `clamav_host` → `unscanned`; never commits).
Routes: `POST /annotations/{id}/evidence` multipart `meta` (JSON ≤ 256 KB: Provenance + `stance`) + optional `file` (`require_human`; note readable and `kind='finding'` else 422; file and `uri` exclusive; stage ≤ `evidence_max_bytes` → 413; `BlobStore.put` + `blobs` upsert + enqueue `evidence_scan`; status `pending`) → 201 evidence JSON; `GET /annotations/{id}/evidence` → `{items}`; `GET /evidence/{id}`; `GET /evidence/{id}/file` (409 `{"detail":"not available"}` unless clean/unscanned; headers per R7; streamed); `PUT /evidence/{id}/reproduction {outcome: reproduced|failed|null, note?}` → reproductions; `DELETE /evidence/{id}` (creator or admin; admin audited) → 204. Evidence JSON: `{id, annotation_id, stance, crate, file: {filename, size, sha256, scan_status} | null, uri, uri_sha256, reproductions: {reproduced, failed, mine}, created_by, created_at}`. Unreadable note ⇒ every evidence route 404.
- [ ] Tests: missing provenance field → 422; non-finding → 422; file over a small patched limit → 413; scan handler clean/infected/unscanned/size-limit (fake scan); download pending/infected → 409, clean → bytes + `attachment`/`octet-stream`/`nosniff`; stranger → 404; reproduction counts + clear; refuting evidence listed (counts in note JSON `evidence`).
- [ ] **Commit** `feat(notes): RO-Crate evidence with scanned storage and reproductions`

### Task 6: Community notes in retrieval + docs (opus review)

**Files:** `notes/community.py`, `retrieval.py` (`Passage.note_id: str | None = None`), `api/routes/search.py` (`notes: bool = True` query param; each hit gets `notes: [...]`), `chat/retrieve.py` (`include_notes=True` param; appends note passages after ranking, not counted in k), `chat/answer.py` (`_base` adds `note_id` when set), `chat/access.py` (`recheck(..., notes: set[str] = frozenset())` R9), `api/routes/chats.py` (`include_notes: bool = True` in message body), `api/routes/chats_read.py` (pass readable note ids), `mcp/server.py` (`_slim` keeps `notes`); `docs/enterprise/security.md`, `deployment-guide.md` (`ROR_EVIDENCE_MAX_BYTES`, clamd `StreamMaxLength` ≥ 1 GiB, proxy body size), `agent-integration.md` (4 tools, proposal cap), `operations-runbook.md` (`evidence_scan` job); Test `tests/test_community_notes.py`.
**Interfaces:** `community_notes(session, p, doc_pages: dict[str, set[int]]) -> dict[str, list[dict]]` (R8; item `{id, kind, body (≤ 500 chars), author, score, page}`); `readable_note_ids(session, p, ids) -> set[str]`. Chat note passage: `chunk_id=f"note:{id}"`, `note_id`, doc/version of the annotated doc, `title=f"Community note on: {doc title}"`, text = body ≤ 1200.
- [ ] Tests: search hit carries a visible `everyone` note; private/shared-not-to-me/hidden/low-score/reported/stale notes absent; `notes=false` → no notes; chat context (FakeLLM captures messages) contains the note text only when readable; shared-chat reader without note access sees that citation `available:false`; `recheck` default drops note items.
- [ ] **Commit** `feat(notes): community notes in search and chat`
