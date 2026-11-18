# Security: known limits

Part of the [security architecture](../security.md). These are limits the project accepts, each with its reason. They
are behaviours of the shipped design, not open defects. For what the controls guarantee, see
[access control](access-control.md), [audit](audit.md) and the [threat model](threat-model.md).

## Audit

- **The audit log has no database-level immutability and no retention**: the application only inserts, and enforcing
  write-once storage and retention belongs to your database and SIEM. ([Audit](audit.md), [data retention](data-retention.md#audit-log).)
- **401 responses are counted and alerted, not audited per row**, so unauthenticated traffic cannot fill the log.
- **Rate-limit denial rows are throttled to one per principal per minute**, for the same reason.
- **Audit replay rows store the user id in `detail`, not `user_id`**, because the replay runs outside the request's
  transaction and its user row may be gone.
- **Sign-in, search, general document views and downloads, and chat questions are not audited**: volume and privacy
  cost outweigh the value; only `label.view` on `block_export` documents is recorded.

## Agents and the MCP mount

- **PAT group removal applies at the owner's next JWT-authenticated request**: the token's groups are a snapshot
  intersected with the owner's stored groups. Revoke tokens (`DELETE /api/admin/users/{id}/pats`) for immediate effect.
- **Per-token rate limiting is off by default and per API replica**: a process-local bucket needs no shared state. Add
  ingress limits for a global cap.
- **There is no PAT misuse detection beyond the alerts**: new-client and burst analytics are left to your SIEM.

## Labels and handoffs

- **Handoff user messages, brief, goal and notes are free text.** They are not label-gated, so whatever is typed
  there is visible to every loader in the audience until the handoff expires or is revoked; gating arbitrary text is
  not possible.
- **Prompt quoting cannot stop a model from being persuaded by quoted data.** Delimiter escaping keeps data inside its
  block; it does not make a model ignore instructions it reads there. Chat tools are read-only and answers show their sources.
- **`block_export` is a deterrent, not DRM**: it cannot stop copying, screenshots or printing of text a person may
  read inline, and the owner and interactive admins are exempt.
- **`exclude_llm` binds LLM context, not embeddings**: chunks are embedded regardless, and earlier summaries and chat
  answers are not rewritten.
- **Labels are handling markers, not a classification system**, and cannot protect regulated data on their own.

## Retrieval and label timing

- **Rerank can see just-revoked text during the `acl_sync` window**: Elasticsearch filters first and Postgres
  re-checks after, so the reranker may score text the caller can no longer read. The re-check drops it before return.
- **The reranker may score `exclude_llm` text**: it is a cross-encoder that returns scores only. Keep `ROR_RERANK_URL` on a trusted network.
- **Near-duplicate checks compare only against documents the caller can see**, so invisible duplicates can remain; anything else would leak existence.

## Platform and supply chain

- **Images are not minimal**: the backend is `python:3.12-slim` (a Debian base with a shell), run as UID 10001.
- **There is no vulnerability-scan stage and no STIG baseline**: use your registry's scanner and your own hardening profile.
- **Pod Security Admission enforcement is opt-in** (`pod_security.enforce` in the Terraform module), because the right level is a cluster policy choice.
- **The latex-builder is not shipped**: `format: latex` builds return 501, and a site that implements it owns its isolation ([contract](../latex-builder.md)).
- **Connector S3 endpoints are admin-chosen and not SSRF-filtered**: with an allowlisted internal CIDR the HTTP connector can read any HTTP service in that range ([threat model](threat-model.md#9-connectors)).
- **Network isolation depends on the CNI**: "no egress" for parser sandboxes holds only where `networkPolicy.enabled` is true and the cluster enforces NetworkPolicy.
- **Encryption at rest and TLS to data services are platform properties** (storage class, object store, operators); rOracle does not provide them.
