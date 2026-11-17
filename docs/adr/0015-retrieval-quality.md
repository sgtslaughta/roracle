# ADR-0015: Eval-gated retrieval quality pipeline

Status: Accepted (2026-11-16). Amends ADR-0005 (chunk context prefix) and ADR-0007 (rerank depth).

## Context
Enterprise deployments may hold a very large corpus. The user ranks answer accuracy first; compute is
cheap and plentiful (RTX 4090 host `game-01`). Every retrieval index is ACL-filtered per caller
(ADR-0002) and Postgres re-checks every hit (R19); sensitivity labels (ADR-0014) keep `exclude_llm`
documents away from every LLM and `block_agents` documents away from PAT principals. Retrieval
techniques that summarise *across* documents conflict with all three.

## Decision
1. **Measure first.** An eval harness (`roracle eval`) scores gold sets — YAML/JSON fixtures and
   admin-curated sets in Postgres — **per principal** through the production retrieval path.
   Metrics: Recall@{5,10,50,100}, MRR, nDCG@10 at passage and document level, plus LLM-judged answer
   faithfulness and citation support. Any hit not visible to the principal (Postgres
   `visible_documents`, plus `llm_allowed` on the chat path) is an `acl_violation` and fails the run.
   Runs: CLI, CI (synthetic corpus, fake models), and automatically after every re-embed cutover or
   index rebuild; results are stored and shown as a trend to admins.
2. **Gain gate.** A feature is on by default only if, on the reference set (≥ 200 queries, ≥ 3
   principals, at least one non-admin), versus the baseline on the same corpus: passage nDCG@10
   +0.02 **or** passage Recall@10 +2 pts with a paired-bootstrap 95% CI lower bound > 0; passage
   Recall@100 not lower by more than 0.5 pts; faithfulness and citation support not lower by more
   than 0.02; zero ACL violations; latency within budget. Otherwise it ships behind its setting.
3. **Cross-encoder rerank**: `BAAI/bge-reranker-v2-m3` served by Hugging Face
   **text-embeddings-inference (TEI)** `89-1.9` (Ada) on `game-01`, top 200 fused passages, client
   timeout with fallback to the fused order. TEI's `/rerank` wire format (`{query, texts}` →
   `[{index, score}]`) differs from our Cohere-style contract, so `_rerank` gains a small
   `ROR_RERANK_API=cohere|tei` adapter. Chosen over vLLM (pre-allocates GPU memory, contends with
   Ollama on the same 24 GB card, multi-GB image), Infinity (native contract but stalled
   maintenance) and llama.cpp `/v1/rerank` (native contract, kept as the documented fallback, but
   weaker batching for 200-document requests). TEI is purpose-built for cross-encoders (token-budget
   dynamic batching, flash attention), allocates memory on demand, runs fully offline from a
   mounted model directory, and is maintained by Hugging Face.
4. **Contextual chunks** (Anthropic Contextual Retrieval): at ingest an LLM writes a 50–100-token
   note situating each chunk in its document; the note is indexed in a separate BM25 field and
   prepended to the embedding prompt. Prompts keep a stable document prefix so endpoint prefix
   caching applies. `exclude_llm` documents get their heading path instead and are never sent.
   The embedding prompt version is part of `prompt_sha`, so cached vectors invalidate correctly.
   Notes are cached per version in the blob store; a rebuild job backfills existing documents.
5. **Per-document RAPTOR**: section and document summaries of **one** document are indexed as extra
   retrieval units (`unit` = `section` | `document`) with the document's ACL and label fields, so
   ES filters, `acl_sync`, deletes and R19 apply unchanged. Each unit lists its `source_chunks`;
   chat expands a unit hit into its best source passages, so citations stay passage- and page-exact.
6. **Router + query-time global answers**: questions are classified specific-fact / multi-hop /
   global. Global questions map-reduce over the summaries of at most `ROR_GLOBAL_MAX_DOCS`
   candidate documents that Postgres confirms caller-visible and LLM-allowed at query time, and
   cite documents (resolved to a source passage). Multi-hop questions may use an iterative query
   decomposition loop (chat and MCP `ask`), behind a setting and the gain gate.

