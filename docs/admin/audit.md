# Audit log

rOracle keeps a record of admin actions, label changes, access-token use and other security-relevant events. **Admin, Audit** is the viewer. The application only ever inserts audit rows: it has no code that edits or deletes them. Nothing in the database stops someone with write access from changing or removing rows, so protect database write access accordingly. This page covers how to read it, which events it holds, how to get data out and how long it is kept.

## The viewer

<!-- screenshot: admin-audit: the Audit log with both filters and an expanded details cell -->

Entries come newest first, 50 at a time. **Load older entries** fetches the next page.

| Control | What it does |
|---|---|
| **Action starts with** | Prefix match on the action name: `admin.` shows every admin call, `label.` every label event, `document.label` one action. Typing is debounced; the characters `%` and `_` are taken literally |
| **User ID** | Only that user's entries. It must be a UUID (a user's id is in the Users API and in `GET /api/me`); anything else is flagged and ignored |
| Time, User, Action, Target, Details | One row per event. Details is the event's JSON, cut at 160 characters with **More** and **Less** |
| Row menu | **Copy details** and **Copy target** |

The **User** column shows a dash when the row has no user. That is the case for refused admin calls (below).

The API is `GET /api/admin/audit` with `action`, `user_id`, `before_id` and `limit` (1 to 200, default 50). It returns `items` and `next_before_id`, which is the `before_id` for the next page, or `null` at the end. Each item has `id`, `at`, `user_id`, `username`, `action`, `target`, `client` and `detail`.

Opening the log is itself recorded: each page you load adds an `admin.list_audit` row.

## What is recorded

Rows share one shape: when, who (user id), an action, a target (usually an id or a path), the client and a JSON detail. The actions below are the ones present in the code. The [security guide](../enterprise/security.md#6-audit) gives the policy view.

| Action | Written when | Target and detail |
|---|---|---|
| `admin.<handler>` | Any admin request (`admin.list_users`, `admin.list_audit` and so on), reads included | The request path; the query parameters |
| `admin.denied` | A non-admin, or an access token, calls an admin route (`403`) | The path; the user id and name are in the detail, not in the User column |
| `admin.connector_create`, `admin.connector_update`, `admin.connector_delete` | Connector changes | The connector id; its settings with `***` for the secret, before and after for updates |
| `admin.settings_update` | A runtime setting changed or reset | `changes` with old and new values |
| `admin.revoke_pats` | An admin revoked a user's tokens | The user id |
| `admin.tags_merge` | Tags merged | The target tag; sources and number of documents |
| `admin.job_retry`, `admin.jobs_retry_failed`, `admin.maintenance_<op>` | Job retries and maintenance runs | The job id or counts |
| `admin.reindex_start`, `admin.reindex_drop_old`, `admin.writing_profile`, `retrieval.rebuild`, `eval.*` | Index, writing-profile and evaluation administration | Written next, under index maintenance and evaluation |
| `label.create`, `label.update`, `label.delete`, `label.reorder` | Label set changes | The label id, with name and rank or the fields changed (old and new); for `label.reorder`, the ordered ids |
| `document.label`, `annotation.label` | A paper's or note's label changed | `from` and `to` are label ids; `via` says how; the `reason` when given |
| `label.export_blocked` | A download or export was refused by a label | The document id; the route |
| `label.view` | A non-owner non-admin read a no-export paper in the reader | The document id; at most one row per user, document and day |
| `pat.create`, `pat.revoke` | A user made or revoked an access token | The token id; scopes on create |
| `pat.use` | A REST request made with an access token | `METHOD /path` (cut at 200 characters); the client is `pat:<token name>` |
| `mcp.<tool>` | An agent called an MCP tool | The tool's target; `error` in the detail when it failed |
| `document.delete`, `document.transfer` | A paper deleted or given to another owner | The document id; title and owner, or old and new owner |
| `annotation.hide`, `annotation.unhide`, `annotation.remove` | Note moderation | The note id |
| `handoff.create`, `handoff.load`, `handoff.update`, `handoff.note`, `handoff.revoke` | Handoff lifecycle | The handoff code or id |
| `proposal.approve`, `proposal.reject` | A person decided an agent's proposal | The proposal id |
| `draft.*`, `evidence.delete` | Writing and evidence actions | The draft, document or evidence id |

A few examples for the filter box:

- `admin.` then read the **Target** column: who looked at or changed what in the admin area.
- `admin.denied`: attempts by people or tokens without admin rights.
- `label.` and `document.label`: the history of sensitivity decisions ([labels](labels.md#lowering-a-label)).
- `pat.`: access-token creation, revocation and use. Use the **User ID** filter for one person.

Row details are for investigating, not for storing content: they hold ids, names, counts and short reasons. Treat them as sensitive anyway, because paper titles and user names can appear in them.

## Getting data out

There is no export button and no CSV download. The options are:

- **The viewer.** Filter, then copy details or targets from the row menu.
- **The API.** Page through `GET /api/admin/audit` with `next_before_id`. It needs an interactive admin sign-in: an access token is refused, so a scheduled export cannot use one. Every page fetched adds its own `admin.list_audit` row.
- **The database.** The data is the `audit_log` table in Postgres (columns `id`, `at`, `user_id`, `action`, `target`, `client`, `detail`, with `detail` as JSON). Operators with database access can query it directly and forward it to a SIEM. That is an operator task: see the [security guide](../enterprise/security.md#6-audit). The [operations runbook](../enterprise/operations-runbook.md) has sample queries for label and blocked-export events only.

## Retention

rOracle never deletes audit rows. There is no retention setting and none of the maintenance steps (purging deleted papers, pruning old jobs, collecting unused blobs, reconciling the index) touches the table, so it grows for as long as the database exists. If a user record is ever removed from the database, their rows stay with the user cleared.

If your policy needs a limit, it has to be applied outside rOracle, for example by archiving rows to your log system and trimming old ones in the database. Decide this with your security team before the table gets large, and keep what your policy requires: the log is the only record of who lowered a label or revoked a token.

## Related

- [Users and groups](users-groups.md)
- [Sensitivity labels](labels.md)
- [Security guide](../enterprise/security.md#6-audit)
