# Decisions

Why rOracle uses what it uses. The decision matrix rates the options considered for each technology area against the
user-weighted criteria (accuracy, security, air-gap fit, performance, licence, ops cost), and gives each decision's
status and revisit trigger. Architecture decision records live in [adr/](../adr/README.md).

## Pages

- [Decision matrix](decision-matrix.md): method, weights, summary of all 20 areas, cross-cutting caveats, ADR gaps.
- [Data tier](matrix-data.md): system of record and queue, search engine, vector quantisation, object store, ingestion.
- [Retrieval and models](matrix-retrieval.md): embedding model, reranker, LLM serving, retrieval techniques, votes, gold set.
- [Access, security and product](matrix-product.md): ACL enforcement, labels, identity, handoffs and writing, frontend.
- [Platform and delivery](matrix-platform.md): backend shape, deployment and IaC, CI and release, observability.
