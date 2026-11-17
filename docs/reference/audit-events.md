# Audit events

Every action rOracle writes to the `audit_log` table, taken from the `audit()` call sites in
`backend/src/roracle` (plus the two places that build an `AuditLog` row directly). Policy, guarantees and gaps:
[security: audit](../enterprise/security/audit.md). Admin viewer: [admin: audit log](../admin/audit.md).

## Row shape

| Column | Meaning |
|---|---|
| `id`, `at` | Sequence number and server time |
| `user_id` | The acting user's id, or empty for the cases marked "none" below. Shown as a dash in the viewer |
| `action` | The event name (tables below) |
| `target` | Usually an object id or a route path; MCP free text is a `sha256:` prefix. Not truncated except where noted |
| `client` | JWT: the token's `azp` (the OIDC client). PAT: `pat:<token name>` |
| `detail` | JSON object (fields per event; `{}` when none) |

**Where visible.** Every row is shown in **Admin, Audit** (`GET /api/admin/audit`, admin JWT only) and is in the
`audit_log` table for operators. "Viewer" below means that page. There is no per-user view of one's own audit rows.

**Success only.** Rows share the request transaction. A request that fails after its row was added rolls the row
back, so the log records completed actions. Exceptions are called out: `admin.denied`, `label.export_blocked` and
`mcp.<tool>` rows with `error` are written outside the failed transaction. Rate-limited (429) and read-only-token
(403 `insufficient scope`) REST requests write nothing.

## Access tokens and agents