## Rejected: corpus-wide GraphRAG / cross-document RAPTOR
- A summary or community report built from several documents mixes their ACLs and labels. It cannot
  be post-filtered per caller (R19 works per document), so it either leaks or must be dropped for
  almost everyone.
- It goes stale on every delete, visibility change, share revoke, label raise or new version, and
  cannot be invalidated precisely without rebuilding large parts of the graph.
- Citations weaken from passage/page to "derived summary", contradicting mandatory citations.
- It helps sense-making queries but hurts specific-fact recall, the dominant research query type.
Query-time map-reduce over per-document summaries (Decision 6) covers global questions with the
caller's own visibility at the moment of asking.

## Consequences
- New service on the GPU host (TEI) and new air-gap mirror entries (image + model weights).
- Ingest and rebuild cost grows with chunk count (one LLM call per chunk, two-level summaries per
  document); compute is cheap by user decision, and results are cached per version.
- Every later retrieval change must ship with an eval report; defaults change only on measured gain.
- Context notes are cached per version keyed by `sha256(chunk text)` (R9): the key ignores the context model and
  `context_doc_chars`, so changing the model or prompt requires bumping `CONTEXT_PROMPT_VERSION`. Notes are
  untrusted output about their own document and never enter a retrieval `_source` list or an LLM prompt.
- Rebuild and re-embed are mutually exclusive (same advisory lock; re-embed refuses while `rebuild_units` are
  pending). `rebuild_units` is claimed after all other job kinds so a backfill never delays ingest.
  `ROR_CONTEXT_MAX_CHUNKS` (400) bounds notes per document; later chunks get the heading path.

## Outcomes (2026-11-17)

Implemented on branch `feat/retrieval-quality`. **Every quality feature ships OFF**: a default flips only with a PASS
report in `docs/eval/` (decision 2), and none has one yet. The gate itself (`eval/gate.py`: `MIN_QUERIES` 200,
`MIN_PRINCIPALS` 3, `NDCG_GAIN` 0.02, `JUDGE_MAX_LOSS` 0.02, paired bootstrap) and the eval harness are in place and run in CI
(`eval ci`, synthetic fixture, floors in `tests/eval/floors.json`).

| Decision | Outcome | State of the default | Report |
|---|---|---|---|
| 1-2 Eval harness + gate | Delivered: CLI (`eval run|compare|import-beir|ci`), admin API and Eval page, per-principal runs, auto-runs after a re-embed cutover / rebuild, trend. The SciFact baseline (BEIR; 300 queries, 1,200 query-principal pairs, 0 ACL violations): library search d_nDCG@10 0.770, d_Recall@10 0.887, d_Recall@100 0.971, p95 301 ms; chat path d_nDCG@10 0.753, d_Recall@10 0.887, d_Recall@100 0.972, p95 199 ms. | n/a | run log; baseline kept in the dev DB (`eval_runs`) |
| 3 Cross-encoder rerank | Delivered: TEI adapter (`ROR_RERANK_API=tei`), depth 200, timeout fallback (`roracle_rerank_fallback_total`), `deploy/game-01/reranker.sh`. **Gate on SciFact, depth 200, replace-scores: chat FAIL** (d_Recall@10 -0.039 with CI below 0, citation support -0.055), **search INSUFFICIENT** (d_nDCG@10 -0.046, d_Recall@10 -0.049). Latency was fine (p95 545 / 581 ms; depth-200 live test p95 797 ms against an 800 ms budget). Diagnosis: the adapter and the text sent are correct; a rerank-only order loses to a strong fused first stage on SciFact. Sending `title + text` and blending the min-max reranker score with the fused score (`ROR_RERANK_BLEND`, 0.3) gives +0.015 nDCG / +0.011 Recall@10, below the +0.02 gate. | **OFF** (`ROR_RERANK_URL` empty). Re-gate on the generated own-corpus set (weaker first stage, long PDFs) | `docs/eval/2026-11-16-rerank.md` (PENDING until that run) |
| 4 Contextual chunk notes | Delivered (index-time notes, `ROR_CONTEXT_MODEL`, rebuild job, `exclude_llm` fallback to the heading path). | OFF (`ROR_CONTEXT_NOTES=false`) | `docs/eval/2026-11-16-context-notes.md` PENDING |
| 5 Per-document RAPTOR units | Delivered (section + document units with `source_chunks`, expansion to real passages). | OFF (`ROR_RAPTOR=false`) | `docs/eval/2026-11-16-raptor.md` PENDING |
| 6 Router + global map-reduce | Delivered (heuristic router, optional small-model call, per-document map, bounded fan-out and admission). | OFF (`ROR_ROUTER=false`) | `docs/eval/2026-11-16-router.md` PENDING |
| 6 Agentic decomposition | Delivered for chat and MCP `ask`; the eval runner runs it for the candidate. Ledger ruling: budget = added p95 <= 1.0 s per planning round, <= 3 rounds, measured with `ROR_AGENTIC_PLANNER_MODEL` (a small fast model, dev `qwen2.5:3b`). | OFF (`ROR_AGENTIC_RETRIEVAL=false`) | `docs/eval/2026-11-17-agentic.md` PENDING |

