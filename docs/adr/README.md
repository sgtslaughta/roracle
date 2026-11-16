# Architecture Decision Records

One file per decision. Format: Context → Decision → Consequences. Status `Accepted` unless superseded.
Spec: [`../spec.md`](../spec.md).

| ADR | Decision |
|---|---|
| [0001](0001-system-of-record.md) | Postgres is the source of truth; MinIO holds bytes; Elastic is a rebuildable index |
| [0002](0002-elastic-basic-app-acl.md) | Elastic Basic licence; ACL filters built by the API |
| [0003](0003-postgres-job-queue.md) | Own job table in Postgres (SKIP LOCKED); no broker |
| [0004](0004-identity.md) | mTLS proxy → Keycloak X.509 → JWT; scoped PATs |
| [0005](0005-ingestion-pipeline.md) | ClamAV, Gotenberg, Docling, GROBID; contextual chunk embeddings |
| [0006](0006-embedding-model-config.md) | Embedding model is config; index per model behind an alias |
| [0007](0007-retrieval.md) | Hybrid BM25 + kNN, app-side RRF, rerank, capped vote boost |
| [0008](0008-dedup-versioning.md) | SHA-256 blob dedup without disclosure; near-dup prompt; document versions |
| [0009](0009-one-backend-codebase.md) | One Python backend image, three entrypoints; MCP mounted in the API |
| [0010](0010-agent-loop.md) | Handoff links, propose-then-approve annotations, RO-Crate evidence |
| [0011](0011-paper-writing.md) | Writing profiles, citation verification, sandboxed LaTeX builds |
| [0012](0012-frontend.md) | React + Vite + shadcn/ui; design tokens |
| [0013](0013-repo-ci-release.md) | Monorepo, GitLab CI, Conventional Commits, single SemVer |
| [0014](0014-sensitivity-labels.md) | Admin-ordered sensitivity labels with LLM, agent and export gates; labels only restrict |
| [0016](0016-terraform-iac.md) | Terraform wraps the Helm chart (no cloud resources); placeholder gate for air-gap mirrors |
