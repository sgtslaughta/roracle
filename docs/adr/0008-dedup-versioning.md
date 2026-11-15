# ADR-0008: Deduplication without disclosure, near-dup prompt, versions

Status: Accepted (2026-11-15)

## Decision
- `blobs(sha256 PK, size, refcount)`; identical bytes stored once. A new upload whose hash matches a
  version the uploader **can see** returns 409 `duplicate` (client may retry with `force=true`).
  If the match is invisible to them, the upload proceeds normally and the response is
  indistinguishable from a fresh upload.
- Near-duplicates (MinHash + summary embedding, only among visible documents) prompt:
  new version / separate document / cancel. Connector matches go to an admin queue.
- `documents` hold identity, owner, visibility; `document_versions` hold blob + version number.
  Search defaults to the latest version.

## Consequences
- "No leak" behaviour is a tested contract, not an implementation detail.
