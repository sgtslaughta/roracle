# Security: threat model

Part of the [security architecture](../security.md). A STRIDE summary per trust boundary, then the detailed control
tables. Every control named here exists in code or in the chart; where a control is operator-supplied or only a
contract, it says so. Gaps are collected in [known limits](known-limits.md).

## 1. Assets and trust boundaries

| Asset | Where | Sensitivity |
|---|---|---|
| Documents (originals, derived PDFs, chunks, vectors) | object store, Elastic | Per-document visibility; may be export-controlled |
| Metadata, ACLs, chats, notes, drafts, audit log | Postgres | Reveals research activity |
| Credentials: JWT signing keys (Keycloak), PAT hashes, service secrets | Keycloak, Postgres, secret store | High |
| Evidence packages (agent-produced data, notebooks) | S3 object store | Untrusted content |

Boundaries: (1) user/agent -> org proxy (mTLS); (2) proxy -> ingress; (3) api/worker -> data services;
(4) api/worker -> inference endpoint; (5) worker -> parsing sidecars processing **untrusted files**.
The STRIDE tables below cut the same system into seven views: proxy and ingress, api, worker (untrusted content),
LLM prompt injection, agents and MCP, the latex-builder, and connectors.

STRIDE: **S**poofing, **T**ampering, **R**epudiation, **I**nformation disclosure, **D**enial of service,
**E**levation of privilege.

## 2. STRIDE per boundary

### 2.1 User or agent to proxy, ingress and web pod

Users present client certificates to the org proxy (operator-run); the proxy and ingress terminate TLS. rOracle
never sees the certificate, only the Keycloak JWT or a PAT.

