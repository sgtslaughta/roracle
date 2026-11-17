# Decision matrix

Why rOracle uses what it uses: the options considered for 20 technology areas, rated against the same six criteria.
The [ADRs](../adr/README.md) record each decision. This page and its four area pages put the decisions side by side,
give their current status, and name what would make a revisit worthwhile. Terms are in the [glossary](../glossary.md).

## Method

**Criteria and weights.** The weights are a user decision (2026-11-17):

| Criterion | Code | Weight | 5 means |
|---|---|---|---|
| Accuracy | A | 3 | Best answer and retrieval quality; for non-retrieval areas, correct and repeatable behaviour |
| Security | S | 3 | Strongest guarantees, fewest known limits |
| Air-gap fit | G | 3 | Runs fully offline from mirrored images, charts, packages and models, with nothing fetched at runtime |
| Performance / scale | P | 2 | Meets the [performance budgets](../perf/budgets.yaml) with headroom at the 1M-document target |
| Licence | L | 2 | Permissive open-source licence, no paid tier needed |
| Ops cost | O | 1 | Cheapest to run: fewest services, least operator effort |

**Ratings.** Each option gets 1 to 5 per criterion. **Every rating is judgement**, made by the author of this page
from the evidence linked in each row. They are not measurements. The weighted total is
`3A + 3S + 3G + 2P + 2L + O`, with a maximum of 14 × 5 = 70. The area pages write ratings as `A4 S5 G5 P4 L5 O5`.

**Evidence.** Each number taken from an evaluation or performance run links its report: [docs/eval](../eval/README.md),
the [performance report of 2026-11-17](../perf/report-2026-11-17.md) and [budgets.yaml](../perf/budgets.yaml).
Decisions without an ADR cite the decision ledger of the phase that made them, with date and line. Current state
(defaults, flags, chart values) is taken from code: `backend/src/roracle/config.py`, `deploy/helm/roracle/values.yaml`
and `deploy/terraform/`.

**Status.**

- **Decided**: chosen and backed by an ADR or a recorded user decision. A revisit needs new evidence.
- **Provisional**: chosen or shipped for now, with an explicit trigger that reopens it. Typical triggers are an eval
  PASS or FAIL, or a missing piece landing.
- **Revisit**: the evidence already points elsewhere, or a known gap must close first.

