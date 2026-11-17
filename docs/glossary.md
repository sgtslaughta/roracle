# Glossary

Definitions are taken from the code under `backend/src/roracle`.

## Access

- **Principal**: the authenticated caller (`auth.Principal`): user id, groups, admin flag, scopes and how they authenticated (`jwt` or `pat`).
- **Visibility**: a document's audience: `private`, `shared`, `org_unit` or `everyone` (`access.py`). `shared` grants access through user or group shares only while visibility is `shared`. Annotations use `private`, `shared`, `everyone`.
- **Org unit**: a rooted path such as `/physics/optics` derived from token groups. Blank or root-only groups are ignored, and `org_unit` visibility needs a usable one. An `org_unit` document is visible to a user whose org units, or their ancestors (including `/`), include the document's org unit.
- **R19 re-check**: Elasticsearch hits are only candidates; Postgres is the authority and re-checks visibility, label gates and latest version on every request before anything is shown or sent to an LLM. An unreadable item looks like a missing one.
- **PAT**: personal access token, an opaque `ror_pat_...` bearer secret stored only as a SHA-256 hash, for agents and scripts.
- **Scope**: a PAT permission, `read` or `write`. Without `write`, non-GET/HEAD/OPTIONS requests get 403.

## Labels

- **Sensitivity label**: a ranked label on a document with three gates and one rule: `exclude_llm`, `block_agents`, `block_export`, and `reason_on_lower` (a reason is required to lower the label).
- **Gates**: `exclude_llm` keeps the text away from any LLM, for everyone. `block_agents` withholds text from PAT callers and from handoff transcripts. `block_export` blocks export and download for non-owners and non-admins; inline reads write a `label.view` audit row at most once per user, document and UTC day.
- **Effective label**: the higher-ranked of the document label and a note label (`Policy.effective`).

## Collaboration

- **Handoff**: a frozen, shareable context bundle with an 8-character code and an expiry, redacted per loader at read time. Kinds: `chat`, `collection`, `search`, `document`, `agent`. Chat and collection source handles are shown to the creator only.
  - **Snapshot / live**: the bundle itself is frozen at creation. > TODO(verify): whether a "live" handoff mode exists; none found in `handoff.py`.
- **Proposal**: an agent-submitted item (`note`, `relation`, `evidence`, `label`) with status `pending`, `approved` or `rejected`, decided by a human.
- **Annotation kinds**: `comment`, `finding`, `correction`, `question`, `relation`. Status is `visible`, `hidden` or `removed`. A `relation` needs a relation type and a target document.
- **Evidence / RO-Crate**: provenance records of how a result was produced, shaped as an RO-Crate. The context identifier is never fetched (air-gap).

## Index and retrieval

- **Unit**: a retrieval item in the chunk index. An ordinary chunk has no `unit` field; summary units are `section` or `document` (RAPTOR). A unit hit expands into its real source chunks, so a summary is never cited.
- **Contextual note**: an LLM-written sentence situating a chunk in its document, used for BM25 and embeddings. With an `exclude_llm` label it falls back to the heading path.
- **Generation (index)**: an index named `chunks-<model-slug>-<gen>`. A model switch builds a new generation.
- **Alias**: the read alias `chunks` points at exactly one index; `chunks-all` is the mutation alias spanning generations.
- **Reembed**: re-embedding into a new generation with copy, converge, cutover and catch-up phases, then one atomic alias swap.
- **Rebuild**: `roracle rebuild --what context[,raptor]` recomputes index-time derived fields per version.

## Jobs

- **Lease**: a running job's time-limited claim (`jobs` table, `FOR UPDATE SKIP LOCKED`); a heartbeat extends it and expired leases are requeued.
- **Drain**: after SIGTERM a running job gets `ROR_JOB_DRAIN_S` seconds, then its lease is released (0 waits).

## Writing and evaluation

- **Writing profile**: a versioned set of manuscript rules (YAML shipped in `writing/profiles`, with admin overrides in the database). Default `journal-article`.
- **Gold set**: labelled queries with expected documents, used by the eval harness (`eval/gold.py`).
- **BBQ**: Elasticsearch `bbq_hnsw` vector index type, selectable with `vector_index_type`; the config comment says to use it only after an eval gate. The default is `int8_hnsw`.