| | Threat | Control |
|---|---|---|
| S | Forged identity headers or tokens | Services trust only bearer tokens: RS256 JWT checked against Keycloak JWKS (`iss`, `aud`, `exp`, `azp`), or a PAT looked up by SHA-256 hash. No identity header is read ([access control](access-control.md#2-authentication)) |
| T | Request tampering, smuggled bodies | TLS to the proxy is operator-owned. The api's input guard rejects NUL bytes (422) and oversized JSON (413) before routing |
| R | Denied actions | Admin, label, handoff, token, scope-denial, rate-limit and MCP events are audited; sign-in and search are not ([audit](audit.md#what-is-and-is-not-audited)) |
| I | Metrics, schema and error leaks | `/metrics` is cluster-internal: the ingress does not route it and the web nginx returns 404 for `/api/metrics`. It shares the api port (8000), so the monitoring namespace, the web pods and the ingress-controller namespace (`networkPolicy.ingressNamespaceSelector`) can reach it; `/metrics` itself is not authenticated ([network](network.md#11-networkpolicies)). `/readyz` returns only `ok`/`fail` per dependency. The OpenAPI schema (`/api/openapi.json`, `/api/docs`) is public ([known limits](known-limits.md#platform-and-supply-chain)) |
| D | Request floods | Per-token rate limit (`ROR_RATE_LIMIT_PER_MINUTE`, **off by default**, per replica); ingress-level limits are the operator's ([abuse](#4-abuse-and-rate-limits)) |
| E | Browser-side injection | The web pod sends a full CSP and related headers ([web headers](network.md#headers-sent-by-the-web-pod)); HSTS is set at the ingress or org proxy ([deployment guide](../deployment-guide.md#44-ingress)) |

### 2.2 API process

| | Threat | Control |
|---|---|---|
| S | Using someone else's PAT | PATs are shown once, stored only as SHA-256, expire (default 30 days, max 365), are revocable, and are rejected when the owner has not signed in within `ROR_PAT_MAX_IDENTITY_AGE_DAYS` |
| T | Changing data without permission | Every route resolves a principal; writes need a `write` scope (PAT) or a JWT, and sharing/ownership changes a `manage` scope; label, token and note routes are human-only (403 `agents must propose`) |
| R | Disputing an action | `audit_log` rows for the events listed in [audit events](../../reference/audit-events.md). Rows for failed requests are replayed after the rollback; the table has no database-level immutability |
| I | Seeing documents one may not | One SQL visibility function mirrored by the Elastic filter, then a Postgres re-check (R19) of every result; unreadable objects are 404, never 403; totals and facet counts come from the ACL-filtered Elasticsearch query, without the row re-check ([known limits](known-limits.md#retrieval-and-label-timing)) |
| D | Expensive requests | Upload size cap while streaming, `ROR_BUILD_CONCURRENCY` for draft builds (excess 503), admission caps on global and agentic answers, interactive embedding failures return 503 without retries |
| E | Agent gaining human or admin rights | An agent (PAT or `/mcp` caller) is never admin; `require_jwt` guards token routes (403 `personal access tokens cannot manage tokens`) and `require_human` the label, note and proposal routes (403 `agents must propose`); admin routes refuse PATs. Agents hold `read` ⊂ `write` ⊂ `manage`; sharing, visibility, delete, transfer, draft submit and co-author changes need `manage` (403 `insufficient scope: manage`, checked before any lookup) and `/mcp` callers never have it |

### 2.3 Worker: untrusted content (ClamAV, Docling sandbox)

Uploaded files and evidence are untrusted. The worker is the only caller of the parser sidecars.

| | Threat | Control |
|---|---|---|
| S | File named to look like something else | Filenames are metadata only; storage keys are SHA-256 content addresses |
| T | Parser exploit writes outside its sandbox | Parsers run in separate pods: non-root, `allowPrivilegeEscalation: false`, all capabilities dropped, read-only root filesystem with sized `emptyDir` writable paths, seccomp `RuntimeDefault` (chart defaults in `values.yaml`, `parsers.*`) |
| R | Unknown origin of a file | Documents record their owner and versions their content hash; deletes, transfers and uploads are audited |
| I | Parser reads secrets or exfiltrates | Parser pods have no secrets in env and **no egress**: the `parser-<name>` NetworkPolicies list `Egress` with no rules (ClamAV only reaches its freshclam mirror CIDRs when `parsers.clamav.mirrorUrl` is set) ([network](network.md)). This depends on `networkPolicy.enabled` (default true) and a CNI that enforces it |
| D | Zip bombs, huge or page-heavy files | Upload cap 500 MB by default (`ROR_MAX_UPLOAD_BYTES`) enforced while streaming; Docling limited by `DOCLING_SERVE_MAX_NUM_PAGES` (2000) and `DOCLING_SERVE_MAX_FILE_SIZE` (629145600); parser memory limits |
| E | Malware in the corpus | When ClamAV is configured (chart default; `ROR_CLAMAV_HOST` empty means no scan, see [known limits](known-limits.md#platform-and-supply-chain)): ClamAV scan before any parsing (`ingest`) and for evidence (`evidence_scan`); infected files are quarantined/not downloadable. **Fail closed**: `AlertExceedsMax yes`, limits at least the upload caps, readiness checks the effective limits, an over-limit stream is a non-retryable error ([secrets and TLS](secrets-tls.md#clamav-fail-closed-configuration)). Signatures come from `parsers.clamav.mirrorUrl`; without a mirror only the image's baked-in signatures exist |

### 2.4 LLM prompt injection (documents and notes as hostile input)

Retrieved text, community notes, chat history and agent-supplied text can carry instructions. The model call has
no tools that write data in the chat path.

| | Threat | Control |
|---|---|---|
| S | Injected text posing as a system or tool message | Retrieved text is placed inside tagged blocks (`<passage>`, `<note>`, `<unit>`, `<finding>`, `<question>`, `<found>`) and the prompt says it is quoted data. The system prompt is trusted; so is the structure outside the tags, and titles and authors are quoted too (`prompt_quote.meta`); a model can still be persuaded by quoted data ([known limits](known-limits.md#labels-and-handoffs)) |
| T | Text closes a delimiter and escapes the block | `prompt_quote.quote` (re-exported as `chat/answer.py::_quote`) rewrites every `</` (also `< /x >`, any tag name) to `<\/` and every opening of one of our tags (`<passage`, `<note`, `<unit`, `<finding`, `<question`, `<found`) to `<\tag`, case-insensitively. It runs over passage, note, unit (`chat/global_map.py`, `<unit id=...>`), finding (`chat/global_answer.py`, `<finding n=...>`), the document title in the global map and the finding titles in the global answer, the agentic planner's seen passages and the question. Document titles and author names go through `prompt_quote.meta` (whitespace-normalised, capped at 300 characters, then quoted) and sit inside `<source>` delimiters. **Residual:** quoting cannot stop a model from being persuaded by quoted data ([known limits](known-limits.md#labels-and-handoffs)). Exceptions: RAPTOR section prompts and the draft style check send the document's own text unquoted (single-document scope; the output stays with that document) |
| R | Which context produced an answer | Answers store citations and retrieved passages; handoffs and exports re-check them per reader |
| I | Injected text makes the model leak gated text or call out | Gated text never enters a prompt (`exclude_llm`, see [LLM gate coverage](access-control.md#llm-gate-coverage-exclude_llm-block_agents)). Chat answers never render images; same-site relative and fragment links stay links, but external http(s) URLs are shown as plain, non-clickable text with the full URL, so an injected URL cannot exfiltrate by image or by one click (a user who copies it deliberately still can: [known limits](known-limits.md#labels-and-handoffs)). Community notes reach the model only if written by the asker or upvoted at least `ROR_CHAT_NOTE_MIN_UPVOTES` with ratio above `ROR_CHAT_NOTE_MIN_RATIO`, in a separate labelled block, never numbered or citable; authors cannot vote on their own notes and editing a body resets its votes |
| D | Prompt stuffing | Per-document character budgets (`ROR_GLOBAL_DOC_CHARS`), question cap (600 characters) for the router and planner, retrieval and history limits |
| E | Injected instructions change what the system does | Chat tools are read-only; agents can only *propose* annotations and humans approve. **Residual:** wrong or manipulated answers are possible; the UI always shows sources and the passages read |

### 2.5 Agents and MCP

| | Threat | Control |
|---|---|---|
| S | Agent presenting a stolen PAT or OAuth agent token | PATs: hash-only storage, expiry, revocation, owner sign-in bound, per-token rate limit, `ROR_PATS_ENABLED` kill switch. OAuth agent tokens (`azp` in `ROR_OIDC_AGENT_CLIENTS`) are Keycloak JWTs: always `read`+`write` whatever OAuth scopes were granted, revoked in Keycloak (session or consent), not covered by `ROR_PATS_ENABLED` or the sign-in bound ([known limits](known-limits.md#agents-and-the-mcp-mount)) |
| T | Agent altering shared knowledge | Notes, labels and evidence need a human: agents create pending proposals that a person approves; the approver's rights apply. Sharing and ownership changes (visibility, shares, org unit, co-authors, delete, transfer, draft submit, chat shares, non-private uploads, handoffs beyond the creator) need the `manage` scope, which `/mcp` callers never hold (403 `insufficient scope: manage`). A `write` agent can still upload private papers, tag, vote, manage collections and chats, and create handoffs for itself; a caller without `manage` that omits visibility on upload gets `private`. Every such action leaves a `pat.use` or `mcp.<tool>` row |
| R | Which agent did what | `mcp.<tool>` rows (client name from the token) for tool calls; `pat.use` for REST calls, including failed ones (replayed after a rollback); 429 (`auth.rate_limited`, `mcp.rate_limited`) and scope denials (`auth.scope_denied`, REST and MCP) are audited too. 401s are counted and alerted, not audited per row |
| I | Agent reading gated documents | `block_agents` hides documents from every agent principal: PATs (admin PATs included), every `/mcp` caller (an OAuth/Keycloak token there is rewritten to a non-admin agent) and OAuth agent clients on REST. Near-duplicate checks of an agent upload run with that agent view; `exclude_llm` text is withheld from them. `block_export` applies to every non-owner non-admin including PATs, but is a deterrent |
| D | Agent hammering the API | Per-token bucket (`pat:<id>`; an OAuth agent token shares the user's `user:<id>` bucket), max 50 pending proposals per user and client (OAuth agents share `client` = their `azp`, [known limits](known-limits.md#agents-and-the-mcp-mount)), MCP bodies and upload size caps (`ROR_MCP_MAX_UPLOAD_BYTES`), the per-user upload quota |
| E | PAT becoming admin or minting tokens | A PAT is never admin; token management requires a JWT; labels are human-only |

### 2.6 latex-builder (optional, off by default)

`ROR_LATEX_BUILDER=disabled` is the default: `format: latex` builds return 501. The repository ships the service
(`deploy/latex-builder/`, enabled by setting the URL; details and the control table in [latex-builder](../latex-builder.md#threat-model)).
Markdown drafts do not use it: they go through pandoc and a Chromium Gotenberg
(row "Hostile draft Markdown" below).

| | Threat | Control |
|---|---|---|
| S | Source posing as trusted TeX | rOracle sends the draft text and bibtex; the service is treated as hostile-input handling |
| T | TeX writes files or runs commands | One-shot engine per request, `-no-shell-escape`, `openin_any=p`, `openout_any=p`, fresh empty work dir |
| R | Who built what | rOracle audits `draft.build`; logging inside a site-supplied builder is the site's |
| I | Reading files or the network | Read-only root filesystem, no secrets in env, deny-all egress, ingress only from the api |
| D | Infinite loops, huge output | Wall-clock (`timeout_s`, max 120), CPU, memory, work-dir size, 2 MiB request body and PDF size limits |
| E | Escape from the build container | Non-root, seccomp `RuntimeDefault`, no capabilities |

### 2.7 Connectors (SSRF, file reads)

Admins (never PATs) configure connectors. Details in [section 5](#5-connectors).

| | Threat | Control |
|---|---|---|
| S | Connector run impersonating a user | Connector documents are owned by the creating admin with the connector's visibility and label |
| T | Config changed in the database to widen access | Filesystem roots are re-checked at every run, not only at create/update |
| R | Who changed a connector | `admin.connector_create/update/delete` audited with secrets masked |
| I | SSRF to metadata or internal services; local file read | Connect-time IP vetting, no proxies, redirects re-vetted, private ranges blocked unless inside `ROR_CONNECTOR_HTTP_ALLOW_CIDRS`; metadata addresses never reachable; `realpath` allow-list, `O_NOFOLLOW`, regular files only. S3 endpoints are admin-chosen and **not** SSRF-filtered |
| D | Crawl bombs, slow servers | Size, time, page and depth caps; absolute per-request timeout; robots.txt honoured |
| E | A crawler reaching an internal service | Only allowlisted CIDRs; with an allowlisted internal range the connector can read any HTTP service in it |

## 3. Untrusted content handling

| Threat | Control |
|---|---|
| Malware in uploads/evidence | When ClamAV is configured (chart default): scan before any parsing; quarantine on hit; signature updates from mirror. With `ROR_CLAMAV_HOST` empty nothing is scanned and evidence is served as `unscanned` ([known limits](known-limits.md#platform-and-supply-chain)) |
| Parser exploits (PDF, Office, images) | Parsing only in Docling/GROBID/Gotenberg sidecars: non-root, read-only root FS, no egress, CPU/memory limits, seccomp `RuntimeDefault` |
| SSRF / local-file read via Office links (external rels, INCLUDEPICTURE/INCLUDETEXT, DDE) | Gotenberg runs in its own pods with a deny-all-egress NetworkPolicy (ingress only from the worker), no secrets in its env, read-only root FS, and `--api-disable-download-from --webhook-disable --chromium-disable-routes --pdfengines-disable-routes --api-timeout=300s`. Residual: a local-file include can copy files from the Gotenberg container itself into the uploader's PDF rendition; mitigated because that container holds no secrets. No code-side link sanitizing (bypass-prone) |
| Hostile draft Markdown reaching the PDF builder (raw HTML/TeX, remote images/CSS, script, Lua filters, pandoc file reads) | pandoc runs with `--sandbox` (no file/network access), raw HTML/TeX/attribute extensions off, no user filters or templates, empty env + PATH, fresh temp cwd, memory cap via the pandoc RTS (`-M2g`), `RLIMIT_CPU`/`RLIMIT_FSIZE` via prlimit (no fork hooks), one wall-clock deadline per build (bounds pandoc and the Gotenberg response stream; Gotenberg connect/write may overrun it by their per-operation timeout), stdout cap, `--data-dir` set to the empty temp dir, a per-process concurrency cap `ROR_BUILD_CONCURRENCY` (default 2; excess requests get 503 `build service busy`; restart to change; builds are CPU-heavy), nesting-depth bombs rejected with 422. Its JSON AST is re-written by an allow-list sanitizer: raw nodes dropped, images only `data:image/{png,jpeg,gif,webp};base64`, links only `http(s):`/`mailto:`/`#`, element attributes reduced to id + classes (no `style`/`on*`), document metadata discarded. The HTML starts with a CSP meta (`default-src 'none'; img-src data:; style-src 'unsafe-inline'; font-src data:`) and goes to a dedicated Chromium Gotenberg with deny-all egress; response size capped and `%PDF-` checked. Refs resolve under the caller's ACL. Math uses MathML (no JS) |
| Zip bombs / huge files | Upload size cap (default 500 MB) enforced while streaming; page-count and decompressed-size limits in parsers |
| Path traversal via filenames | Filenames are metadata only; storage keys are SHA-256 content addresses |
| XSS via HTML/Markdown documents and notes | No server-side sanitization: notes are stored as typed. Notes and answers are rendered client-side as GFM without raw HTML (react-markdown), links only through an allow-list (`safeHref`), images suppressed, KaTeX with `trust` off; originals are served as attachments (PDF and md/txt inline only as `application/pdf`/`text/plain` with `Content-Security-Policy: sandbox` and `nosniff`); strict CSP on the SPA (`script-src 'self'`); PDF.js renders without scripting |
| LaTeX code execution | Off by default (501). The shipped latex-builder runs one `latexmk -no-shell-escape` per request in a throwaway directory, with no egress, a read-only FS except tmpfs, no secrets and hard limits; TeX can still read files inside its own container ([latex-builder](../latex-builder.md#threat-model)) |
| Notebooks in evidence | Not rendered and never executed: stored and offered only as downloads |
| Evidence files | Never executed or rendered inline: served only as `attachment` with `X-Content-Type-Options: nosniff` and `Content-Security-Policy: sandbox`; ClamAV-scanned by the `evidence_scan` job, infected files are not downloadable |
| RO-Crate JSON-LD / provenance URIs | The `@context` is an identifier and is never fetched (air-gapped, no SSRF); crate metadata is generated by rOracle from validated fields; reference URIs must use `http(s)`, `s3`, `doi`, `git` or `ssh` schemes and are displayed, never dereferenced server-side |
| Prompt injection from documents | See section 2.4: quoted context with delimiter escaping, read-only chat tools, agents only propose |
| Shared chats | Sharing a chat (user or group) is read-only; recipients see answer text, but every citation and retrieved passage is re-checked against the reader's own document visibility on each read/export (hidden ones become "source not available"). Admins have no implicit access to others' chats, except that `/api/admin/ratings` shows every rated answer in full (text, model, chat id) |

### Web app Content-Security-Policy

The SPA ships a meta CSP limited to `img-src 'self' data: blob:` (so Vite dev keeps working). Production
nginx must send a full CSP header with at least: `default-src 'self'; img-src 'self' data: blob:;
script-src 'self'; style-src 'self' 'unsafe-inline'; font-src 'self'; connect-src 'self' <OIDC authority origin>;
frame-ancestors 'self'; base-uri 'self'; object-src 'none'` (`'self'`, not `'none'`: the OIDC silent-renew iframe needs it, see
[network](network.md#headers-sent-by-the-web-pod)). The header the chart's web pod actually sends is in
[network](network.md#headers-sent-by-the-web-pod). Chat answers never render images and render
external http(s) URLs as plain text with the full destination shown (never a clickable link); only same-site
relative and fragment links stay clickable. A prompt-injected document therefore cannot exfiltrate data through an
image fetch or a one-click link; copying a shown URL by hand remains possible.

## 4. Abuse and rate limits

PAT misuse detection (alerts on use from a new client or burst rates) is **not implemented**. At most 50 agent
proposals per user and client can be pending (429 `too many pending proposals`).

**Per-token rate limit** (`ROR_RATE_LIMIT_PER_MINUTE`, runtime-adjustable, **default `0` = off**). When set, every authenticated REST and
MCP request takes one token from an in-process token bucket: capacity = the limit, refilled continuously at
limit/60 per second. The bucket key is `pat:<token id>` for personal access tokens and `user:<id>` for Keycloak
JWTs, so a user's PAT and browser session have separate budgets. Over the limit the API answers `429
{"detail":"rate limited"}` with `Retry-After` (seconds). Unauthenticated requests are not counted (they fail with
401); `/healthz`, `/readyz` and `/metrics` are unauthenticated and therefore never limited. 429 responses are audited as `auth.rate_limited` / `mcp.rate_limited`, at most one row per principal per minute.

The bucket lives in each API replica's memory (idle buckets are evicted after 10 min). With N replicas the
effective cluster-wide limit is about N x the configured value, and a restart resets it. For a hard global cap or
IP-level protection also set limits on the ingress (e.g. nginx `limit-rps`/`limit-connections`).

**Input guard** (`api/input_guard.py`, ASGI middleware before routing). NUL characters (raw, `%00` or JSON `\u0000`) in the query string or a JSON body answer `422` (Postgres cannot store them). JSON bodies are capped at 3 MiB or, when `ROR_DRAFT_MAX_BYTES` is raised, at 3 x that plus 64 KiB, whichever is larger (`413`); `/mcp` (own cap) and multipart/octet-stream uploads are not buffered by it. Upload streaming and the `tmp/` lifecycle: [data retention](data-retention.md#uploads-and-tmp).

## 5. Connectors

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
  (`GET /api/admin/review`).
- Connector documents are matched for exact duplicates only against the same connector's own documents, so a
  user's private document is never referenced by (or revealed through) a connector. They are exempt from the
  per-user upload quota (`ROR_UPLOAD_QUOTA_BYTES` counts user document uploads and evidence files). Changing a connector's
  visibility or org unit updates its existing documents and re-syncs their search ACLs.
- S3 endpoints must be http(s) URLs without embedded credentials.
- **HTTP crawl (SSRF controls).** Admin-chosen URLs make the server fetch, so the fetch path is locked down:
  - Only `http`/`https` URLs without embedded credentials (`file:`, `ftp:`, etc. rejected at create and at every
    fetch and redirect hop).
  - **Connect-time IP vetting.** Name resolution happens once, inside the socket connect: every address the
    name resolves to is checked (one bad address blocks the host: fail closed) and the connection goes to a
    vetted IP literal. There is no separate pre-check lookup, so DNS rebinding cannot swap the address. TLS
    still verifies the certificate against the URL host. Encodings such as `0x7f.1` and decimal IPs are judged on the resolved
    address. IPv4-mapped IPv6 (`::ffff:a.b.c.d`) is judged by the IPv4 it
    wraps. Other translator/tunnel forms (NAT64 `64:ff9b::/96` and `64:ff9b:1::/48`, 6to4, Teredo, `::/96`) are
    blocked unless an allowlisted **/96** translator prefix covers them (NAT64, `::/96`; any other IPv6 CIDR
    overlapping those ranges is a startup error, and 6to4 `2002::/16` and Teredo `2001::/32` can never be allowlisted); the low 32 bits are then judged as an IPv4 (never-list, public or allowlisted).
    NAT64-only networks therefore allowlist their translator prefix (e.g. `64:ff9b::/96`) plus the IPv4 ranges
    they need.
  - Anything not globally routable (private, loopback, CGNAT, ULA, reserved) is blocked unless inside an
    explicit `ROR_CONNECTOR_HTTP_ALLOW_CIDRS` entry (for air-gapped internal crawling; empty by default).
    `::1` cannot be allowlisted (use IPv4 `127.0.0.0/8` for local test crawls); an allowlisted translator CIDR must be the
    translator's real RFC 6052 `/96` prefix (`/32`-`/64` NAT64 layouts are unsupported).
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
- At the network layer the worker may reach only `connectors.egressCIDRs` on `connectors.ports` (default 443,
  no CIDRs): [network](network.md#11-networkpolicies).

## 6. Residual risks and review items

- `/metrics` (api and worker) is unauthenticated and exposes job counts per kind, route templates and status codes (no user data, no document identifiers). It is not routed by the ingress or the web proxy, but the api port is reachable from the web pods and the ingress-controller namespace (see section 2.1).
- LLM answers can be wrong despite citations; UI always shows sources and passages read.
- Near-duplicate prompts compare only against documents the uploading principal can see (an agent upload: the
  agent view, never admin); cross-tenant near-dup information never surfaces, which can leave invisible duplicates
  in the corpus (accepted).
- Blob object written before the DB commit can orphan on failure; blob GC removes objects without a `blobs` row
  or references ([data retention](data-retention.md)).
- The rest: [known limits](known-limits.md).