**How to read the totals.** Differences of 3 points or less are within judgement noise. Where the top-ranked option is
not the chosen one, the area page says why. Usually the reason is a criterion the matrix does not weigh, such as
switching cost, or an option that is a different kind of thing (a CI floor or a site's own tooling). The matrix
informs decisions; it does not overrule an ADR or an eval gate.

## Summary

| # | Area | Chosen (total) | Top-ranked if different | Status | Page |
|---|---|---|---|---|---|
| 1 | System of record and job queue | Postgres `jobs` table with `SKIP LOCKED` and leases (68) | | Decided | [data](matrix-data.md#1-system-of-record-and-job-queue) |
| 2 | Search engine and version | Elasticsearch Basic 9.5.6, int8 default (61) | OpenSearch 60, within noise | Decided (user, 2026-11-16) | [data](matrix-data.md#2-search-engine-and-version) |
| 3 | Vector quantisation | int8 HNSW (58) | | int8 decided; BBQ provisional, eval-gated | [data](matrix-data.md#3-vector-quantisation) |
| 4 | Object store | Rook-Ceph RGW (63); external S3 option (62) | | Decided (user, 2026-11-16) | [data](matrix-data.md#4-object-store) |
| 5 | Embedding model | EmbeddingGemma-300m, 768-d (57) | | Provisional: revisit when own-corpus eval data exists | [retrieval](matrix-retrieval.md#5-embedding-model) |
| 6 | Reranker | None today (67); TEI `bge-reranker-v2-m3` blend is the candidate (53) | | Provisional: own-corpus re-gate PENDING | [retrieval](matrix-retrieval.md#6-reranker) |
| 7 | LLM serving and models | OpenAI-compatible endpoint: Ollama on the GPU host (54), vLLM in-cluster (59) | | Provisional | [retrieval](matrix-retrieval.md#7-llm-serving-and-models) |
| 8 | Retrieval techniques | Hybrid BM25 + kNN with app-side RRF (67); notes, RAPTOR, router and agentic all off | | Baseline decided; each feature provisional until a PASS | [retrieval](matrix-retrieval.md#8-retrieval-techniques) |
| 9 | Votes boost | Smoothed ratio, ±15 % cap (67) | | Decided (user, 2026-11-15) | [retrieval](matrix-retrieval.md#9-votes-boost) |
| 10 | ACL enforcement | App-built ES filter + R19 re-check (64) | | Decided | [product](matrix-product.md#10-acl-enforcement) |
| 11 | Labels model | Restrict-only gates (63) | | Decided | [product](matrix-product.md#11-labels-model) |
| 12 | Identity | mTLS → Keycloak X.509 → JWT, scoped PATs (65) | | Decided | [product](matrix-product.md#12-identity) |
| 13 | Ingestion, dedup, versioning | Docling + GROBID + Gotenberg + ClamAV (58); non-disclosing dedup (64) | | Decided | [data](matrix-data.md#13-ingestion-stack-dedup-and-versioning) |
| 14 | Backend shape | One image, three entrypoints, MCP in the API (62) | | Decided | [platform](matrix-platform.md#14-backend-shape) |
| 15 | Agent loop, handoffs, writing | Frozen handoffs, propose-then-approve (63); LaTeX builder interface (56) | Gotenberg draft PDFs 58 (the shipped path) | Handoffs decided; LaTeX provisional (not shipped, 501) | [product](matrix-product.md#15-agent-loop-handoffs-and-paper-writing) |
| 16 | Frontend | React + Vite + shadcn/ui (61) | | Decided | [product](matrix-product.md#16-frontend) |
| 17 | Deployment and IaC | Terraform wrapping Helm (58) | Helm only, tie at 58 (kept equivalent) | Decided | [platform](matrix-platform.md#17-deployment-and-iac) |
| 18 | CI and release | GitLab CI, single SemVer, chart 0.2.0 (56) | | Decided | [platform](matrix-platform.md#18-ci-and-release) |
| 19 | Observability | kube-prometheus-stack + Loki + OTel collector, optional (52) | Site's own stack 53, within noise | Provisional: no alert rules or api/worker `PodMonitor` shipped | [platform](matrix-platform.md#19-observability) |
| 20 | Gold set | SciFact + LLM-generated, admin-curated (53) | Synthetic CI fixture 58 (a CI floor, not a gold set) | Decided; own-corpus set not built yet | [retrieval](matrix-retrieval.md#20-gold-set) |

## Area pages

- [Data tier](matrix-data.md): areas 1-4 and 13.
- [Retrieval and models](matrix-retrieval.md): areas 5-9 and 20.
- [Access, security and product](matrix-product.md): areas 10-12, 15 and 16.
- [Platform and delivery](matrix-platform.md): areas 14 and 17-19.

## Cross-cutting caveats

These caveats lower ratings or set triggers in several areas:

- **ECK pin.** All four Terraform tfvars profiles pin ECK 2.16.0, while the chart defaults to Elasticsearch 9.5.6,
  which needs an ECK 3.x release ([known limits](../enterprise/security/known-limits.md#platform-and-supply-chain),
  [upgrades](../enterprise/upgrades.md)). Areas 2 and 17.
- **No shipped alert rules or api/worker `PodMonitor`** ([observability](../enterprise/observability.md)). Areas 18 and 19.
- **Known security limits** ([known limits](../enterprise/security/known-limits.md)): the reranker sees candidate text
  before the R19 re-check, OAuth callers on `/mcp` are not PAT-gated, write-scope PATs act with full owner rights, and
  `block_export` is a deterrent. Areas 6, 10, 11, 12 and 15.
- **No feature has an eval PASS yet** ([eval README](../eval/README.md)). Every retrieval quality flag stays off, and
  the provisional rows in areas 3, 5, 6 and 8 wait on the own-corpus reference set (area 20).

## ADR gaps

Decided or provisional rows that rest on a ledger entry rather than an ADR. T17 proposes ADRs for these.

1. **LLM serving and model choice** (area 7): OpenAI-compatible endpoint only; Ollama on the GPU host with
   `qwen2.5:14b` and its `num_ctx 16384` variant; vLLM in the Terraform module. Ledger
   `.worktrees/rq/.superpowers/sdd/rq/progress.md:3` (2026-11-16).
2. **Reranker may score `exclude_llm` text** (area 6): user ruling, ledger
   `.worktrees/rq/.superpowers/sdd/rq/progress.md:34` (2026-11-16 20:38). This is security-relevant. Today it is
   recorded only in the ledger and in [known limits](../enterprise/security/known-limits.md#retrieval-and-label-timing).
3. **Elasticsearch 9.5.6 and the vector-type gate** (areas 2 and 3): recorded only in the "Outcomes" section of
   ADR-0015, with ledger `.worktrees/rq/.superpowers/sdd/rq/progress.md:41` (2026-11-16 21:05) and line 72. ADR-0002 still
   describes the Basic-licence choice without a version.
4. **Gold set source: SciFact plus generated questions** (area 20): ledger
   `.worktrees/rq/.superpowers/sdd/rq/progress.md:3` (2026-11-16). ADR-0015 covers the harness but not the source.
5. **Observability stack as separate releases without subcharts** (area 19): ledger
   `.worktrees/p11-infra/.superpowers/sdd/p11a-production-infra/progress.md:5` (P11a, 2026-11-15).
6. **Handoffs have no live mode** (area 15): ruling in the P12 ledger `.superpowers/sdd/2026-11-17-p12-docs/progress.md:45`
   (2026-11-17), matching `backend/src/roracle/handoff.py:1`. ADR-0010 does not say that contents are frozen.
7. **Dev and CI keep `bitnamilegacy/minio`** (area 4): ledger `.worktrees/iac/.superpowers/sdd/iac/progress.md:11`
   (2026-11-16 21:25). ADR-0016 covers production but not the dev and CI image.
8. **Chart 0.2.0 as a minor bump for a breaking change before 1.0** (area 18): ledger
   `.worktrees/iac/.superpowers/sdd/iac/progress.md:15` (2026-11-16). ADR-0013 defines SemVer but not the rule for
   versions before 1.0.
