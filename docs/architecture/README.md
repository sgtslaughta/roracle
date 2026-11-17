# Architecture

For developers: how rOracle is put together. Decisions and their rationale are in the
[ADRs](../adr/README.md); terms are in the [glossary](../glossary.md).

## Pages

- [Overview](overview.md): context, container and component diagrams, and a container-to-image-to-ADR map.
- [Ingest data flow](ingest.md): a sequence diagram per stage, from upload or connector to a searchable version.
- [Retrieval and chat data flow](retrieval-chat.md): search, router, global and agentic chat, citations and `exclude_llm`.
- [Access control](access-control.md): identity chain, visibility and org units, the Elasticsearch filter and `acl_sync`, the R19 re-check, label gates.
- [Jobs and queue](jobs-queue.md): the `jobs` table, claims and fencing, leases, retries, job kinds, drain and scaling.
- [Search index lifecycle](search-index-lifecycle.md): aliases and generations, re-embed, rebuild, deleting old indices, the search cache, Elasticsearch 8 to 9.
- [Storage](storage.md): object layout, dedup and blob GC, the `tmp/` lifecycle, Rook-Ceph or external S3, object lock.
