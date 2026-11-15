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
- PATs (agents): `ror_pat_` + 32 random bytes, shown once, stored only as SHA-256 hash, mandatory
  expiry (default 30 days, max 365), scopes `read` / `write`, revocable, globally disableable
  (`ROR_PATS_ENABLED`). Token management requires an interactive JWT (a PAT cannot mint PATs).
  - A PAT is **never admin**, even for an admin owner.
  - Groups = snapshot at creation intersected with the owner's current groups: access can narrow
    but never widen after minting.
  - Owner sign-in bound: a PAT is rejected (401 "token owner session stale") unless its owner
    signed in via JWT within `ROR_PAT_MAX_IDENTITY_AGE_DAYS` (default 30). This bounds how long a
    disabled/removed Keycloak user keeps PAT access.
  - Admins revoke all of a user's PATs at once: `DELETE /admin/users/{id}/pats`.
  - `last_used_at` is updated at most once a minute and never blocks requests.

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
- **Notes (annotations)**: readable only if the note is not `removed` (`hidden` only for its author, the
  document owner and admins), its document passes the document ACL (non-owner non-admins also need a
  `searchable` version), any relation target does too, and the reader is the author/an admin or the note
  is `everyone`, or `shared` with them or one of their groups. Replies inherit their root's visibility.
  Anything unreadable is a 404. Community notes attached to search hits and chat passages are
  `everyone` root notes on the document's latest searchable version only (score >= 0.4, < 3 reports,
  ≤ 2 per document, ≤ 6 per response). Notes stored with a chat answer are re-checked per reader on every
  read (fail closed; handoffs show them as unavailable).
- **Agents only propose**: every notes/proposal/evidence write REST route requires a Keycloak JWT
  (PATs get 403 `agents must propose`). PATs reach notes only through MCP `propose_annotation` and
  `reply_to_annotation`, which create pending proposals (default `private`; a requested visibility applies
  only after a human approves).

## 4. Untrusted content handling

| Threat | Control |
|---|---|
| Malware in uploads/evidence | ClamAV scan before any parsing; quarantine on hit; signature updates from mirror |
| Parser exploits (PDF, Office, images) | Parsing only in Docling/GROBID/Gotenberg sidecars: non-root, read-only root FS, no egress, CPU/memory limits, seccomp `RuntimeDefault` |
| SSRF / local-file read via Office links (external rels, INCLUDEPICTURE/INCLUDETEXT, DDE) | Gotenberg runs in its own namespace with a deny-all-egress NetworkPolicy (ingress only from the worker), no secrets in its env, read-only root FS, and `--api-disable-download-from --webhook-disable --chromium-disable-routes --pdfengines-disable-routes --api-timeout=300s`. Residual: a local-file include can copy files from the Gotenberg container itself into the uploader's PDF rendition; mitigated because that container holds no secrets. No code-side link sanitizing (bypass-prone) |
| Zip bombs / huge files | Upload size cap (default 500 MB) enforced while streaming; page-count and decompressed-size limits in parsers |
| Path traversal via filenames | Filenames are metadata only; storage keys are SHA-256 content addresses |
| XSS via HTML/Markdown documents and notes | Server-side sanitization (allowlist), strict CSP on the SPA, PDF.js renders without scripting |
| LaTeX code execution | `latex-builder`: no shell-escape, no network, read-only FS except scratch, 120 s / 2 GB limits, one build per pod |
| Notebooks in evidence | Not rendered and never executed: stored and offered only as downloads |
| Evidence files | Never executed or rendered inline: served only as `attachment` with `X-Content-Type-Options: nosniff` and `Content-Security-Policy: sandbox`; ClamAV-scanned by the `evidence_scan` job, infected files are not downloadable |
| RO-Crate JSON-LD / provenance URIs | The `@context` is an identifier and is never fetched (air-gapped, no SSRF); crate metadata is generated by rOracle from validated fields; reference URIs must use `http(s)`, `s3`, `doi`, `git` or `ssh` schemes and are displayed, never dereferenced server-side |
| Prompt injection from documents | Retrieved text is passed as quoted context, never as instructions; tools available to chat are read-only; agents can only *propose* annotations — humans approve. Community notes are user-written and so untrusted: notes reach an LLM (chat, MCP `ask`, MCP `search`) only if written by the asking user or upvoted ≥ `ROR_CHAT_NOTE_MIN_UPVOTES` times with ratio > `ROR_CHAT_NOTE_MIN_RATIO`; they are placed in a separate labelled block, never numbered or citable, and the system prompt tells the model not to follow them. Authors cannot vote on their own notes (and such votes are ignored), and editing a note's body resets its votes |
| Shared chats | Sharing a chat (user or group) is read-only; recipients see answer text, but every citation and retrieved passage is re-checked against the reader's own document visibility on each read/export (hidden ones become "source not available"). Admins have no implicit access to others' chats; ratings are visible to admins via `/admin/ratings` |

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
approve/reject, share/visibility change, admin actions, PAT create/revoke, `mcp.<tool>` for every
MCP tool call (MCP PAT use is audited this way, REST PAT use as `pat.use` with method and path; failures carry an `error` detail; schema-validation rejections are not audited). Ship to the SIEM via
Loki/OTel. Admin viewer with filters; retention per policy.

## 7. Platform hardening

- Images: minimal bases, non-root UID 10001, no shells in production images where possible, SBOM
  (CycloneDX) and vulnerability scan in CI, signed with cosign; STIG-aligned baselines.
