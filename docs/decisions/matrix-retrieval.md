# Decision matrix: retrieval and models

Areas 5-9 and 20 of the [decision matrix](decision-matrix.md). The method, weights and status words are defined there.
**Every rating is judgement** (1 worst, 5 best; for ops cost, 5 is cheapest to run). Rating cell: `A` accuracy ×3,
`S` security ×3, `G` air-gap fit ×3, `P` performance/scale ×2, `L` licence ×2, `O` ops cost ×1. The maximum weighted
total is 70. **Rule for this page:** an option with no evaluation report rates A3 ("unknown"). It moves only when a
report in [docs/eval](../eval/README.md) records a verdict. Licence notes are general knowledge: confirm them with your
legal team.

The retrieval quality gate ([ADR-0015](../adr/0015-retrieval-quality.md) decision 2) turns a feature on by default only
when its report says PASS. As of 2026-11-17 no feature has a PASS, so every quality feature ships off.

## 5. Embedding model

The model is configuration: each model gets its own index generation behind the `chunks` alias
([ADR-0006](../adr/0006-embedding-model-config.md), [search index lifecycle](../architecture/search-index-lifecycle.md)).
Defaults: `ROR_EMBED_MODEL` = `embeddinggemma`, `ROR_EMBED_DIMS` = 768 (`backend/src/roracle/config.py:85-86`).

| Option | Ratings | Total | Decision | Rationale | ADR | Status / trigger |
|---|---|---|---|---|---|---|
| EmbeddingGemma-300m (768-d) | A4 S3 G5 P5 L3 O5 | 57 | **Chosen** (default) | Small vectors drive Elasticsearch sizing (ADR-0006). SciFact baseline through the production path: document nDCG@10 0.770 and Recall@100 0.971 on search, 0.753 and 0.972 on chat, with 0 ACL violations ([search](../eval/scifact/baseline-search.md), [chat](../eval/scifact/baseline-chat.md)). The Gemma terms of use are a custom licence. | [0006](../adr/0006-embedding-model-config.md) | **Provisional.** Revisit when own-corpus eval data exists (generated reference set, at least 200 queries) |
| e5-mistral-7b (4096-d) | A3 S3 G4 P2 L4 O3 | 45 | Not adopted | About 5x the vector size, so about 5x the Elasticsearch RAM (ADR-0006). Multi-GB weights to mirror. Not measured here. | [0006](../adr/0006-embedding-model-config.md) | Revisit together with row 1, via re-embed and an eval compare |

