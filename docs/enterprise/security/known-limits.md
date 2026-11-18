# Security: known limits

Part of the [security architecture](../security.md). These are limits the project accepts, each with its reason. They
are behaviours of the shipped design, not open defects. For what the controls guarantee, see
[access control](access-control.md), [audit](audit.md) and the [threat model](threat-model.md).

## Audit

- **The audit log has no database-level immutability and no retention**: the application only inserts, and enforcing
  write-once storage and retention belongs to your database and SIEM. ([Audit](audit.md), [data retention](data-retention.md#audit-log).)
- **401 responses are counted and alerted, not audited per row**, so unauthenticated traffic cannot fill the log.
- **Rate-limit denial rows are throttled to one per principal per minute**, for the same reason.
- **Audit replay rows keep `user_id` only while the user row exists**; otherwise the id is in `detail.user_id` (the admin User ID filter matches both).
- **Sign-in, search, general document views and downloads, and chat questions are not audited**: volume and privacy
  cost outweigh the value; only `label.view` on `block_export` documents is recorded.

## Agents and the MCP mount

- **PAT group removal applies at the owner's next JWT-authenticated request**: the token's groups are a snapshot
  intersected with the owner's stored groups. Revoke tokens (`DELETE /api/admin/users/{id}/pats`) for immediate effect.
- **Per-token rate limiting is off by default and per API replica**: a process-local bucket needs no shared state. Add
  ingress limits for a global cap.
- **There is no PAT misuse detection beyond the alerts**: new-client and burst analytics are left to your SIEM.
- **OAuth agent tokens always hold `read` and `write`.** A Keycloak token whose `azp` is in `ROR_OIDC_AGENT_CLIENTS`
  (e.g. `roracle-mcp`) is an agent on REST and `/mcp`, never admin and never `manage`, but its OAuth scopes are not
  mapped: there is no read-only OAuth agent. Revoke it in Keycloak (session or consent); `ROR_PATS_ENABLED` and the
  owner sign-in bound do not apply. It shares the user's rate-limit bucket (`user:<id>`) with the browser session, and
  all its pending proposals share one `client` (the `azp`) for the 50-pending cap.
- **`/.well-known/oauth-protected-resource` advertises `manage`** in `scopes_supported`, but OAuth agents are never
  granted it; only a PAT can carry `manage`.

## Labels and handoffs

- **Handoff user messages, brief, goal and notes are free text.** They are not label-gated, so whatever is typed
  there is visible to every loader in the audience until the handoff expires or is revoked; gating arbitrary text is
  not possible.
- **Prompt quoting cannot stop a model from being persuaded by quoted data.** Delimiter escaping keeps data inside its
  block; it does not make a model ignore instructions it reads there. Chat tools are read-only and answers show their sources.
- **A prompt-injected answer can still show a URL that carries data.** External links in chat answers are plain text
  with the full URL (never clickable) and images never load, so nothing leaves on one click; a user who copies such a
  URL into the browser still sends whatever it encodes.
- **RAPTOR section prompts and the draft style check send the document's own text unquoted** (no delimiter or
  `prompt_quote.quote`). Scope is one document and the output stays with it, so injected text can at most distort
  that document's own section summaries or style findings.
- **`block_export` is a deterrent, not DRM**: it cannot stop copying, screenshots or printing of text a person may
  read inline, and the owner and interactive admins are exempt.
- **`exclude_llm` binds LLM context, not embeddings**: chunks are embedded regardless, and earlier summaries and chat
  answers are not rewritten.
- **Labels are handling markers, not a classification system**, and cannot protect regulated data on their own.

## Retrieval and label timing

- **Rerank can see just-revoked text during the `acl_sync` window**: Elasticsearch filters first and Postgres
  re-checks after, so the reranker may score text the caller can no longer read. The re-check drops it before return.
- **The reranker may score `exclude_llm` text**: it is a cross-encoder that returns scores only. Keep `ROR_RERANK_URL` on a trusted network.
- **Near-duplicate checks compare only against documents the uploader can see** (an agent upload: the agent view,
  `block_agents` excluded, never admin), so invisible duplicates can remain; anything else would leak existence. An
  ingest restarted after the original job was pruned (7 days) falls back to the restarting caller's view.
- **Totals and facet counts come from Elasticsearch only.** Rows are re-checked in Postgres, counts are not. Label and
  sharing changes write the index before they return, but when a change reaches Elasticsearch only through `acl_sync`
  (a near-duplicate merge raising the target's label, recovery after an ES error, a label backfill), counts and facet
  values can include a just-hidden document until the job runs.

## Platform and supply chain

- **Images are not minimal**: the backend is `python:3.12-slim` (a Debian base with a shell), run as UID 10001.
- **There is no vulnerability-scan stage and no STIG baseline**: use your registry's scanner and your own hardening profile.
- **Pod Security Admission enforcement is opt-in** (`pod_security.enforce` in the Terraform module), because the right level is a cluster policy choice.
- **Malware scanning is off when `ROR_CLAMAV_HOST` is empty** (e.g. `parsers.clamav.enabled=false` without an
  external host): uploads are parsed unscanned and evidence is served as `unscanned`.
- **The upload quota counts live documents and evidence files only**: soft-deleted papers stop counting at once, but
  their blobs stay until purge, so an upload/delete loop can hold more than the quota for `ROR_RETENTION_DELETED_DAYS`.
- **The OpenAPI schema is public**: `/api/openapi.json`, `/api/docs` and `/api/redoc` need no sign-in and list every
  route, admin routes included. The schema is also in the repository; no data is exposed.
- **The latex-builder is off by default** (`format: latex` builds return 501 until enabled). When enabled, TeX can still read any file inside its container (`openin_any=p` does not stop absolute reads in TeX Live), so the container must hold nothing secret ([latex-builder](../latex-builder.md#threat-model)).
- **Connector S3 endpoints are admin-chosen and not SSRF-filtered**: with an allowlisted internal CIDR the HTTP connector can read any HTTP service in that range ([threat model](threat-model.md#5-connectors)).
- **Network isolation depends on the CNI**: "no egress" for parser sandboxes holds only where `networkPolicy.enabled` is true and the cluster enforces NetworkPolicy.
- **Encryption at rest and TLS to data services are platform properties** (storage class, object store, operators); rOracle does not provide them.
