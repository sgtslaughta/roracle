# Architecture

For developers: how rOracle is put together. Decisions and their rationale are in the
[ADRs](../adr/README.md); terms are in the [glossary](../glossary.md).

## Pages

- [Overview](overview.md): context, container and component diagrams, and a container-to-image-to-ADR map.
- [Ingest data flow](ingest.md): a sequence diagram per stage, from upload or connector to a searchable version.
- [Retrieval and chat data flow](retrieval-chat.md): search, router, global and agentic chat, citations and `exclude_llm`.

Planned pages (not written yet, so not linked): access control, jobs and queue, search index lifecycle,
storage.
