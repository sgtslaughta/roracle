# ADR-0010: Agent loop — handoffs, proposals, evidence

Status: Accepted (2026-11-15)

## Decision
- **Handoff**: stored context object with a random 8-char id (alphabet without 0/O/1/I/L). The id is
  not a credential: loading requires auth, and documents the loader cannot see are redacted.
  Expiry default 7 days (max 90), revocable, audited.
- **Proposals**: agents never publish. `propose_annotation` creates a pending proposal; a human
  approves/edits/rejects in the UI; approved notes are attributed to the human, marked agent-assisted.
  One `proposals` table with a `kind` column serves notes, relations and evidence.
- **Evidence**: RO-Crate packages with required provenance; stored, scanned, never executed.

## Consequences
- Accountability stays with people; agent output cannot silently enter the corpus or retrieval.