- Kubernetes: Pod Security `restricted`, NetworkPolicies default-deny, no service-account token
  automount except where needed, secrets from the platform secret manager.
- Dependencies come only from internal mirrors; lock files are committed (`uv.lock`, `pnpm-lock.yaml`).

## 8. Abuse and rate limits

Handoff loads are audited. Planned: per-user rate limits on search, chat, upload and MCP calls and
rate-limited handoff loads (P11); PAT misuse alerts, i.e. use from a new client or burst rate (P11);
max 50 pending agent proposals per session (P8).

## 9. Connectors

Admins (never PATs) configure connectors that ingest from S3-compatible buckets, mounted filesystems and HTTP sites.

- **Filesystem allowlist.** Paths are accepted only if their `realpath` equals or is under a root in
  `ROR_CONNECTOR_FS_ROOTS` (empty = disabled); checked at create/update and again at every run, so a
  config changed in the database cannot widen access. The walk never follows symlinks (`..`, symlinked
  directories and symlinked files are skipped); files are opened relative to their directory descriptor with
  `O_NOFOLLOW` and must be regular files (no FIFOs or devices), dot-files and dot-directories are skipped.
  Mount sources read-only; a writer who can swap a parent directory of the root during a run is outside the
  guarantee.
- Hardlinks are not detected: a hardlink inside a root to a file outside it reads like any regular file.
  Keep roots on a dedicated read-only mount so no such links can exist.
- **S3 credentials** are stored in the connector's database row and are write-only through the API
  (`secret_key` reads back as `***`; sending `***` on update keeps the stored value; audit entries are
  redacted). S3 endpoints are admin-chosen and not SSRF-filtered. Without keys the client is unsigned (public buckets).
- **Ownership.** Connector documents are owned by the admin who created the connector, with the connector's
  visibility. Versions that look like near-duplicates park in review and surface in the admin review queue
  (`GET /admin/review`).
- Connector documents are matched for exact duplicates only against the same connector's own documents, so a
  user's private document is never referenced by (or revealed through) a connector. They are exempt from the
  per-user upload quota (`ROR_UPLOAD_QUOTA_BYTES` applies to `POST /documents` only). Changing a connector's
  visibility or org unit updates its existing documents and re-syncs their search ACLs.
- S3 endpoints must be http(s) URLs without embedded credentials.
- **HTTP crawl (SSRF controls).** Admin-chosen URLs make the server fetch, so the fetch path is locked down:
  - Only `http`/`https` URLs without embedded credentials (`file:`, `ftp:`, etc. rejected at create and at every
    fetch and redirect hop).
  - **Connect-time IP vetting.** Name resolution happens once, inside the socket connect: every address the
    name resolves to is checked (one bad address blocks the host: fail closed) and the connection goes to a
    vetted IP literal. There is no separate pre-check lookup, so DNS rebinding cannot swap the address. TLS
    still verifies the certificate against the URL host. Encodings such as `0x7f.1`, decimal IPs, IPv4-mapped
    (`::ffff:a.b.c.d`), 6to4, Teredo and NAT64 (`64:ff9b::/96`, `64:ff9b:1::/48`) forms are judged as the IPv4 they wrap.
  - Anything not globally routable (private, loopback, CGNAT, ULA, reserved) is blocked unless inside an
    explicit `ROR_CONNECTOR_HTTP_ALLOW_CIDRS` entry (for air-gapped internal crawling; empty by default).
    Link-local (incl. `169.254.169.254` metadata), multicast, unspecified and the other known metadata
    addresses (`fd00:ec2::254`, `100.100.100.200`, `168.63.129.16`, `192.0.0.192`) are never reachable, allowlist
    or not.
  - No proxies: proxy environment variables are ignored. Redirects are followed manually (max 5), each hop
    re-vetted; a crawl keeps only content whose final URL is on the seed's origin.
  - Caps: per-file size (`max_upload_bytes`, checked on `Content-Length` and while streaming), per-request time
    (`ROR_CONNECTOR_HTTP_TIMEOUT_S`, absolute: every connect/TLS/read/write is clamped to it, so slow headers or a
    dripping body cannot outlast it); responses with a `Content-Encoding` are skipped; the crawl frontier never
    exceeds the page budget, robots.txt 512 KiB, HTML link parsing 5 MiB, <= 1000 pages and depth <= 3
    per run, plus the run item/time caps.
  - `robots.txt` is honoured by default (`respect_robots`): 2xx is parsed, 5xx disallows the whole origin
    (RFC 9309), 4xx or a network error/oversize file allows everything (advisory control, fail open); the crawl is same-origin per seed (scheme, host,
    port) and sends `User-Agent: rOracle-connector/<version>`.
  - Accepted risk: `getaddrinfo` has no application-level timeout (bounded by the resolver's `resolv.conf`
    timeout/attempts), so a hanging DNS server can delay a fetch beyond its deadline by that bound.
  - Residual: with an allowlisted internal CIDR, the connector can read any HTTP service in that range;
    allowlist only ranges meant to be crawled.
- Failed or partial runs notify every admin (`ops.connector_run`); run logs keep only the last 500 lines.

## 10. Residual risks / review items

- LLM answers can be wrong despite citations; UI always shows sources and passages read.
- Near-duplicate prompts compare only against documents the user can see; cross-tenant near-dup
  information never surfaces, which can leave invisible duplicates in the corpus (accepted).
- Blob object written before the DB commit can orphan on failure; a GC job removes objects without
  a `blobs` row (plan 2).