| Action | Actor | Target | Detail | Written when |
|---|---|---|---|---|
| `pat.create` | The token's owner (JWT) | Token id | `scopes` | `POST /api/me/tokens` |
| `pat.revoke` | The token's owner (JWT) | Token id | none | `DELETE /api/me/tokens/{id}`, only when it was not already revoked |
| `admin.revoke_pats` | Admin | The user id whose tokens were revoked | none | `DELETE /api/admin/users/{id}/pats` |
| `pat.use` | The PAT's owner | `METHOD /path`, cut at 200 characters | none | A **successful** REST request authenticated with a PAT. Not written for 429, 403 `insufficient scope`, handler errors, or MCP calls. Exception: when a handler commits before failing (the `block_export` 403 commits its audit row), the `pat.use` row is kept too |
| `mcp.<tool>` | The token's user (PAT or OAuth access token) | Tool-specific, cut at 200 characters (below) | `{"error": true}` on an HTTP-style error, `{"error": "internal"}` on an unexpected exception (best effort) | Every MCP tool call, resource read and prompt that passes authentication and argument validation |
| `proposal.approve` | The human who approved | Proposal id | `annotation_id`, `client` (the agent's client name) | A person approves an agent's note proposal |
| `proposal.reject` | The human who rejected | Proposal id | `client` | A person rejects it |

`mcp.<tool>` targets: `search`, `ask`, `propose_annotation` and `reply_to_annotation` write a `sha256:` prefix of the
text; `get_document` and `list_annotations` the document id; `get_passages` the chunk ids (cut at 200); `upload` the filename; `create_handoff` the title;
`load_handoff`, `get_handoff_part` and `append_to_handoff` the handoff code; `propose_label` `<doc id>:<label>`;
`save_draft`, `validate_draft` and `check_citations` the draft id; `export_bibtex` the ids or collection; others none.
Tool list: [MCP tools](mcp-tools.md). MCP audit rules: [MCP](mcp.md#audit).

## Admin actions

| Action | Actor | Target | Detail | Written when |
|---|---|---|---|---|
| `admin.<handler>` | Admin (JWT) | The request path | `query`: the query parameters | **Every** successful admin request, reads included. `<handler>` is the route function name, for example `admin.list_users`, `admin.list_audit`. Mutating admin routes also write the specific row below |
| `admin.denied` | none (the user is in `detail`) | The request path | `user_id`, `username` | A non-admin, or any PAT, calls an admin route (403). Written from its own session, best effort |
| `admin.connector_create` | Admin | Connector id | `connector`: the connector as returned by the API (secrets masked `***`) | Connector created |
| `admin.connector_update` | Admin | Connector id | `before`, `after` (same shape) | Connector changed |
| `admin.connector_delete` | Admin | Connector id | `connector` | Connector deleted |
| `admin.settings_update` | Admin | `/admin/settings` | `changes`: `{key: {old, new}}` | A runtime setting changed or reset |
| `admin.tags_merge` | Admin | The target tag | `sources`, `target`, `documents` (count) | Tags merged |
| `admin.job_retry` | Admin | Job id | `kind` | One job retried |
| `admin.jobs_retry_failed` | Admin | empty | `kind`, `retried` (count) | Failed jobs retried in bulk |
| `admin.maintenance_<op>` | Admin | Job id | none | `op` is `purge`, `gc` or `reconcile` |
| `admin.reindex_start` | Admin | `/admin/reindex` | `model`, `dims` | Re-embed started |
| `admin.reindex_drop_old` | Admin | `/admin/reindex/old` | `indices` | Old indices dropped |
| `retrieval.rebuild` | Admin | `/admin/retrieval/rebuild` | `what`, `into` | Retrieval aid rebuild |
| `admin.writing_profile` | Admin | Profile name | `name`, `version`, or `deactivated: true` | Writing profile saved or deactivated |
| `draft.review` | Admin | Draft (document) id | `decision`, `note_len`, `visibility` | Admin approves or rejects a submitted draft |

The route paths in `target` omit the client-visible `/api` prefix (they are the server-side paths).

## Evaluation

| Action | Actor | Target | Detail | Written when |
|---|---|---|---|---|
| `eval.set.create`, `eval.set.update`, `eval.set.delete` | Admin | Set id | `name` (create, delete) or `changes` (sorted field names, update) | Reference set CRUD |
| `eval.query.create`, `eval.query.update`, `eval.query.delete` | Admin | Query id | `set`, `key` (create, delete) or `changes` (update) | Gold query CRUD |
| `eval.import` | Admin | Set id | `created` | Queries imported |
| `eval.generate` | Admin | Set id | `n`, `principal` | Question generation |
| `eval.run` | Admin | Set id | `path`, `judge` | A run started |
| `eval.import_beir` | none (CLI, no principal) | Set name | `dataset`, `owner`, `created`, `reused`, `queries`, `near_dup_kept`, `near_dup_parked` | The BEIR import CLI |

## Labels

| Action | Actor | Target | Detail | Written when |
|---|---|---|---|---|
| `label.create` | Admin | Label id | `name`, `rank` | Label created |
| `label.update` | Admin | Label id | `name`, `changes` | Label edited |
| `label.delete` | Admin | Label id | `name` | Label deleted |
| `label.reorder` | Admin | `/admin/labels/order` | `ids` (ordered) | Labels reordered |
| `document.label` | Owner, admin or the approver | Document id | `from`, `to` (label ids), `via` (`user`, `proposal`, `near_dup` or `submit`), `reason` when given | A document's label changed |
| `annotation.label` | Author, admin or approver | Note id | `from`, `to`, `own_from`, `own_to`, `via`, `reason` | A note's effective or own label changed |
| `label.export_blocked` | The blocked user or PAT | Document id (chat id for chat export) | `route`: `document.file`, `document.pdf`, `document.docling`, `evidence.file`, `draft.build` or `chat.export` | A download or export was refused. Written and committed before the 403; for `chat.export` the export still succeeds with text withheld |
| `label.view` | A non-owner non-admin | Document id | `route`: `document.file`, `document.pdf` or `document.docling` | Inline read of a `block_export` document; at most one row per user, document and UTC day |

Details never contain document or note text. Query: `GET /api/admin/audit?action=label.` or `action=document.label`.

## Documents, notes and evidence

| Action | Actor | Target | Detail | Written when |
|---|---|---|---|---|
| `document.delete` | Owner or admin | Document id | `title`, `owner_id` | Soft delete |
| `document.transfer` | Owner or admin | Document id | `from`, `to` (user ids) | Ownership transferred |
| `annotation.hide`, `annotation.unhide` | Admin or the document owner | Note id | none | Note hidden or restored |
| `annotation.remove` | Admin | Note id | `document_id` | Note removed |
| `evidence.delete` | Admin | Evidence id | `annotation_id` | **Only** when someone other than the creator deleted it; the creator's own delete writes nothing |

## Handoffs

| Action | Actor | Target | Detail | Written when |
|---|---|---|---|---|
| `handoff.create` | Creator | Handoff code | `kind` | Created (web or MCP) |
| `handoff.load` | The loader | Code | none for the manifest; `part`, `page` for transcript or notes pages | Each manifest or page load, by the creator or any loader in the audience |
| `handoff.note` | The writer | Code | none | A note appended |
| `handoff.update` | Creator | Code | none | Title, goal, brief, audience, shares or expiry changed |
| `handoff.revoke` | Creator | Code | none | Revoked (once) |

Handoff contents are frozen at creation; the audit rows record access, not content.

## Writing

| Action | Actor | Target | Detail | Written when |
|---|---|---|---|---|
| `draft.create` | Author | Document id | none | Draft created |
| `draft.save` | Author or co-author | Document id | `version` | New draft version saved |
| `draft.coauthors` | Author | Document id | `users`, `groups` (counts) | Co-authors changed |
| `draft.delete` | Author | Document id | none | Draft deleted |
| `draft.build` | Author or co-author | Document id | `version`, `format` | PDF build started (after label and text gates pass) |
| `draft.cite_ack`, `draft.cite_unack` | Author (human only) | Document id | `key` | A citation warning acknowledged or cleared |
| `draft.submit` | Author (a write-scope PAT can also do this over REST) | Document id | `version` and either `review: true` or `document_version` | Submitted |
| `draft.publish` | Author or the approving admin | Document id | `version`, `visibility` | The draft became a published document |
