# HTTP API guide

For integrators and agent developers. Request and response schemas are in [openapi.json](openapi.json) and are not repeated here. Failures are covered in [Errors](errors.md). Terms such as label and visibility are in the [glossary](../glossary.md).

Examples use `https://roracle.example.internal` and a token in `$TOKEN`. Routes are written as clients call them, with the `/api` prefix. The paths inside `openapi.json` omit that prefix.

## Versioning

- Every REST route is served under `/api`. There is no version segment in the path.
- The running version is the `version` field of `GET /healthz` on the API (through the public address that is `GET /api/healthz`; `/healthz` there is the web pod's plain `ok`) (`{"status": "ok", "version": "..."}`) and the `info.version` of the OpenAPI document. It is the `ROR_VERSION` environment variable if set, else the repository `VERSION` file, else the installed package version.
- Releases use semantic versioning. Check the [OpenAPI schema](openapi.json) of the release you run before depending on a field.
- `/mcp` is served without the `/api` prefix. The API pod serves `/healthz`, `/readyz` and `/metrics` at its root; through the public address only `/api/healthz` (liveness and version) is reachable. `/readyz` and `/metrics` are in-cluster only, and `/api/readyz` and `/api/metrics` return 404 at the web proxy.

## Authentication

Send `Authorization: Bearer <token>` on every request. The API is stateless and reads no cookies, so there is no CSRF token. Two kinds of bearer token are accepted.

| | Keycloak JWT | Personal access token (PAT) |
|---|---|---|
| Looks like | RS256 access token | `ror_pat_` followed by a random string |
| Used by | The web app and people | Agents, scripts, MCP clients |
| Identity | The signed-in user | The user who minted it, as an agent |
| Scopes | Always `read`, `write` and `manage` (implicit, not taken from token claims) | `read`; `read` and `write`; or `read`, `write` and `manage`, set at creation |
| Admin | Yes, with the `roracle-admin` realm role | Never, even for an admin owner |

**JWT checks.** The signature is verified against the identity provider's signing keys. The `exp`, `iss`, `aud` and `sub` claims are required. `iss` must equal `ROR_OIDC_ISSUER`, `aud` must equal `ROR_OIDC_AUDIENCE` (default `roracle`), the token type must be `Bearer`, and the `azp` client must be in `ROR_OIDC_ALLOWED_CLIENTS` (default `roracle-web`, `roracle-mcp`). Clock skew of 30 seconds is tolerated. Groups and the admin flag are read from the token on every request. Any failure is the same `401`. Identity provider setup is in [Keycloak setup](../enterprise/keycloak-setup.md).

**PAT checks.** A PAT works only while `ROR_PATS_ENABLED` is on. It must be unexpired and not revoked. Its owner must have signed in within `ROR_PAT_MAX_IDENTITY_AGE_DAYS` (default 30), otherwise the call fails with `401 token owner session stale`. A PAT carries a snapshot of the owner's groups from creation, intersected with the groups stored at the owner's last JWT request (sign-in or app use); a group removal reaches the token only then. The secret is shown once, at creation, and only its hash is stored. Each successful REST call made with a PAT is recorded in the audit log as `pat.use`. Rejected or failed calls (rate limited, insufficient scope, handler errors) are not, because the row is committed only with a successful request. Calls to `/mcp` are audited as `mcp.<tool>` instead.

**Scopes.** A PAT without `write` is limited to GET, HEAD and OPTIONS. Anything else gives `403 insufficient scope`. `manage` is also needed to change sharing (`visibility`, `org_unit`, `share_users`, `share_groups`, `coauthors`), delete or transfer a document, submit a draft, delete a draft that was already published, share a chat, or create or widen a handoff beyond `creator`; without it: `403 insufficient scope: manage`. A JWT of the web app has all three scopes; a JWT of an agent OAuth client listed in `ROR_OIDC_AGENT_CLIENTS` is an agent and never has `manage`.

**What a PAT cannot do.**

- Manage tokens: `/api/me/tokens` needs a JWT, so a leaked PAT cannot mint more tokens.
- Human-only actions return `403 agents must propose`: annotations (create, edit, delete, reply, vote, report, hide), changing labels, evidence, approving proposals, and acknowledging citations. Agents file proposals for people to approve instead. See [agents and tokens](../user/agents-and-tokens.md).
- Admin routes, even when the owner is an admin. Admin operations are in the [admin guide](../admin/README.md).
- Reach documents whose label has `block_agents`. Those documents are hidden from PAT callers, admins included. Label rules are in [admin labels](../admin/labels.md).

Create a PAT with a JWT (example 5 below). Revoke it with `DELETE /api/me/tokens/{token_id}`. Users manage tokens in the UI too.

## Rate limits

`ROR_RATE_LIMIT_PER_MINUTE` is the per-token bucket size and refill per minute. The default is `0`, which turns the limit off. When on:

- The key is the PAT id for a PAT and the user id for a JWT. The bucket is held in memory by each API replica, so the cluster-wide ceiling is about replicas times the limit.
- When the bucket is empty the API answers `429` with `{"detail": "rate limited"}` and a `Retry-After` header (whole seconds, at least 1). Wait that long and retry.
- There are no `X-RateLimit-*` headers. The limit also covers `/mcp`.

A `503` with `Retry-After: 2` means the embedding service is throttled or down. Retry after the delay.

## Pagination

Five styles exist. Check the route in [openapi.json](openapi.json).

| Style | Parameters | Used by |
|---|---|---|
| Page and size | `page` (from 1), `size` (1 to 50) | `GET /api/search` |
| Opaque cursor | `after`, response field `next` | `GET /api/search` browse list, `GET /api/me/uploads` |
| Limit and offset | `limit`, `offset` | Handoffs, annotations, `GET /api/documents/{doc_id}/cited-by` (the references list takes no paging parameters), uploads, `GET /api/me/proposals` |
| Chat cursor | `before` (opaque), `limit` (1 to 100) | `GET /api/chats`: pass the last chat's cursor to get older chats; a bad cursor is `422 invalid cursor` |
| Notification keyset | `before_id`, `since_id`, `limit` (up to 200), `unread_only`; response `next_before_id` | `GET /api/me/notifications`: newest first; `since_id` polls for newer rows |
| Admin pages | `page` and `limit` (up to 200), or `before_id` and `next_before_id` | Admin lists |

**Cursors are opaque.** A cursor encodes the sort position of the last item. Do not parse or build one. Pass `next` back unchanged as `after`. `next` is `null` on the last page.

Rules:

- On search, `after` works only when browsing (empty `q`), for every sort, but not with `sort=cited` or `cited_min`, and never together with `page` other than 1. Page numbers reach the first 10,000 papers; `after` continues past them.
- On `/api/me/uploads`, `after` and a non-zero `offset` cannot be combined.
- A malformed cursor gives `422 malformed after cursor`.
- Search `total` and the facets are corpus-wide when browsing (everything the caller can see, `total_capped` above 10,000), and cover the kept results when `q` is set.
- `/api/me/uploads` reports `total` capped at 10,000 and sets `total_capped` when the cap is hit.
- A query (`q` set), `sort=cited` and `cited_min` rank at most 200 documents (the `MAX_DOCS` cap); `total_capped` is set when the cited window had more.

## Chat streaming (SSE)

`POST /api/chats/{chat_id}/messages` answers with `Content-Type: text/event-stream`, `Cache-Control: no-cache` and `X-Accel-Buffering: no`. Create the chat first with `POST /api/chats`. Needs the `write` scope.

Each frame is `event: <name>` then `data: <JSON>` then a blank line. Events in the order you can see them:

| Event | Data | When |
|---|---|---|
| `meta` | Routing and context details: `excluded_by_label` (context withheld by label), `route`, `agentic`, `subqueries`, `global` (a global fallback carries `global: {"fallback": ...}`), `summarize` (`{doc_id, title, version, partial?}`, for whole-document summary questions) | First, but only when there is something to report. Can appear again after `progress` |
| `progress` | `{"step", "of", "done"}` for agentic retrieval, or `{"mapped", "total"}` for a global question | Zero or more, before the first `token` |
| `reading` | `{"part", "of"}`, once per window when the question asks for a summary of one document | Before the first `token` |
| `token` | `{"text": "<delta>"}` | One per generated piece of the answer |
| `citations` | `{"items": [...]}` the sources cited in the answer, numbered as in the text | After the last `token` |
| `done` | `{"message_id": "<uuid>"}` | Last frame on success |
| `error` | `{"detail": "inference failed"}` | Last frame on failure |

Do not assume `meta` is present. When the first frame is a `token`, no `meta` was sent. Lines that start with `:` (such as `: keepalive`) are comments: ignore them. Exactly one of `done` or `error` ends the stream. The answer is saved once: complete, failed, or interrupted when the client disconnects. Close the connection to cancel.

The chat body takes `content` (1 to 4000 characters), optional `model` (must be in the configured list), `include_notes` and `viewer`. See the schema `MessageIn` in [openapi.json](openapi.json).

## Uploads

`POST /api/documents` takes `multipart/form-data`. The file part is streamed straight to object storage, so the server does not spool it locally. Form fields may come before or after `file`.

| Field | Notes |
|---|---|
| `file` | Required. Larger than `ROR_MAX_UPLOAD_BYTES` (default 524288000, 500 MiB) gives `413` |
| `title` | Up to 500 characters |
| `visibility` | `private`, `shared`, `org_unit` or `everyone` (default `everyone`) |
| `force` | `true` to upload content that already exists |
| `label_id` | Optional label |

A `201` returns `document_id`, `version_id` and `sha256`. Ingestion then runs in the background: poll `GET /api/me/uploads` for `stage` and `status`. A version in the `needs_title` stage waits for `PUT /api/documents/{doc_id}/versions/{version_id}/title`.

**Idempotency.** There is no idempotency key. Duplicates are found by content hash: re-sending a file that already exists returns `409` with `{"detail": {"code": "duplicate", "document_id": "..."}}`, which usually makes a retry after a timeout harmless. The check is best effort: it cannot see a concurrent request that has not committed yet, so two simultaneous uploads of the same file can both succeed. A committed upload that is still ingesting is matched. Only versions you may know about are matched. Send `force=true` to store it anyway. With a per-user quota (`ROR_UPLOAD_QUOTA_BYTES`, default `0` meaning unlimited), going over gives `413 upload quota exceeded`.

An `org_unit` visibility needs the uploader to belong to an organisational unit, else `422`.

## Endpoints

Grouped by domain. Each group's request and response schemas are in [openapi.json](openapi.json). Every route needs a bearer token unless noted. "PAT" notes follow the rules in [Authentication](#authentication).

### Search and library

| Route | Purpose |
|---|---|
| `GET /api/search` | Hybrid search with filters, facets, sorting and paging. Empty `q` browses. Parameters: [agent integration](../enterprise/agent-integration.md#library-search-api) |
| `GET /api/search/facets/{name}` | Find values of the `author`, `venue`, `tags` or `journal` facet under the current filters (`find`, 1 to 100 characters) |
| `GET /api/me/saved-searches`, `POST /api/me/saved-searches`, `PATCH` and `DELETE /api/me/saved-searches/{sid}` | Your saved library searches |
| `PUT /api/documents/{doc_id}/vote`, `POST /api/documents/{doc_id}/tags`, `DELETE /api/documents/{doc_id}/tags/{tag}` | Vote and tag a document |
| `GET /api/collections`, `POST /api/collections`, `PATCH` and `DELETE /api/collections/{cid}`, `PUT` and `DELETE /api/collections/{cid}/items/{doc_id}` | Personal collections |
| `GET /api/export/bibtex` | BibTeX for up to 200 documents or one collection |
| `GET /api/labels` | Labels you can see |

### Documents

| Route | Purpose |
|---|---|
| `POST /api/documents` | Upload (see [Uploads](#uploads)) |
| `GET /api/documents/{doc_id}` | Document detail. The `ids` field maps an identifier kind (for example `doi`) to its value, with the DOI folded in, and is `{}` when there are none. The schema in [openapi.json](openapi.json) does not show it because the route returns an untyped object |
| `PATCH /api/documents/{doc_id}`, `DELETE /api/documents/{doc_id}`, `POST /api/documents/{doc_id}/transfer` | Edit, delete, transfer ownership |
| `GET /api/documents/{doc_id}/versions/{n}/file`, `/pdf`, `/docling` | Original file, PDF rendering, parsed structure |
| `PUT /api/documents/{doc_id}/versions/{version_id}/title` | Supply a title for a version that needs one |
| `POST /api/documents/{doc_id}/versions/{version_id}/near-dup` | Decide a near-duplicate |
| `GET /api/documents/{doc_id}/references`, `GET /api/documents/{doc_id}/cited-by` | Citation graph |
| `PUT /api/documents/{doc_id}/label` | Change a document label (human only) |

### Chat

| Route | Purpose |
|---|---|
| `POST /api/chats`, `GET /api/chats`, `GET /api/chats/models` | Create, list, list available models |
| `GET`, `PATCH`, `DELETE /api/chats/{chat_id}` | Read, rename or rescope, delete |
| `POST /api/chats/{chat_id}/messages` | Ask a question; SSE answer (see [Chat streaming](#chat-streaming-sse)) |
| `GET` and `PUT /api/chats/{chat_id}/shares`, `GET /api/chats/{chat_id}/export.md` | Sharing and Markdown export |
| `PUT /api/messages/{message_id}/rating` | Rate an answer |

### Notes, evidence and proposals

| Route | Purpose |
|---|---|
| `GET` and `POST /api/documents/{doc_id}/annotations` | List and create notes (writes are human only) |
| `GET`, `PATCH`, `DELETE /api/annotations/{note_id}`, `POST .../replies`, `PUT .../vote`, `POST .../report`, `POST .../hide`, `PUT /api/annotations/{note_id}/label` | Work with one note |
| `GET /api/annotations/{note_id}/evidence`, `POST /api/annotations/{note_id}/evidence` (multipart `meta` and `file`, human only), `GET /api/evidence/{evidence_id}`, `GET /api/evidence/{evidence_id}/file`, `PUT .../reproduction`, `DELETE /api/evidence/{evidence_id}` | Evidence attached to notes |
| `GET /api/me/proposals`, `GET /api/me/proposals/count`, `POST /api/proposals/{proposal_id}/approve`, `POST /api/proposals/{proposal_id}/reject`, `POST /api/proposals/bulk-approve` | Review changes an agent proposed |

### Drafts and writing

| Route | Purpose |
|---|---|
| `POST /api/drafts`, `GET /api/drafts`, `GET /api/drafts/{doc_id}` | Create, list, read drafts |
| `PUT /api/drafts/{doc_id}/content`, `PUT /api/drafts/{doc_id}/coauthors`, `DELETE /api/drafts/{doc_id}` | Edit, co-authors, delete |
| `POST /api/drafts/{doc_id}/validate`, `/check-citations`, `/build`, `/submit` | Checks, LaTeX build, submit for review |
| `GET /api/drafts/{doc_id}/checks`, `GET /api/drafts/{doc_id}/versions/{n}` | Check results, version |
| `POST` and `DELETE /api/drafts/{doc_id}/citations/ack` | Acknowledge, or take back the acknowledgement of, a flagged citation (human only) |
| `GET /api/skills/roracle-writer.zip` | Writing skill bundle for agents |

### Handoffs, inbox and account

| Route | Purpose |
|---|---|
| `POST /api/handoffs`, `GET /api/handoffs`, `GET`, `PATCH`, `DELETE /api/handoffs/{code}`, `GET /api/handoffs/{code}/parts/{part}`, `POST /api/handoffs/{code}/notes` | Frozen context packages. Contents are fixed at creation, and each load re-checks who may see what. See [handoffs](../user/handoffs-inbox.md) |
| `GET /api/me`, `GET /api/me/uploads` | Who you are; your uploads |
| `GET`, `PUT /api/me/onboarding` | Get-started checklist state (PUT sets `dismissed`); GET also returns `pats_enabled` |
| `GET /api/me/notifications`, `POST /api/me/notifications/read` | Inbox |
| `GET`, `POST /api/me/tokens`, `DELETE /api/me/tokens/{token_id}` | Personal access tokens (JWT only) |
| `GET /api/directory/users`, `GET /api/directory/groups` | Look up people and groups for sharing |

### Admin

Admin routes sit under `/api/admin/` and need a JWT with the admin role: users and groups, labels, connectors, jobs and maintenance, reindex, runtime settings, eval, writing profiles, audit, tags, drafts review. Each successful admin call is audited as `admin.<endpoint>` (failed calls roll the row back), and a denied call is audited (best effort) as `admin.denied`. Operations are described in the [admin guide](../admin/README.md); the routes are in [openapi.json](openapi.json).

### Operations

| Route | Purpose |
|---|---|
| `GET /healthz` | Liveness and version. No auth. Public address: `GET /api/healthz` |
| `GET /readyz` | Readiness: `{"checks": {"db", "es", "s3"}}`, 200 when all `ok`, else 503. No auth. In-cluster only (`/api/readyz` is 404 at the proxy) |
| `GET /metrics` | Prometheus metrics. Not in the OpenAPI schema. In-cluster only (`/api/metrics` is 404 at the proxy) |
| `POST /mcp` | MCP over JSON. See [MCP tools](mcp-tools.md) |

## Examples

Replace identifiers with your own. Add `-H "Authorization: Bearer $TOKEN"` to every call except where noted.

### 1. Search

```bash
curl -sS --get "https://roracle.example.internal/api/search" \
  -H "Authorization: Bearer $TOKEN" \
  --data-urlencode "q=graph neural networks" \
  --data-urlencode "year_min=2024" \
  --data-urlencode "size=5"
```

Returns `total`, `total_capped`, `page`, `next`, `results` and `facets`. Repeat a filter to pass several values.

### 2. Chat with streaming

Create a chat, then post a question. `-N` turns off curl buffering so frames show as they arrive.

```bash
curl -sS -X POST "https://roracle.example.internal/api/chats" \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"title": "GNN survey", "scope": "all"}'
# -> {"id": "<chat_id>", ...}

curl -sS -N -X POST "https://roracle.example.internal/api/chats/<chat_id>/messages" \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"content": "Which papers compare message-passing variants?"}'
```

Output looks like:

```text
event: token
data: {"text": "Several papers"}

event: citations
data: {"items": [...]}

event: done
data: {"message_id": "..."}
```

`scope` is `all`, `open` or `collection:<uuid>`.

### 3. Upload

```bash
curl -sS -X POST "https://roracle.example.internal/api/documents" \
  -H "Authorization: Bearer $TOKEN" \
  -F "file=@paper.pdf" -F "title=A Survey of GNNs" -F "visibility=private"
# -> {"document_id": "...", "version_id": "...", "sha256": "..."}
```

Add `-F force=true` to bypass the duplicate check.

### 4. Cursor paging

```bash
curl -sS --get "https://roracle.example.internal/api/me/uploads" \
  -H "Authorization: Bearer $TOKEN" --data-urlencode "limit=2"
# -> illustrative: {"items": [...], "total": 7, "total_capped": false, "next": "MjAyNi0x..."}

curl -sS --get "https://roracle.example.internal/api/me/uploads" \
  -H "Authorization: Bearer $TOKEN" \
  --data-urlencode "limit=2" --data-urlencode "after=MjAyNi0x..."
```

Pass `next` back as `after` until `next` is `null`. The browse list works the same way: `GET /api/search?sort=newest&size=20`, then `after`.

### 5. Create a personal access token

This needs a JWT, not a PAT. `scopes` is `["read"]` (default) or includes `"write"` or `"manage"` (each implies the scopes before it). `expires_in_days` is 1 to 365 (default 30).

```bash
curl -sS -X POST "https://roracle.example.internal/api/me/tokens" \
  -H "Authorization: Bearer $JWT" -H "Content-Type: application/json" \
  -d '{"name": "ci-agent", "scopes": ["read"], "expires_in_days": 30}'
# -> 201 {"id": "...", "name": "ci-agent", "prefix": "ror_pat_...", "scopes": ["read"],
#         "expires_at": "...", "token": "ror_pat_..."}
```

The full `token` is returned only in this response. Store it now.

### 6. Export BibTeX

```bash
curl -sS --get "https://roracle.example.internal/api/export/bibtex" \
  -H "Authorization: Bearer $TOKEN" \
  --data-urlencode "doc_id=<doc_id_1>" --data-urlencode "doc_id=<doc_id_2>"
```

Returns `text/x-bibtex`. Use `collection=<collection_id>` instead of, or together with, `doc_id` (at most 200 documents in total). One of the two is required, else `422`. Documents you cannot see, or whose label blocks export for you, are left out without an error.

### 7. Revoke a token

```bash
curl -sS -X DELETE "https://roracle.example.internal/api/me/tokens/<token_id>" \
  -H "Authorization: Bearer $JWT"
# -> 204
```
