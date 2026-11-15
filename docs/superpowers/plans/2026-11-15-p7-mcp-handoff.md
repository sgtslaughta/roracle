# P7 MCP & Handoff Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Agents use rOracle over MCP with exactly their user's permissions (Keycloak JWT or personal access token), and researchers hand sessions to agents (and agents back to researchers) via short, non-secret handoff codes whose content is redacted per loader.

**Architecture:** PATs are rows with a SHA-256 hash; `resolve_principal()` accepts a JWT or `ror_pat_…` and returns the same `Principal` (plus `scopes`). Handoffs are rows keyed by an 8-char code; a service module (`roracle/handoff.py`) does create/load/redact/page/append and is shared by REST (`/handoffs`) and MCP. The MCP server is the official `mcp` SDK (`mcp.server.MCPServer`, v2) mounted at `/mcp` (stateless streamable HTTP, JSON responses) behind a small ASGI auth wrapper; each tool opens its own DB session and calls the same functions the REST routes use, so ACL and R19 are identical.

**Tech Stack:** P4 stack + `mcp>=2.3,<3`.

**Spec:** `docs/spec.md` §3 (PATs), §7 (Handoff, MCP tools); ADR-0004, ADR-0010; `docs/enterprise/agent-integration.md`.

## Global Constraints

- PAT format `ror_pat_<secrets.token_urlsafe(32)>`; stored: `sha256(token)` hex + 12-char display prefix. Scopes `["read"]` or `["read","write"]`. Expiry required (default 30 d, max 365 d). Revocable. `settings.pats_enabled` (env `ROR_PATS_ENABLED`, default true) disables all PATs globally.
- JWT principals have scopes `{"read","write"}`. A read-only PAT on any non-GET/HEAD/OPTIONS REST request → 403 `{"detail":"insufficient scope"}`; MCP write tools → tool error `insufficient scope`. PAT management (`/me/tokens*`) requires a JWT (PAT → 403).
- Every invalid/expired/revoked/disabled PAT → the same uniform 401 as a bad JWT.
- Handoff code: 8 chars from `23456789ABCDEFGHJKMNPQRSTUVWXYZ` (31 symbols, 8 chars ≈ 39.6 bits — see Ruling R2), `secrets.choice`. Display `XXXX-XXXX`; URL `/h/XXXX-XXXX`; resource `roracle://handoff/XXXXXXXX`. Input normalisation: strip, uppercase, drop `-`/spaces, strip `roracle://handoff/` or `…/h/` prefix.
- Handoff kinds: `chat` (snapshot of messages + cited passages), `collection` (live `collection_id` + doc-id snapshot), `search` (query + result doc ids), `document` (doc id + page), `agent` (created via MCP: brief + doc ids).
- Audience: `creator` (default) | `shared` (users + groups in `handoff_shares`) | `authenticated`. Expiry default 7 d, max 90 d.
- Load errors: not found or not in audience → 404 `{"detail":"not found"}`; in audience but expired/revoked → 410 `{"detail":"handoff expired"}` / `{"detail":"handoff revoked"}`. Creator always in audience.
- Redaction on every load/part: doc ids and passages the loader cannot see (`visible_documents` + non-owners only `searchable` versions, via `chat.access.visible_versions`) are removed; manifest `withheld: N` = distinct withheld docs. Transcript citations to withheld docs become `{"n", "available": false}`; text unchanged (P4 shared-chat precedent).
- Every handoff load (REST or MCP, manifest or part) writes `audit_log(action="handoff.load", target=code, user_id, client)`. Every MCP tool call writes `audit_log(action="mcp.<tool>", client=<PAT name | JWT azp>)`.
- MCP tool errors: wrap `HTTPException` → `ToolError(detail)`; no stack traces, no existence leaks (same 404 text).

## Review Focus

1. Read-only PAT cannot write via REST *or* MCP; revoked/expired PAT or `pats_enabled=false` → 401 everywhere — Task 2, 4.
2. A handoff whose cited doc the loader cannot see: no title/id/passage of that doc leaks in manifest, parts or MCP output; `withheld` counts it — Task 3.
3. Code space/normalisation: lowercase, hyphen, URL and `roracle://` forms all load the same handoff; collisions retried — Task 3.
4. Every MCP retrieval tool goes through `visible_documents` / R19 PG re-check (search, get_passages, ask) — Task 4, 5.
5. Unauthenticated `/mcp` → 401 with `WWW-Authenticate: Bearer resource_metadata=…`; no tool runs — Task 4.

