# Security: audit log

Part of the [security architecture](../security.md). The full list of actions, with actor, target and detail
fields, is the [audit events reference](../../reference/audit-events.md). The admin viewer and its filters are in
[admin: audit log](../../admin/audit.md). Log shipping and SIEM wiring are in [observability](../observability.md).

## 6. Audit

The `audit_log` table (id, time, user, action, target, client, detail) is written by the application through one
helper, `audit()` in `backend/src/roracle/audit.py`, which adds a row to the caller's database transaction.

### What the log guarantees

- **The application only inserts.** No code updates or deletes `audit_log` rows.
- **There is no database-level immutability.** Nothing in Postgres (no trigger, rule, revoked privilege or
  separate schema) stops someone with write access to the database from editing or deleting rows. Treat the log as
  evidence only as far as you trust and monitor database write access.
- **There is no retention.** rOracle never deletes audit rows and has no retention setting. See
  [data retention](data-retention.md#audit-log).
- If a user row is removed, that user's audit rows are kept with the user cleared (`ON DELETE SET NULL`).
- Rows carry ids, names, titles (for example `document.delete`), counts and short reasons, not document or note body text. Free text in a target is a
  SHA-256 prefix (MCP `search`, `ask` and note bodies). Connector secrets are redacted (`***`).

### What is and is not audited

Audit rows share the request's database transaction, so they follow its fate. Durable rows (listed below) are replayed after a rollback:

| Case | Audited? |
|---|---|
| REST request with a PAT, handler succeeds | Yes: `pat.use` with `METHOD /path` (cut at 200 characters) and the client `pat:<token name>` |
| REST request with a PAT, handler raises (404, 403, 409, 422, 5xx) | Yes: `pat.use` is replayed after the rollback with `user_id` null, the user in `detail.user_id` and the status in `detail.failed` |
| REST request rejected for rate limit (429) | Yes: `auth.rate_limited`, at most one per principal per minute, so a flood cannot fill the log |
| REST request denied for scope (403 `insufficient scope`, `insufficient scope: manage`) | Yes: `auth.scope_denied` with `detail.need` (`write` or `manage`) and `detail.via` |
| Admin route, handler succeeds | Yes: `admin.<handler>` (reads included) plus the action's own row where there is one |
| Admin route, handler raises | Yes: `admin.<handler>` replayed with `detail.failed`; the query string is never stored, only parameter names |
| Non-admin or PAT calling an admin route (403) | `admin.denied`, replayed after the rollback like the rows above. If the replay itself fails it is logged, counted in `roracle_audit_write_failures_total` and the original status is still returned |
| MCP tool call that passes authentication and validation | Yes: `mcp.<tool>`; on an HTTP-style error a fresh `mcp.<tool>` row with `error: true`; on an unexpected exception best effort with `error: "internal"` |
| MCP request rejected at HTTP level (401, 429, 413) or failing schema validation | **No** |
| Export refusals (`label.export_blocked`) | Yes, committed before the 403 so the refusal does not erase it |

MCP never writes `pat.use`; REST never writes `mcp.*`. An MCP request rejected for rate limit (429) writes `mcp.rate_limited` (at most one per principal per minute) from its own session. No row holds a token, query string or request body.

Events that are **not** audited, because no code writes them: sign-in (the user row is upserted, no audit row),
search queries (REST or MCP `search` beyond the `mcp.search` row), document view or download (apart from
`label.view` on `block_export` documents), chat questions and answers, uploads, sharing and visibility changes
(`PATCH /api/documents/{id}`, collections, chat shares), and note creation. Earlier versions of this page listed
sign-in and search auditing; that was never implemented.

### Reading and exporting

Admins read the log in the viewer or `GET /api/admin/audit` (`action` prefix, `user_id`, `before_id`, `limit`);
the endpoint needs an interactive admin JWT, and each page fetched adds an `admin.list_audit` row. There is no
export button: operators forward the table to the SIEM from Postgres. Label events can be queried with
`GET /api/admin/audit?action=label.` or `document.label`.

### Related

- Per-token rate limit and input guard: [threat model](threat-model.md#4-abuse-and-rate-limits).
- Handoff loads are audited as `handoff.load`; chat handoff content is gated as described in
  [access control](access-control.md#deterrent-not-drm).
