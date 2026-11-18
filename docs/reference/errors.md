# Errors

Every failure is an HTTP status with a JSON body. Shapes and request schemas live in [openapi.json](openapi.json); the walkthrough is in the [API guide](api.md).

## Body shapes

| Shape | When |
|---|---|
| `{"detail": "<message>"}` | Almost every error: 401, 403, 404, 409 (non-upload), 413, 429, 503 |
| `{"detail": [{"type", "loc", "msg", ...}]}` | Request validation failures (422) from FastAPI |
| `{"detail": {"code": "duplicate", "document_id": "<uuid>"}}` | Upload of a file whose content already exists (409) |
| `{"detail": "export blocked by label", "label": "<label name>"}` | A label blocks export of a document you can otherwise see (403) |

Match on the status code first. Treat the `detail` text as a hint for humans: only the `duplicate` code and the `label` field are structured.

## Status codes

| Status | Meaning | Typical `detail` |
|---|---|---|
| 400 | Malformed upload | `empty file`, `form part header too large` |
| 401 | Missing, invalid, expired or revoked credential. The same message in every case except the stale-owner case below, so the cause is not revealed. Carries `WWW-Authenticate: Bearer` | `invalid token` |
| 401 | A PAT whose owner has not signed in recently (see `ROR_PAT_MAX_IDENTITY_AGE_DAYS`). The owner signs in again | `token owner session stale — sign in again` |
| 403 | Read-only PAT used on a method other than GET, HEAD or OPTIONS | `insufficient scope` |
| 403 | PAT used on token management | `personal access tokens cannot manage tokens` |
| 403 | PAT used on a human-only action (notes, label changes, evidence, proposal approval, citation acknowledgement) | `agents must propose` |
| 403 | Not an admin, or an admin route called with a PAT (admins included) | `admin only` |
| 403 | Label blocks export | `export blocked by label` |
| 403 | Posting to a chat that was shared with you read-only | `read-only` |
| 403 | A PAT reads or builds text whose label is Exclude from AI (a draft, its versions or reports, or a document's file or evidence) | `withheld by label: <label name>` |
| 403 | You are not the owner or author of this object | for example `only the document owner can change this document`, `only the draft owner can do this`, `only the creator or an admin can delete evidence` |
| 404 | Unknown, deleted or not visible to you. Hidden and absent look the same. Path ids above 2^63 - 1 also give 404 | `not found` |
| 405 | `GET /mcp` (only POST is served) | plain text `method not allowed`, `Allow: POST` |
| 409 | Duplicate upload (see above), or the action conflicts with current state: not awaiting a title or a near-duplicate decision, a reindex, rebuild, run or job already active, only failed jobs can be retried, a name or key that already exists, draft documents that must use the `/api/drafts` endpoints | for example `not awaiting a title`, `reindex running` |
| 410 | Handoff revoked or expired | `handoff revoked`, `handoff expired` |
| 413 | Upload larger than `ROR_MAX_UPLOAD_BYTES`; upload quota exceeded; JSON body over the cap | `file exceeds <n> MB limit`, `upload quota exceeded`, `request body too large` |
| 422 | Invalid parameter or body, NUL character, unknown model or scope | see [Input rejections](#input-rejections) |
| 429 | Rate limit hit. Carries `Retry-After` (whole seconds, at least 1) | `rate limited` |
| 429 | An agent already has 50 pending proposals for this user and client. No `Retry-After` | `too many pending proposals` |
| 500 | Creating a handoff could not find a free code (very rare) | `could not allocate handoff code` |
| 501 | LaTeX builds are not enabled | `LaTeX builds are not enabled` |
| 503 | A backend is unavailable, busy or in a transient conflict; retry | `inference unavailable` (with `Retry-After: 2`), `search unavailable`, `search index upgrade in progress`, `update failed, please retry`, `transfer failed, please retry`, `saved, but search index update is pending`, and for draft builds `build service busy`, `unavailable`, `not configured` or `timed out` |

`ROR_*` names are listed in [settings.md](settings.md).

## Input rejections

The input guard runs before routing and rejects what Postgres cannot store, with no side effects:

- A NUL character (`%00` or a raw NUL byte in the query string, `\u0000` or a raw NUL in a JSON body) gives 422 `NUL characters are not allowed`.
- A numeric path segment above 2^63 - 1 gives 404 `not found`.
- A JSON body larger than the cap gives 413 `request body too large`. The cap is 3 MiB, or `3 x ROR_DRAFT_MAX_BYTES + 64 KiB` when that is larger. It checks `Content-Length` first and then the bytes actually received.
- Multipart (`multipart/form-data`) and `application/octet-stream` bodies are not buffered or scanned. Uploads enforce their own limits instead (see [Uploads](api.md#uploads)).
- `/mcp` has its own body cap and is not scanned.
- A database value-out-of-range that slips past the guard gives 422 `value out of range or not storable`.

Search parameters have their own limits (422): `q` up to 1000 characters, list filters up to 50 values of up to 200 characters, `size` 1 to 50, `limit` ranges per route, and `mode=quick` accepts `q` only.

## Mid-stream errors

A chat answer streams with HTTP 200. Failures before the first frame (unknown chat, unknown `model`, unreadable scope, backends down) are ordinary HTTP errors from the table above. A failure after streaming starts arrives as an `error` event and the turn is saved as failed. See [Chat streaming](api.md#chat-streaming-sse).