## Rulings (plan-level)

- R1: PAT management requires JWT — a leaked write PAT must not mint further long-lived tokens — cost: headless users must create PATs in UI (intended).
- R2: alphabet of 31 unambiguous symbols × 8 chars ≈ 39.6 bits, not 50; 50 bits needs 10+ chars which breaks the `XXXX-XXXX` format fixed by spec/ADR. Code is not a secret (auth + audience + redaction), so 40 bits only needs collision-resistance; collisions retried on insert — cost: spec wording "≈50-bit" is inaccurate; doc updated.
- R3: groups for PAT principals come from the user row as of the user's last JWT request (`principal_for_user`) — cost: group removal takes effect for PATs only after the next web login; documented in security.md.
- R4: one `audit_log` table (P7 is the first audited feature); P10 admin UI reads it — cost: none.
- R5: MCP runs stateless + JSON responses (no sessions, horizontal scale, no sampling/elicitation) and with DNS-rebinding protection off (bearer-only auth, no cookies) — cost: no server→client streaming; acceptable for these tools.
- R6: OAuth protected-resource metadata (`/.well-known/oauth-protected-resource`) served as a plain route pointing at the Keycloak issuer; no SDK auth server — cost: none.

---

### Task 1: Storage

**Files:** Migration `0006_mcp_handoff.py` (`down_revision = "0005"`); models `PersonalAccessToken(id, user_id→users CASCADE, name, prefix, token_hash unique, scopes jsonb, expires_at, revoked_at, last_used_at, created_at)`, `Handoff(code String(8) PK, creator_id→users, kind CHECK, title, goal, brief, source jsonb, doc_ids jsonb, passages jsonb, transcript jsonb, notes jsonb, audience CHECK, created_via CHECK ui|mcp, expires_at, revoked_at, created_at)`, `HandoffShare(handoff_code→handoffs CASCADE, principal_kind user|group, principal; PK all three)`, `AuditLog(id bigserial, at, user_id nullable, action, target, client, detail jsonb)`; `Settings.pats_enabled`. Test `tests/test_p7_schema.py`.
- [ ] Tests: CHECK constraints reject bad kind/audience; cascade on handoff delete; token_hash unique. **Commit** `feat(mcp): add token, handoff and audit storage`

### Task 2: Personal access tokens + dual auth (opus review)

