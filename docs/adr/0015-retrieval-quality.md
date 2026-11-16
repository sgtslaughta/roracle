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
