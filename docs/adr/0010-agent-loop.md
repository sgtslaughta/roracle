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

## Update (2026-11-17)

Handoffs have no live mode. Contents are frozen when the handoff is created (kinds: chat, collection, search, document, agent; `handoff.py:28`). Who may load one, and what is redacted, is re-evaluated on every load. Postgres re-checks visibility and version stage (R19; older versions of a visible document are allowed). Documents hidden by `block_agents` are dropped from the package for every loader, admins included ([ADR-0014](0014-sensitivity-labels.md) R10).