**Files:** `roracle/pats.py`, `roracle/auth.py` (extend), `api/routes/tokens.py`; Test `tests/test_pats.py`.
**Interfaces:** `Principal.scopes: frozenset[str] = frozenset({"read","write"})`, `Principal.client: str = ""`; `resolve_principal(session, jwks, settings, authorization) -> Principal` (JWT path unchanged; `ror_pat_` path: hash lookup, not revoked, not expired, enabled; updates `last_used_at`; `client = "pat:<name>"`); `current_principal` = resolve + scope check by `request.method`; `require_jwt` dependency. Routes: `GET /me/tokens`, `POST /me/tokens {name, scopes, expires_in_days}` → 201 `{id, name, prefix, scopes, expires_at, token}` (token shown once), `DELETE /me/tokens/{id}` → 204 (others' ids → 404).
- [ ] Tests: PAT authenticates `/me` as its user with the user's stored groups; read PAT → 403 on POST/PATCH/DELETE; revoked/expired/unknown/disabled → 401; PAT on `/me/tokens` → 403; hash stored, never the token; expiry > 365 → 422.
- [ ] **Commit** `feat(auth): personal access tokens`

### Task 3: Handoff service + REST (opus review)

**Files:** `roracle/handoff.py`, `roracle/audit.py` (`audit(session, p, action, target, detail=None)`), `api/routes/handoffs.py`; Test `tests/test_handoff.py`.
**Interfaces:** `new_code()`, `normalize(code_or_uri) -> str`, `display(code) -> "XXXX-XXXX"`; `create(session, p, kind, *, source_id=None, title="", goal="", brief="", doc_ids=(), audience="creator", shares=(), expires_in_days=7, via="ui") -> Handoff` (chat: `readable_chat`, snapshot messages + cited passages; collection: owned collection, membership snapshot; document/search/agent: doc ids filtered to what the creator can see); `load(session, p, code) -> dict` manifest `{code, display, url, uri, kind, title, goal, brief, source, doc_ids, passages, notes, withheld, parts: {"transcript": pages}, expires_at, created_by}`; `get_part(session, p, code, part, page) -> {items, page, pages}` (20 items/page); `append_note(session, p, code, text)` (anyone in audience with write scope; note `{at, by, text, via}`). Routes: `POST /handoffs`, `GET /handoffs` (mine, newest first), `GET /handoffs/{code}`, `GET /handoffs/{code}/parts/{part}?page=`, `PATCH /handoffs/{code}` (creator: title, brief, goal, audience, shares, expires_in_days), `DELETE /handoffs/{code}` (creator: revoke → 204), `POST /handoffs/{code}/notes`.
- [ ] Tests: code alphabet/normalisation forms; creator-only default; shared group member loads, outsider 404; expired/revoked 410 for audience, 404 for outsider; withheld count + no leaked titles/ids for invisible docs incl. transcript citations; every load audited; expiry > 90 → 422.
- [ ] **Commit** `feat(handoff): create, load and redact handoffs`

### Task 4: MCP server + read tools

**Files:** `pyproject.toml` (`mcp`), `roracle/mcp/__init__.py`, `roracle/mcp/server.py` (`build_mcp(app) -> MCPServer`), `roracle/mcp/auth.py` (ASGI wrapper + `current()` contextvar), `api/app.py` (mount `/mcp`, lifespan enters `session_manager.run()`, route `/.well-known/oauth-protected-resource`); Test `tests/test_mcp.py` (TestClient as context manager; JSON-RPC `initialize` + `tools/call` over POST `/mcp`).
**Interfaces:** tools `search(query, collection?, limit=10)` (calls `routes.search.search`; returns results with passages + `chunk_id`s), `get_document(doc_id)` (`documents_read.detail`), `get_passages(chunk_ids, context=0)` (ES fetch by id → R19 `visible_versions` → drop invisible silently; `context` neighbours ≤ 2), `list_collections()`. Each tool: own session from `app.state.session_factory`, commit on success, `audit("mcp.<tool>")`.
- [ ] Tests: no/invalid token → 401 + `WWW-Authenticate` with `resource_metadata`; `tools/list` has the P7 tool set; search with FakeES returns only visible docs; get_passages drops invisible chunk; get_document invisible → tool error "not found".
- [ ] **Commit** `feat(mcp): mount MCP server with read tools`

### Task 5: MCP ask, upload, handoff tools + docs

**Files:** `roracle/mcp/server.py` (or `mcp/tools_write.py` if > 500 lines), `docs/enterprise/agent-integration.md`, `docs/enterprise/security.md`, `docs/enterprise/deployment-guide.md` (env vars); Test `tests/test_mcp_tools.py`.
**Interfaces:** `ask(question, collection?)` → `retrieve_for_chat` (scope `all` / `collection:<id>`, empty viewer) + non-streaming completion + `parse_citations` → `{answer, citations}`; `upload(filename, content_base64, title?, visibility="private")` (write; calls `routes.documents.upload`; `max_request_body_size` sized from `max_upload_bytes`); `load_handoff(code)`, `get_handoff_part(code, part, page=1)`, `create_handoff(kind="agent", title, brief, doc_ids?, audience?)` (write; returns `{code, url, uri}`), `append_to_handoff(code, note)` (write). Resource template `roracle://handoff/{code}` → manifest.
- [ ] Tests: read PAT → `upload`/`create_handoff`/`append_to_handoff` error "insufficient scope"; ask with fake LLM returns cited answer and never cites invisible docs; load via `roracle://` form; agent-created handoff loadable by creator in REST.
- [ ] **Commit** `feat(mcp): ask, upload and handoff tools`