Security is rated the same for both, because embeddings are computed for every chunk regardless of label
([known limits](../enterprise/security/known-limits.md#labels-and-handoffs)).

## 6. Reranker

Cross-encoder `bge-reranker-v2-m3` served by TEI, with a client timeout that falls back to the fused order
([ADR-0015](../adr/0015-retrieval-quality.md) decision 3). Code defaults: `ROR_RERANK_URL` empty (reranking off),
`ROR_RERANK_DEPTH` 200 for chat and other LLM-bound callers, `ROR_SEARCH_RERANK_DEPTH` 50 for `/search`,
`ROR_RERANK_BLEND` 0.3, `ROR_RERANK_MAX_CHARS` 2000 (`backend/src/roracle/config.py:124,131,160-162`). The chart
default `rerank.url` is also empty.

| Option | Ratings | Total | Decision | Rationale | ADR | Status / trigger |
|---|---|---|---|---|---|---|
| No reranker (fused RRF order) | A4 S5 G5 P5 L5 O5 | 67 | **Chosen** (current default) | The fused first stage beat the reranker on SciFact ([rerank report](../eval/2026-11-16-rerank.md)). No passage text leaves for a scoring service. | [0015](../adr/0015-retrieval-quality.md) | Provisional. Revisit on the own-corpus re-gate |
| TEI `bge-reranker-v2-m3`, depth 200, scores replaced | A3 S4 G4 P3 L4 O3 | 50 | Failed gate | SciFact chat FAIL (d_Recall@10 -0.039, citation support -0.055). Search INSUFFICIENT. Live p95 797 ms at 2000-char passages, against an 800 ms budget ([rerank report](../eval/2026-11-16-rerank.md)). | [0015](../adr/0015-retrieval-quality.md) | Off |
| TEI, depth 200, `title + text`, blend 0.3 (the code default once a URL is set) | A4 S4 G4 P3 L4 O3 | 53 | Candidate | +0.015 nDCG@10 and +0.011 Recall@10 on SciFact, below the +0.02 gate ([rerank report](../eval/2026-11-16-rerank.md)). The reranker may score `exclude_llm` text (user ruling, ledger `.worktrees/rq/.superpowers/sdd/rq/progress.md:34`, 2026-11-16 20:38). During the `acl_sync` window it can see just-revoked text ([known limits](../enterprise/security/known-limits.md#retrieval-and-label-timing)). | [0015](../adr/0015-retrieval-quality.md); exclude_llm ruling: ADR: none, see ledger (2026-11-16) | Provisional, eval-gated: PENDING the own-corpus set |
| TEI, depth 100 | A3 S4 G4 P4 L4 O3 | 52 | Not measured | About half the reranker latency. ADR-0015 chose 200 for long PDFs. Lowering `ROR_RERANK_MAX_CHARS` or the depth is the documented mitigation if latency bites ([rerank report](../eval/2026-11-16-rerank.md)). | [0015](../adr/0015-retrieval-quality.md) | Revisit if depth 200 passes on accuracy but misses the latency budget |

## 7. LLM serving and models

The backend speaks only the OpenAI-compatible API (`ROR_LLM_BASE_URL`, used for chat and embeddings). Model names are
settings: `ROR_CHAT_MODELS` default `qwen2.5:14b`, `ROR_SUMMARY_MODEL` default `qwen2.5:3b`, `ROR_AGENTIC_PLANNER_MODEL`
empty, meaning the first chat model (`backend/src/roracle/config.py:110,144,210`). The user approved the GPU host setup
with a `num_ctx 16384` variant of `qwen2.5:14b` and TEI (ledger `.worktrees/rq/.superpowers/sdd/rq/progress.md:3`,
2026-11-16). That variant serves `ROR_CONTEXT_MODEL` ([air-gap mirror manifest](../enterprise/airgap-mirror-manifest.md)).
The optional Terraform `inference` module runs a vLLM-style server and is disabled by default (ADR-0016).

| Option | Ratings | Total | Decision | Rationale | ADR | Status / trigger |
|---|---|---|---|---|---|---|
| Ollama on a GPU host (`qwen2.5:14b`, ctx-16k variant; `qwen2.5:3b` for the planner and summaries) | A4 S3 G5 P3 L4 O4 | 54 | **Chosen** for dev and the GPU host | Simple model import from a mirror (`ollama create`). Weaker batching under concurrency, and no API key by default. | ADR: none, see ledger (2026-11-16) | Provisional. The Qwen2.5 3B and 14B checkpoints are not published under the same licence: check each mirrored model before production |
| vLLM (Terraform `inference` module) | A4 S4 G4 P5 L5 O3 | 59 | **Chosen** for in-cluster GPU (optional) | Continuous batching. The module enforces an LLM API key and a default-deny NetworkPolicy (ADR-0016). The image is several GB. | [0016](../adr/0016-terraform-iac.md) | Provisional. Off by default |

Both rows are engines behind the same API, so the real decision is "any OpenAI-compatible endpoint". The models
themselves are provisional until own-corpus faithfulness data exists.

## 8. Retrieval techniques

Hybrid BM25 plus kNN, both ACL-filtered, fused with RRF (`k = 60`, `backend/src/roracle/retrieval.py:74`), then the
Postgres R19 re-check ([ADR-0007](../adr/0007-retrieval.md), [retrieval and chat](../architecture/retrieval-chat.md),
[glossary](../glossary.md#access)). Flags and defaults: `ROR_CONTEXT_NOTES`, `ROR_RAPTOR`, `ROR_ROUTER` and
`ROR_AGENTIC_RETRIEVAL` are all `false` (`backend/src/roracle/config.py:172,177,183,201`).

| Option | Ratings | Total | Decision | Rationale | ADR | Status / trigger |
|---|---|---|---|---|---|---|
| Hybrid BM25 + kNN, app-side RRF | A4 S5 G5 P5 L5 O5 | 67 | **Chosen** (always on) | SciFact baseline as in area 5. Chat retrieval p95 352 ms (budget 800, [perf report](../perf/report-2026-11-17.md)). The ACL filter applies in both legs. | [0002](../adr/0002-elastic-basic-app-acl.md), [0007](../adr/0007-retrieval.md) | Decided |
| Contextual chunk notes | A3 S4 G5 P3 L5 O3 | 55 | Shipped off | One LLM call per chunk at ingest (`ROR_CONTEXT_MAX_CHUNKS` bounds it). `exclude_llm` documents fall back to the heading path. | [0015](../adr/0015-retrieval-quality.md) | Provisional: [report](../eval/2026-11-16-context-notes.md) PENDING |
| Per-document RAPTOR units | A3 S5 G5 P3 L5 O3 | 58 | Shipped off | Each unit carries its own document's ACL and label fields, so filters and R19 apply unchanged. RAPTOR has a 1.1 s chat budget ([budgets.yaml](../perf/budgets.yaml)) that the perf run did not exercise. | [0015](../adr/0015-retrieval-quality.md) | Provisional: [report](../eval/2026-11-16-raptor.md) PENDING |
| Router + query-time global map-reduce | A3 S5 G5 P3 L5 O3 | 58 | Shipped off | Maps only over documents that Postgres confirms are visible and LLM-allowed at query time (`ROR_GLOBAL_MAX_DOCS`). | [0015](../adr/0015-retrieval-quality.md) | Provisional: [report](../eval/2026-11-16-router.md) PENDING |
| Agentic decomposition loop | A3 S5 G5 P2 L5 O3 | 56 | Shipped off | Budget: up to 1.0 s of added p95 per planning round, at most 3 rounds ([agentic report](../eval/2026-11-17-agentic.md)). | [0015](../adr/0015-retrieval-quality.md) | Provisional: report PENDING |
| Corpus-wide GraphRAG or cross-document RAPTOR | A3 S1 G4 P2 L4 O1 | 37 | **Rejected** | Mixes the ACLs and labels of several documents and cannot be post-filtered per caller (R19 works per document). It goes stale on every delete, share or label change, and weakens citations to "derived summary". | [0015](../adr/0015-retrieval-quality.md) | Decided |

The four optional techniques rank below the baseline only because the A3 rule applies until a report exists. Each one
flips on with a PASS report, not because of this matrix.

## 9. Votes boost

| Option | Ratings | Total | Decision | Rationale | ADR | Status / trigger |
|---|---|---|---|---|---|---|
| Smoothed ratio `(up + 1) / (up + down + 2)`, multiplier capped at ±15 % | A4 S5 G5 P5 L5 O5 | 67 | **Chosen** | Votes refine relevance but never override it (`backend/src/roracle/retrieval.py:83,87`). | [0007](../adr/0007-retrieval.md) (amended) | Decided (user decision 2026-11-15, ledger `.superpowers/sdd/2026-11-15-p3-search/progress.md:41`) |
| Wilson lower bound | A2 S5 G5 P5 L5 O5 | 61 | Replaced | Ranked documents with 1-3 upvotes below unvoted ones (ADR-0007 amendment). | [0007](../adr/0007-retrieval.md) | Decided |

## 20. Gold set

| Option | Ratings | Total | Decision | Rationale | ADR | Status / trigger |
|---|---|---|---|---|---|---|
| SciFact (BEIR, mirrored) baseline + LLM-generated own-corpus questions, admin-curated | A4 S4 G4 P4 L3 O3 | 53 | **Chosen** | SciFact gives a public baseline: 300 queries, 1,200 query-principal pairs ([eval README](../eval/README.md)). Generated questions cover long PDFs and a weaker first stage. Eval endpoints are admin-only, because gold sets reveal that private documents exist. Check the SciFact dataset licence before redistributing it. | [0015](../adr/0015-retrieval-quality.md) decision 1; SciFact choice: ADR: none, see ledger (2026-11-16) | Decided (user decision, ledger `.worktrees/rq/.superpowers/sdd/rq/progress.md:3`). The own-corpus set does not exist yet, which keeps every gate PENDING |
| SciFact only | A2 S5 G4 P4 L3 O5 | 52 | Rejected | The reranker result shows that SciFact transfers poorly: its fused first stage is unusually strong ([rerank report](../eval/2026-11-16-rerank.md)). | none | Decided |
| Synthetic CI fixture only | A1 S5 G5 P5 L5 O5 | 58 | CI floor only | Guards regressions in `eval ci` (floors in `backend/tests/eval/floors.json`); says nothing about real quality. | [0015](../adr/0015-retrieval-quality.md) (outcomes) | Kept as the CI gate |

The synthetic fixture ranks highest because it costs nothing and touches no real data. Its A1 is why it cannot be the
gold set: under these weights, accuracy alone does not dominate. Read this row together with that caveat.
