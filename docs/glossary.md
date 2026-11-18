# Glossary

Definitions are taken from the code under `backend/src/roracle`.

## Access

- **Principal**: the authenticated caller (`auth.Principal`): user id, groups, admin flag, scopes and how they authenticated (`jwt` or `pat`).
- **Visibility**: a document's audience: `private`, `shared`, `org_unit` or `everyone` (`access.py`). `shared` grants access through user or group shares only while visibility is `shared`. Published-paper co-authors (`document_coauthors`) keep read access whatever the visibility, as does the owner. Annotations use `private`, `shared`, `everyone`.
- **Org unit**: a rooted path such as `/physics/optics` derived from token groups. Blank or root-only groups are ignored, and `org_unit` visibility needs a usable one. An `org_unit` document is visible to a user whose org units, or their ancestors (including `/`), include the document's org unit.
- **R19 re-check**: Elasticsearch hits are only candidates; Postgres is the authority and re-checks document visibility, version stage (not `rejected` or `cancelled`, and `searchable` for non-owner non-admins) and the `block_agents` gate for PAT callers (and handoff loaders), plus `exclude_llm` where text goes to an LLM. Older versions of a visible document are allowed. An unreadable item looks like a missing one.
- **PAT**: personal access token, an opaque `ror_pat_...` bearer secret stored only as a SHA-256 hash, for agents and scripts.
- **Scope**: a PAT permission, `read` or `write`. Without `write`, non-GET/HEAD/OPTIONS requests get 403.

## Labels

- **Sensitivity label**: a ranked label on a document, with four flags (`GATES`): `exclude_llm`, `block_agents`, `block_export` and `reason_on_lower`.
- **Gates**: `exclude_llm` keeps text away from any LLM for everyone and withholds it from PAT callers. `block_agents` hides the document entirely from PAT callers, admins included; handoffs drop such documents for every loader and withhold their text in transcripts. `block_export` blocks export and download for non-owners and non-admins; inline reads write a `label.view` audit row at most once per user, document and UTC day. `reason_on_lower`: only admins can lower a document label, and when the current label has this flag a 3-500 character reason is required.
- **Effective label**: the higher-ranked of the document label and a note label (`Policy.effective`).

## Collaboration

- **Handoff**: a shareable context bundle with an 8-character code and an expiry. Kinds: `chat`, `collection`, `search`, `document`, `agent`. Contents are frozen at creation; who may load it and what is redacted are re-evaluated on every load. Notes can be appended. Chat and collection source handles are shown to the creator only.
- **Proposal**: an agent-submitted item (`note`, `relation` or `label`; `evidence` is reserved in the schema but not yet accepted) with status `pending`, `approved` or `rejected`, decided by a human.
- **Annotation kinds**: `comment`, `finding`, `correction`, `question`, `relation`. Status is `visible`, `hidden` or `removed`. A `relation` needs a relation type and a target document.
- **Evidence / RO-Crate**: provenance records of how a result was produced, shaped as an RO-Crate. The context identifier is never fetched (air-gap).

## Index and retrieval

- **Unit**: a retrieval item in the chunk index. An ordinary chunk has no `unit` field; summary units are `section` or `document` (RAPTOR). A unit hit expands into its real source chunks, so a summary is never cited.
- **Contextual note**: an LLM-written sentence situating a chunk in its document, used for BM25 and embeddings. With an `exclude_llm` label it falls back to the heading path.
- **Generation (index)**: an index named `chunks-<model-slug>-<gen>`. A model switch builds a new generation.
- **Alias**: the read alias `chunks` points at exactly one index; `chunks-all` is the mutation alias spanning generations.
- **Reembed**: re-embedding into a new generation with copy, converge, cutover and catch-up phases, then one atomic alias swap.
- **Rebuild**: `roracle rebuild --what context[,raptor]` recomputes index-time derived fields per version.

- **Identifier**: a persistent id in a version's `meta.ids` (`doi`, `arxiv`, `pmid`, `pmcid`, `isbn`, `acl`, `openreview`, `issn`, `eissn`). Citation linking matches only `doi`, `arxiv`, `pmid`, `pmcid`, `acl` and `openreview`; the reader shows all of them as copyable plain text (`Identifiers.tsx`).

## Jobs

- **Lease**: a running job's time-limited claim (`jobs` table, `FOR UPDATE SKIP LOCKED`); a heartbeat extends it and expired leases are requeued.
- **Drain**: after SIGTERM a running job gets `ROR_JOB_DRAIN_S` seconds, then its lease is released (0 waits).

## Writing and evaluation

- **Writing profile**: a versioned set of manuscript rules (YAML shipped in `writing/profiles`, with admin overrides in the database). Default `journal-article`.
- **Gold set**: labelled queries with expected documents, used by the eval harness (`eval/gold.py`).
- **BBQ**: Elasticsearch `bbq_hnsw` vector index type, selectable with `vector_index_type`; the config comment says to use it only after an eval gate. The default is `int8_hnsw`.
