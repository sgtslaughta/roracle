# rOracle Security Architecture

Audience: security reviewers, ISSOs, platform engineers. Companion to the
[deployment guide](deployment-guide.md). Decisions: ADR-0002, 0004, 0008, 0010, 0011, 0014, 0015.
Terms are in the [glossary](../glossary.md). This page is the index: the detail lives in `security/`.

> Labels are handling markers enforced inside rOracle, not a classification system. `block_export` is a deterrent,
> not DRM. Read the [known limits](security/known-limits.md) before relying on any control.

| Page | Covers |
|---|---|
| [Threat model](security/threat-model.md) | Assets, trust boundaries, STRIDE per boundary, untrusted content, prompt injection, rate limits, connectors and SSRF |
| [Access control](security/access-control.md) | Authentication, PATs, authorization, labels and gates, what is and is not guaranteed |
| [Audit](security/audit.md) | What the log guarantees and what it misses; the event list is the [audit events reference](../reference/audit-events.md) |
| [Secrets and TLS](security/secrets-tls.md) | Terraform secrets module, PAT hashing, TLS trust and rotation, ClamAV fail-closed |
| [Network](security/network.md) | Every NetworkPolicy, web headers, pod baseline |
| [Data retention](security/data-retention.md) | Document deletion cascade, blob GC, tmp lifecycle, handoff expiry, audit retention |
| [Known limits](security/known-limits.md) | Behaviours reviewers should know about |

Related: [access control data flow](../architecture/access-control.md), [MCP reference](../reference/mcp.md),
[API reference](../reference/api.md), [admin audit viewer](../admin/audit.md), [observability](observability.md),
[backup and DR](backup-dr.md).

## 1. Assets and trust boundaries

Documents and derived data (object store, Elasticsearch), metadata, ACLs, chats, notes, drafts and the audit log
(Postgres), credentials (Keycloak keys, PAT hashes, service Secrets) and evidence packages. Five boundaries:
user/agent to the org proxy, proxy to ingress, api/worker to data services, api/worker to the inference endpoint,
and worker to the parsing sidecars that process untrusted files. See the
[threat model](security/threat-model.md#1-assets-and-trust-boundaries).

## 2. Authentication

Keycloak RS256 JWTs behind an mTLS proxy; services never trust identity headers. PATs (`ror_pat_...`) are hash-only, expire
(default 30 days, max 365), are never admin and need an owner who signed in within `ROR_PAT_MAX_IDENTITY_AGE_DAYS`.
[Details](security/access-control.md#2-authentication).

## 3. Authorization

One SQL visibility function, mirrored by the Elasticsearch filter and re-checked in Postgres (R19); unreadable is 404.
Agents only propose notes and labels, but a write-scope PAT can use every route that has no human-only guard: submit drafts, change a document's visibility or shares, delete or transfer it, upload, tag, share chats ([known limits](security/known-limits.md#agents-and-the-mcp-mount)). Handoffs are frozen at
creation and re-checked on every load. [Details](security/access-control.md#3-authorization).

## 4. Untrusted content handling

ClamAV before parsing (fail closed), parsers in no-egress sandboxes, sanitised drafts, evidence served as sandboxed
downloads, quoted-context prompts with delimiter escaping. [Details](security/threat-model.md#3-untrusted-content-handling).

## 5. Data protection

TLS and encryption at rest come from the platform; the inference endpoint gets only the minimum context.
[Secrets and TLS](security/secrets-tls.md#5-data-protection-in-transit-and-at-rest); deletion and retention in
[data retention](security/data-retention.md).

## 6. Audit

An insert-only (by application code) `audit_log` table. Not database-immutable, no retention, and rows roll back with
a failed request; sign-in and search are not audited. [Details](security/audit.md#6-audit) and the
[event list](../reference/audit-events.md).

## 7. Platform hardening

Non-root, seccomp, dropped capabilities, read-only root filesystems, default-deny NetworkPolicies.
[Details](security/network.md#7-platform-hardening).

## 8. Abuse and rate limits

Per-token token bucket (off by default, per replica), input guard, upload caps. [Details](security/threat-model.md#4-abuse-and-rate-limits).

## 9. Connectors

Filesystem allow-list, connect-time IP vetting and `ROR_CONNECTOR_HTTP_ALLOW_CIDRS` for the HTTP crawler.
[Details](security/threat-model.md#9-connectors).

## 10. Residual risks and review items

[Threat model](security/threat-model.md#10-residual-risks-and-review-items) and [known limits](security/known-limits.md).

## 11. Data-tier network and credential controls (Helm chart)

[NetworkPolicies](security/network.md#11-networkpolicies) for the app and data tiers; least-privilege Elasticsearch and
object-store identities in [secrets and TLS](security/secrets-tls.md#least-privilege).

## 12. Web tier, pod baseline and secrets (Helm chart)

[Web headers and pod baseline](security/network.md#12-web-tier-and-pod-baseline); [secrets handling](security/secrets-tls.md#secrets-handling),
[TLS trust](security/secrets-tls.md#tls) and [ClamAV](security/secrets-tls.md#clamav-fail-closed-configuration).

## 13. Sensitivity labels

Up to 20 ranked labels with four gates: `block_agents`, `exclude_llm`, `block_export`, `reason_on_lower`. Gates only
restrict. Full text, gate table and who may change a label:
[access control](security/access-control.md#13-sensitivity-labels). Design: [ADR-0014](../adr/0014-sensitivity-labels.md).

## 14. Retrieval-quality surfaces and the LLM gate

LLM gate coverage per path, no cross-document artefacts, new endpoints and the perf tooling:
[access control](security/access-control.md#14-retrieval-quality-surfaces-and-the-llm-gate).
