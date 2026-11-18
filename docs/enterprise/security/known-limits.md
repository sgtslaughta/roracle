# Security: known limits

Part of the [security architecture](../security.md). These are behaviours of the shipped code that a security
reviewer or ISSO should know before relying on a control. Each was checked against the code in this repository; they
are limits of the design, not a list of open defects. For what the controls do guarantee, see
[access control](access-control.md), [audit](audit.md) and the [threat model](threat-model.md).

## Audit

- **Audit rows follow the request transaction.** REST `pat.use` and `admin.<endpoint>` rows are written only for
  successful calls: if the handler raises, the transaction rolls back and the row with it. Requests rejected for rate limit (429) or
  for a read-only token writing (403 `insufficient scope`) are not audited at all. MCP tool calls are audited as
  `mcp.<tool>` (including most failures), but 401, 429, 413 and schema-validation rejections on `/mcp` are not.
  `admin.denied` is written from a separate session and is best effort. (`backend/src/roracle/auth.py`, `db.py`,
  `api/routes/admin_deps.py`, `mcp/server.py`.)
- **Some activity is never audited**: sign-in, search, general document views and downloads (only `label.view` on
  `block_export` documents), chat questions, uploads, sharing and visibility changes. No code writes those rows.
- **The audit log has no database-level immutability and no retention.** The application only inserts rows, but
  nothing in Postgres prevents someone with write access from editing or deleting them, and nothing ever deletes
  them. Forward the table to a write-once store and apply your own retention. ([Audit](audit.md), [data retention](data-retention.md#audit-log).)

## Agents and the MCP mount

- **A write-scope PAT can submit drafts through the REST API** (`POST /api/drafts/{id}/submit`): that route uses the
  general principal, not the human-only guard. MCP has no submit tool, and the writing profile's admin review still
  applies. (`api/routes/drafts_submit.py`.)
- **PAT group removal applies only at the owner's next JWT-authenticated request.** A PAT's groups are the snapshot taken at creation
  intersected with the groups stored on the owner's user row, which is rewritten only when the owner makes a
  Keycloak-JWT request. Removing someone from a Keycloak group does not narrow their PAT until then. Revoke the
  tokens (`DELETE /api/admin/users/{id}/pats`) for immediate effect. (`pats.py`, `auth.py`.)
- **A write-scope PAT can do far more than submit drafts.** Routes built on the general principal have no human-only
  check, so a PAT acts with its owner's rights: `PATCH /api/documents/{id}` (visibility, shares, org unit, co-author
  removal), `DELETE /api/documents/{id}`, `POST /api/documents/{id}/transfer`, uploads, tags, votes, collections, chats
  and chat shares, and handoffs. **An agent can therefore widen a document's visibility or hand over ownership**, and
  these actions leave only a `pat.use` row (plus `document.delete` or `document.transfer`; visibility changes have no
  specific audit row). Use read-only tokens unless the agent really needs write, and revoke tokens promptly.
  (`api/routes/documents_write.py`, `documents_manage.py`, `library.py`, `handoffs.py`, `chats*.py`.)
- **Per-token rate limiting is off by default** (`ROR_RATE_LIMIT_PER_MINUTE=0`) and, when set, is per API replica.
- PAT misuse detection (new client, burst use) does not exist.

## Labels and handoffs

- **Chat handoffs carry transcript text verbatim**, except assistant replies that cite label-gated papers (withheld per loader as
  described in [access control](access-control.md#deterrent-not-drm)). Replies that drew on papers the loader cannot see
  (ACL, or `block_agents`) are withheld as `[withheld: unavailable]` and those citations are dropped. User messages and the handoff **brief, goal and
  notes are free text and are not label-gated**: anything typed or pasted there is visible to every loader in the audience
  until the handoff expires or is revoked. Expired handoffs stay in the database.
- **`block_export` is a deterrent, not DRM.** It blocks download and export routes for non-owners and non-admins (PATs
  included) and audits inline reads (`label.view`, once per user, document and day). It cannot stop copying,
  screenshots or printing of text a person may read inline, and the document owner (JWT or PAT) and admins on an interactive JWT are exempt (an admin's PAT is not, because a PAT is never admin).
- **`exclude_llm` binds LLM context, not embeddings.** Chunks are embedded regardless, summaries written before a label was
  raised are not deleted, and past chat answers are not redacted for their owner.
- Labels are handling markers inside rOracle, not a classification system. They cannot protect regulated data on their own.

## Retrieval and label timing

- **Rerank sees just-revoked text during the `acl_sync` window.** Library search, chat and `ask` (`chat/retrieve.py`) filter in Elasticsearch, reranks
  (the reranker service receives the candidate passage text), and only then re-checks visibility in Postgres. After a
  share, visibility or label change, Elasticsearch still holds the old ACL until the `acl_sync` job runs, so the
  reranker can score text the caller no longer may read. The Postgres re-check (R19) drops those results before
  they reach the caller, so nothing is returned, but the text did reach the reranker endpoint.
- **The reranker may score `exclude_llm` text.** A user ruling: the cross-encoder is not generative and returns scores only. Keep
  `ROR_RERANK_URL` on a trusted network. (`retrieval.py`, [access control](access-control.md#gates).)
- **Chunks of a soft-deleted document stay in Elasticsearch if the immediate delete fails** and purge is off (the default):
  `acl_sync` skips deleted documents. Postgres hides them (R19), but the reranker can still score their text.
- Near-duplicate checks compare only against documents the caller can see, which can leave invisible duplicates in the corpus.

## Prompt injection residual

Delimiter escaping (`_quote`) covers passage, note, unit and finding bodies. In the main chat prompt the document title
(`chat/answer.py::_label`, whitespace-normalised only) and community-note titles and authors are placed outside the
quoting tags **unquoted**, so a crafted title or author name is model input that is not delimiter-escaped. Titles come
from document metadata or uploaders. The global map and answer prompts do quote titles.

## Quota and observability

- **The upload quota counts soft-deleted papers until purge.** `ROR_UPLOAD_QUOTA_BYTES` sums every non-rejected,
  non-cancelled version of the user's own documents without filtering `deleted_at`, and `ROR_RETENTION_DELETED_DAYS`
  defaults to 0 (never purge). Deleting a paper does not free quota unless a purge is configured or run.
  (`api/routes/documents.py`, `maintenance.py`.) Connector documents are exempt from the quota.
- **The repo ships no Prometheus alert rules and no api/worker PodMonitor.** Only the CNPG Postgres `PodMonitor` is rendered.
  Nothing scrapes `/metrics` until you add a `PodMonitor`, and no alert fires on security signals such as 401 or 429
  bursts. ([Observability](../observability.md).)

## Platform and supply chain

- **ECK 2.16.0 example pins versus Elasticsearch 9.5.6.** The example tfvars profiles pin ECK 2.16.0 while the chart's
  default Elasticsearch is 9.5.6, which needs an ECK 3.x release that lists 9.5. Raise `charts.eck` and
  `operator_images.eck` before installing ([upgrades](../upgrades.md), [mirror manifest](../airgap-mirror-manifest.md)).
- **Images are not stated to be minimal or shell-free.** The backend image is `python:3.12-slim` (a Debian base with a shell) running as UID 10001.
- **No SBOM, vulnerability-scan or image-signing stage in CI, no Pod Security Admission labels, no STIG baseline.** See
  [network and platform hardening](network.md#7-platform-hardening).
- **The latex-builder is not shipped.** `format: latex` builds return 501. A site that implements the builder owns its isolation
  ([contract](../latex-builder.md)).
- **Connector S3 endpoints are admin-chosen and not SSRF-filtered**; with an allowlisted internal CIDR the HTTP
  connector can read any HTTP service in that range ([threat model](threat-model.md#9-connectors)).
- **Network isolation depends on the CNI.** Parser sandboxes have "no egress" only where `networkPolicy.enabled` is true
  and the cluster enforces NetworkPolicy.
- Encryption at rest and TLS to data services are properties of the storage class, object store and operator choices;
  rOracle does not provide them.
