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

## Addendum (2026-11-15, P1 final review)

Uploads always PUT the blob, even when the content-addressed object already exists. Skipping the PUT made duplicate uploads measurably faster, an oracle revealing that someone else holds identical bytes. Cost: upload bandwidth on duplicates.

## Update (2026-11-17)

Near-duplicate candidates are found through a MinHash LSH band table (`version_lsh`, 16 bands x 8 rows, `ingest/neardup.py`) instead of a scan; below a 0.8 threshold the code falls back to scanning. Visibility filtering still happens before any candidate is shown.

Update (2026-11-18): near-duplicate checks run with the uploading principal's view. An agent upload (PAT, `/mcp`, OAuth agent client) carries an `agent` marker in its ingest job, so the check applies the agent gate and is never admin-wide; a hidden match is treated as no match. Security review 2026-11, F1.
