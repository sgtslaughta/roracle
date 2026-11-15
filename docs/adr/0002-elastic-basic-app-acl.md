# ADR-0002: Elastic Basic licence with ACL filters built by the API

Status: Accepted (2026-11-15)

## Context
Document-level security and native RRF need paid Elastic tiers. Clients never query Elastic directly.

## Decision
- Run Elastic Basic. Every chunk/note document stores `owner_id`, `visibility`, `org_unit_path`,
  `share_users[]`, `share_groups[]`.
- The API builds a mandatory filter per request:
  `visibility=everyone OR owner_id=me OR me ∈ share_users OR share_groups ∩ my_groups OR
  (visibility=org_unit AND org_unit_path prefix-matches one of my groups)`.
- Rank fusion (RRF) is computed in the API.

## Consequences
- One query builder owns access control; it gets dedicated tests including "no leak" cases.
- Sharing changes require re-indexing the affected document's ACL fields (cheap update-by-query).
