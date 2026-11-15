# rOracle Security Architecture

Audience: security reviewers, ISSOs, platform engineers. Companion to the
[deployment guide](deployment-guide.md). Decisions: ADR-0002, 0004, 0008, 0010, 0011.

## 1. Assets and trust boundaries

| Asset | Where | Sensitivity |
|---|---|---|
| Documents (originals, derived PDFs, chunks, vectors) | MinIO, Elastic | Per-document visibility; may be export-controlled |
| Metadata, ACLs, chats, notes, drafts, audit log | Postgres | Reveals research activity |
| Credentials: JWT signing keys (Keycloak), PAT hashes, service secrets | Keycloak, Postgres, secret store | High |
| Evidence packages (agent-produced data, notebooks) | MinIO | Untrusted content |

Boundaries: (1) user/agent → org proxy (mTLS); (2) proxy → ingress; (3) api/worker → data services;
(4) api/worker → inference endpoint; (5) worker → parsing sidecars processing **untrusted files**.

## 2. Authentication

- Users present client certificates to the org proxy; Keycloak's X.509 authenticator maps the cert
  to a user and issues an RS256 JWT (≤ 5 min access token lifetime recommended).
- rOracle validates `iss`, `aud=roracle`, `exp`, signature against Keycloak JWKS. Unknown `kid` →
  one JWKS refetch, then reject (handles key rotation, prevents refetch amplification).
- rOracle services **never** trust identity headers; only tokens.
- PATs (agents): 32 random bytes, shown once, stored as SHA-256 hash with a server pepper, mandatory
  expiry (default 30 days, max 365), scopes `read` / `write`, revocable, globally disableable, every use
  audited with client name.

## 3. Authorization

- Visibility per document: `private`, `shared` (users/groups), `org_unit` (owning Keycloak group
  path and descendants), `everyone` (all authenticated users). Admins (`roracle-admin` realm role)
  see all and are audited.
- One function defines visibility in SQL; the Elastic filter mirrors it (ADR-0002). Both have
  contract tests including negative ("must not see") cases.
- **No existence leaks**: duplicate detection reveals a match only when the user can already see it;
  search facets/counts are computed from ACL-filtered results; handoff loads redact undisclosed
  documents and say only how many were withheld; links to documents a user cannot see show a
  request-access page with no title or metadata.
- MCP tools run as the token's user; no tool bypasses the ACL path.

## 4. Untrusted content handling

| Threat | Control |
|---|---|
| Malware in uploads/evidence | ClamAV scan before any parsing; quarantine on hit; signature updates from mirror |
| Parser exploits (PDF, Office, images) | Parsing only in Docling/GROBID/Gotenberg sidecars: non-root, read-only root FS, no egress, CPU/memory limits, seccomp `RuntimeDefault` |
| Zip bombs / huge files | Upload size cap (default 500 MB) enforced while streaming; page-count and decompressed-size limits in parsers |
| Path traversal via filenames | Filenames are metadata only; storage keys are SHA-256 content addresses |
| XSS via HTML/Markdown documents and notes | Server-side sanitization (allowlist), strict CSP on the SPA, PDF.js renders without scripting |
| LaTeX code execution | `latex-builder`: no shell-escape, no network, read-only FS except scratch, 120 s / 2 GB limits, one build per pod |
| Notebooks in evidence | Rendered statically; never executed |
| Prompt injection from documents | Retrieved text is passed as quoted context, never as instructions; tools available to chat are read-only; agents can only *propose* annotations — humans approve |

## 5. Data protection

- TLS everywhere in transit (ingress, service mesh or per-service TLS to Postgres/Elastic/MinIO).
- Encryption at rest via storage class / MinIO SSE (KMS-backed where available).
- Soft delete → purge after retention (default 30 days); purge removes chunks, vectors, derived files,
  and blob objects at refcount 0. Backups age out per backup policy.
- Inference endpoint receives only the minimum context (retrieved passages, the question); no
  documents leave the enclave.

## 6. Audit

Append-only `audit_log` table (who, when, action, target, client, request id): sign-in, search,
document view/download, chat question (text hash + cited doc ids), handoff create/load, proposal
approve/reject, share/visibility change, admin actions, PAT create/use/revoke. Ship to the SIEM via
Loki/OTel. Admin viewer with filters; retention per policy.

## 7. Platform hardening

- Images: minimal bases, non-root UID 10001, no shells in production images where possible, SBOM
  (CycloneDX) and vulnerability scan in CI, signed with cosign; STIG-aligned baselines.
- Kubernetes: Pod Security `restricted`, NetworkPolicies default-deny, no service-account token
  automount except where needed, secrets from the platform secret manager.
- Dependencies come only from internal mirrors; lock files are committed (`uv.lock`, `pnpm-lock.yaml`).

## 8. Abuse and rate limits

Per-user rate limits on search, chat, upload, MCP calls; max 50 pending agent proposals per session;
handoff loads audited and rate-limited; PAT misuse alerts (use from new client, burst rate).

## 9. Residual risks / review items

- LLM answers can be wrong despite citations; UI always shows sources and passages read.
- Near-duplicate prompts compare only against documents the user can see; cross-tenant near-dup
  information never surfaces, which can leave invisible duplicates in the corpus (accepted).
- Blob object written before the DB commit can orphan on failure; a GC job removes objects without
  a `blobs` row (plan 2).