PENDING reports hold the exact compare command; each needs a reference set with at least 200 active queries
(`is_reference`), until then every gate verdict is INSUFFICIENT and no flag may flip.

### Elasticsearch 9.5.6, vector storage and filtered search (performance work, Task 10)
- **Decision (user):** Elasticsearch **9.5.6** on a fresh cluster with a rebuild from Postgres + MinIO (no 8.19 hop; the chart
  comment documents the in-place path 8.15 -> 8.19.x -> 9.5.x for operators who keep a cluster). The Python client is 9.x.
- `index_options` are always explicit (`search_index.vector_index_options`; ES 9.1+ would otherwise pick `bbq_hnsw`
  silently). Default `int8_hnsw`. **`bbq_hnsw` (1-bit + float rescore, `ROR_VECTOR_RESCORE_OVERSAMPLE` 3) is available
  but not adopted**: it needs a recall A/B through the eval gate first (`int4_hnsw` is the fallback if recall drops).
- ACORN filtered kNN (`ROR_ES_HNSW_FILTER_HEURISTIC=acorn`) with the ACL filter inside the `knn` clause; vectors excluded from
  `_source` (`ROR_ES_EXCLUDE_SOURCE_VECTORS`, ES >= 9.2; the re-embed recomputes vectors from text, and the
  vector-reuse lookup at ingest asks for them with `exclude_vectors: false`); refresh and replica
  settings are per index at creation. Skipped on purpose: native rrf/linear retrievers and `bbq_disk` (not Basic license).
  Details: `capacity-planning.md`.

### Performance budgets (Task 10) and their measured state
Budgets live in `docs/perf/budgets.yaml`; measured vs budget at 1,000,000 documents / 1,000,000 chunks, with every
extrapolation labelled, in `docs/perf/report-2026-11-17.md`: all 12 measured budgets pass (claim is governed by p99 per
ruling; browse at 10M documents and Elasticsearch at 10M chunks are extrapolated; ingest throughput per worker cannot be
measured without docling and an LLM). The measurements found Postgres JIT (chart default `jit: off`), the HNSW worst case of
random vectors, API CPU per process, and a reconcile-gate gap (all fixed or documented there). A budget gate runs nightly
(`roracle perf suite --profile ci`, GitLab job `perf`).
Structural changes behind the numbers: job leases with a per-claim fencing token and drain (ADR-0003 amendment), keyset
browse, LSH near-duplicate lookup, sliced re-embed with a pre-swap convergence pass, explicit connection budget
(`docs/perf/connection-budget.md`).
