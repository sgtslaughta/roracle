# ADR-0014: Sensitivity labels

Status: Accepted (2026-11-16)

## Context

Admins need handling markers on documents and notes that restrict LLM use, agent access and export,
without introducing a second access-control system. Visibility and shares stay the only grant.

## Decision

- **R1 Storage.** Table `labels` (migration `0012`). `documents.label_id` is NOT NULL (FK `RESTRICT`),
  `annotations.label_id` is nullable (null = inherit the document label), `connectors.label_id` is
  nullable (null = global default). The migration seeds `General` (rank 0, `#496580`, no gates, default)
  and backfills every document. Label is per document, not per version.
- **Model.** Admin-ordered by unique integer `rank` (higher = more sensitive), at most 20 labels, exactly
  one default. Four gates: `exclude_llm`, `block_agents`, `block_export`, `reason_on_lower`. Gates are
  monotonic: a gate set on a label is set on every higher-ranked label (422 `gates must not decrease with
  rank`), so raising a label can never escape a gate and `effective = max(doc, note)` carries both gate sets.
- **Labels only restrict.** Every label check is an extra AND on top of `visible_documents` /
  `readable_notes`; no label condition is ever OR-ed into visibility.
- **R6 ES projection.** Chunks carry `label_id` (keyword), not rank. Gate sets resolve at query time from
  the per-request `Policy`, so reorder and gate edits need no reindex; a document's label change converges
  through the `acl_sync` job (immediate sync plus durable job, as for visibility). The migration enqueues
  one `acl_sync` job per non-deleted document; until it drains, ES lacks the field and the Postgres
  post-filter (which applies the same gates) is the authority: brief over-fetch, never a leak.
- **R7 Gate (b) `block_agents`.** For PAT principals (admin PATs included) the label-blocked documents
  and notes behave as invisible: `access.visible_documents` adds a label subquery, `search_acl.acl_filter`
  adds `must_not terms label_id`, `readable_notes` applies the effective label. Result: 404, absent from
  lists, search, facets and counts, with no "blocked" hint.
- **R8 Gate (a) `exclude_llm`.** Chat/`ask` retrieval, community note passages, `cite_check`, draft
  style/validate and the ingest summary skip such content (`cite_check` on an excluded document yields verdict `unchecked`, "label excludes LLM review"; a draft whose own label excludes LLM skips the style and cite judges) (ES `must_not` plus Postgres re-check). Chat
  context the user asked for explicitly (open tabs, selection, pins) is dropped and reported as
  `excluded_by_label` in the SSE `meta` frame. Agents (PAT) additionally receive no passage text of these
  documents anywhere: metadata and label stay visible, text is replaced or emptied and flagged `withheld`.
  Embeddings are still computed (not a generative context); existing summaries and past chat answers are not
  retro-redacted for their owner, but replaying an old turn into an LLM drops it.
- **R9 Gate (c) `block_export`.** Applies to everyone except the document owner and admins, including their
  PATs. Non-owner PATs are always treated as downloads (no inline reader view). Humans may still read inline
  in the reader; only download/export is blocked, and each inline read is audited `label.view`. This is a
  deterrent, not DRM.
- **R10 Handoffs.** A handoff is an agent context package: `block_agents` documents and notes are excluded
  for every loader, human or agent. Documents dropped at `create` (`_visible_doc_ids`) are *not* counted in
  `withheld`; only redactions at view time are. Deliberate (safer than R10, which counted both): the
  loader's `withheld` never signals documents that were hidden from the package before it existed. An MCP
  creator still sees them in `create_handoff`'s `dropped` count (all requested ids not kept).
- **Who may change a label.** Document: owner or admin; note: author or admin. Raise or equal is allowed;
  lowering is admin only (403 `only admins can lower a label`) and needs a 3-500 char reason when the
  current label has `reason_on_lower`. A note label cannot rank below its document's (422). All write
  routes are human-only; agents `propose_label` and a human approves (the approver's rights apply, so an
  owner cannot approve a lowering). Every change is audited and notifies the owner/author when someone else
  acted.
- **R14 Not a classification system.** rOracle sensitivity labels are handling markers enforced only inside
  rOracle. They are not a security classification system, do not implement clearance or need-to-know, and
  must not be relied on to protect classified, export-controlled or otherwise regulated data. Access is
  governed solely by visibility and shares; labels can only restrict.

## Consequences

- One subquery per visibility check for PATs; gate edits take effect immediately without reindexing.
- Migration `0012` locks `documents` while it backfills and enqueues one `acl_sync` job per document
  (see the operations runbook).
- Owners and admins keep exporting their own labelled work; non-owners can still read inline, so
  `block_export` deters but does not prevent copying.
- No label retire/merge: a label in use cannot be deleted (409 `label in use`).
- Operations and operator-facing limits: [security](../enterprise/security.md#13-sensitivity-labels),
  [agent integration](../enterprise/agent-integration.md#sensitivity-labels),
  [runbook](../enterprise/runbook-routine.md#sensitivity-labels).
