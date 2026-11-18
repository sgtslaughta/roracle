# Conversational filler vs. retrieval: research for the paper RAG

Date: 2026-11-17. Scope: hybrid ES9 BM25 + embeddinggemma, TEI rerank, air-gapped, one RTX 4090, 2-3 s to first token.
Convention: [S] = claim backed by the cited URL. [E] = my estimate or inference, NOT sourced; verify by measuring.
Several fetched pages gave only abstracts; where a paper's numbers were not retrievable I say so rather than guess.

## 0. Diagnosis of the failing case

- Query "find things on robots" adds generic tokens (find, things, on) to both legs. Lexical: short abstracts that repeat "robot" and also contain "find"/"things" outscore a long paper, because BM25 normalises by length [E]. Dense: the embedding of a whole request sentence encodes "a search request" plus "robots", pulling toward short topical blurbs [E].
- ES stop filter and stop analyzer default to a small English list (the, a, and, is...) [S] https://www.elastic.co/guide/en/elasticsearch/reference/8.3/analysis-stop-tokenfilter.html . "find" and "things" are not on that list, so even with stopwords on they survive [E]. Whether your `text` fields use a stop filter depends on the mapping analyzer [S] same page; check it.
- BM25 is a scoring function, it does not itself strip filler; stopword removal is an analysis-chain choice [E, consistent with the docs above].
- Cheapest observation: the bare query already works, so the fix is "make the question look like the bare query" for the lexical leg (and ideally the dense leg).

## 1. LLM query rewriting / condensing (incl. multi-turn)

**What.** An LLM turns (history + latest message) into a standalone, search-shaped query.

- LangChain `create_history_aware_retriever`: takes chat history + question, produces a standalone query; skips rewrite when history is empty; the prompt must say "rewrite, do not answer" [S] https://python.langchain.com/docs/how_to/qa_chat_history_how_to (search summary of this page; the page now redirects to docs.langchain.com).
- LlamaIndex `condense_plus_context`: condense to standalone question, retrieve, then generate; supports `stream_chat` [S] https://developers.llamaindex.ai/python/examples/chat_engine/chat_engine_condense_plus_context/
- Cohere Chat `search_queries_only=True` returns generated search queries (one or more) plus an `is_search_required` flag, with no search or reply [S] https://docs.cohere.com/v1/docs/retrieval-augmented-generation-rag
- Azure AI Search agentic retrieval: an LLM reads the whole chat thread, decomposes into focused subqueries, adds synonyms/spelling fixes, runs them in parallel over text and vector fields; planning adds latency and is billed per token [S] https://learn.microsoft.com/azure/search/agentic-retrieval-overview
- Vespa documents query rewriting as "query focusing, enhancing, stopwords, synonyms" via rules or custom searchers [S] https://docs.vespa.ai/en/query-rewriting.html
- Rewrite-Retrieve-Read: rewrite step before retrieval, optionally with a small RL-trained rewriter; reports consistent gains on open-domain and multiple-choice QA (abstract only, no numbers retrieved) [S] https://arxiv.org/abs/2305.14283

**Evidence.** Instructed-LLM rewriting of conversational utterances reports up to +31.7% P@1, +25.2% MRR, +27% NDCG@3, +11.5% R@500 over prior rewriters on TREC CAsT [S] https://arxiv.org/pdf/2410.07797 . Fine-tuned modest LLMs can beat generic instruction-tuned ones at this task [S] (same search result set) https://arxiv.org/pdf/2410.07797 . Caveat: these are conversational-QA benchmarks, not filler-stripping for keyword legs; no source measured that directly [E].

**Local cost.** No source gave 7B latency [S: none]. Estimate [E]: an 8B model on a 4090 with a 10-token-in/20-token-out job is roughly 150-400 ms if the model is already resident (keep_alive) and prompt is short; cold model load costs seconds, so pin it. If the generator and rewriter are the same Ollama model, there is no swap.

**Fit.** Best done as one call that returns JSON: `{standalone, keywords[], title_terms[]}`. Standalone feeds the dense leg; keywords feed BM25. Skip the call when there is no history and the deterministic cleaner (section 2) already shrank the query to <= 4 content words [E].

## 2. Keyword / stopword extraction for the lexical leg

**What.** Strip request scaffolding ("find things on", "tell me about", "do we have any papers about") and run BM25 on the residue only.

- Vespa lists "query focusing" and stopword handling as first-class query rewriting [S] https://docs.vespa.ai/en/query-rewriting.html
- ES multi_match: `operator`/`minimum_should_match` control how many terms must match; with `best_fields` it applies per field, `cross_fields` per term; docs recommend `combined_fields` over `cross_fields` [S] https://www.elastic.co/docs/reference/query-languages/query-dsl/query-dsl-multi-match-query
- Weaviate hybrid takes the keyword query separately from the vector query's weighting (alpha) [S] https://docs.weaviate.io/weaviate/concepts/search/hybrid-search . In ES you can likewise send different strings to each leg of an `rrf` retriever [E, standard retriever composition; see https://www.elastic.co/docs/reference/elasticsearch/rest-apis/retrievers/rrf-retriever].

**Evidence.** No controlled study found for filler stripping in hybrid RAG [S: none]. Vendor docs treat it as standard practice (above).

**Cost.** Regex/small stoplist: microseconds [E]. Zero GPU.

**Fit.** Highest value per effort. Implement a conversational-stoplist (imperatives: find, show, list, get, search, give, tell; fillers: things, stuff, anything, papers/articles/docs when generic; prepositions on/about/regarding/related to; politeness) applied to the BM25 leg only. Keep the original string for the dense leg and the reranker (cross-encoders handle filler fine and need the full intent) [E]. Danger: over-stripping real content ("papers on things" in a title). Mitigation: if cleaning empties the query, fall back to raw.

## 3. HyDE and Query2doc

**HyDE.** LLM writes a hypothetical answer document; embed that instead of the query; encoder bottleneck filters invented facts. Beats unsupervised Contriever, comparable to fine-tuned retrievers across web search, QA, fact verification and several languages (abstract; no exact numbers retrievable) [S] https://arxiv.org/abs/2212.10496
**Query2doc.** Few-shot LLM writes a pseudo-document appended to the query; no fine-tuning; BM25 improves 3-15% on MS-MARCO/TREC DL; also helps dense retrievers (abstract) [S] https://arxiv.org/abs/2303.07678
**Risk.** LLM expansion degrades retrieval for unfamiliar or ambiguous queries; it invents entities and narrows coverage (SIGIR 2025 short) [S] https://arxiv.org/abs/2505.12694 . LlamaIndex's own HyDE demo shows a failure on an ambiguous query ("What is Bel?") [S] https://developers.llamaindex.ai/python/framework/optimizing/advanced_retrieval/query_transformations/ . Smaller LMs hallucinate more; GOLFer filters generated sentences to compensate [S] https://preview.aclanthology.org/setup/2025.findings-acl.8
**Local cost.** Generating 100-200 tokens with an 8-20B model is roughly 1-3 s on a 4090 [E], which eats the whole 2-3 s budget unless streamed in parallel with nothing else. Not suited to the default path.
**Fit.** Our failure is a short topical query ("robots") where the bare query already works; HyDE targets vocabulary mismatch, a different problem. Mismatch risk: abstracts for "robotics" are fine, but HyDE could drift to a narrow sub-topic. Use only as an optional "deep search" mode, or as an extra RRF list (never replacing the original) [E].

## 4. Multi-query with RRF

**What.** Generate N paraphrases/aspects; retrieve per query; fuse by Reciprocal Rank Fusion.

- RAG-Fusion: multi-query + RRF; authors found answers more accurate and comprehensive, but off-topic when generated queries drift; no latency data in the abstract [S] https://arxiv.org/abs/2402.03367
- LangChain MultiQueryRetriever: LLM yields alternative phrasings, results are the unique union [S] https://python.langchain.com/v0.2/docs/how_to/MultiQueryRetriever/
- Haystack QueryExpander (`n_expansions`, `include_original_query`) + MultiQueryText/EmbeddingRetriever, dedupe and sort by score [S] https://docs.haystack.deepset.ai/docs/queryexpander
- ES `rrf` retriever: `rank_constant` default 60, `rank_window_size` default 10, per-retriever `weight` (9.2+), score = weighted sum of RRF scores [S] https://www.elastic.co/docs/reference/elasticsearch/rest-apis/retrievers/rrf-retriever . Linear retriever with MinMax normalisation is the score-aware alternative [S] https://www.elastic.co/search-labs/blog/linear-retriever-hybrid-search
**Local cost.** One extra generation (3 short queries ~ 40-60 tokens, 0.3-0.8 s [E]) plus N ES+embed calls (embeddinggemma query embedding is tens of ms [E]); ES queries run in parallel. Reranker cost grows with fused candidate pool, so cap pool (e.g. 50) [E].
**Fit.** Good for the lexical leg: make the original-bare, the cleaned-keywords and one synonym/LLM variant three BM25 lists, fused with weights. The raw filler query stays in as a low-weight list so nothing is lost [E].

## 5. Title, metadata, tag boosting; doc-level vs chunk-level

**Field boosting.** ES multi_match `fields: ["title^3", "tags^3", "abstract^2", "body"]`; `best_fields` = dis_max (best single field), `tie_breaker` adds others; `combined_fields` preferred for BM25F-style cross-field scoring [S] https://www.elastic.co/docs/reference/query-languages/query-dsl/query-dsl-multi-match-query . Vespa tutorial combines `bm25(title)` and `bm25(body)` with vector closeness in a hybrid rank expression [S] https://docs.vespa.ai/en/tutorials/hybrid-search.html . Vertex AI Search supports boost specs in [-1, 1] on conditions (max 20), affecting first 1,000 docs [S] https://docs.cloud.google.com/generative-ai-app-builder/docs/ranking-overview
**Why it fixes our case.** "Scalability in Computing and Robotics" plus tag #robotics are short, high-signal fields; BM25 on title/tags is immune to body length, so the long paper scores on exactly the field that matches [E].
**Contextual chunk headers / title prefix.** Anthropic: prepending LLM-generated chunk context before embedding and BM25 cut top-20 retrieval failure from 5.7% to 3.7% (35%) for embeddings, 2.9% (49%) with contextual BM25, 1.9% (67%) with reranking; ~US$1.02 per million document tokens one-off with prompt caching [S] https://www.anthropic.com/news/contextual-retrieval . A cheaper variant (prefix "Title | tags | section" instead of LLM context) is the same idea with no generation cost [E; no measured gain found for that variant].
**Late chunking** (embed whole doc then pool per chunk) is an alternative needing long-context embedders [S] https://weaviate.io/blog/late-chunking
**Parent-document / doc-level.** LangChain ParentDocumentRetriever embeds small child chunks and returns the larger parent [S] https://python.langchain.com/docs/how_to/parent_document_retriever . For us: score at chunk level, then aggregate to doc (max or top-2 sum) and also keep a doc-level index (title+abstract+tags, one vector) as a third RRF list [E]. A long paper otherwise competes as many diluted chunks against abstract-sized docs that are one dense chunk [E].
**Cost.** Index-time only (re-embed once). Query-time: one more ES sub-query. Negligible latency [E].

## 6. Diversity / MMR

- MMR (Carbonell & Goldstein, SIGIR 1998): greedy; picks the item maximising relevance minus similarity to already-picked; lambda trades relevance vs novelty [S] https://people.eng.unimelb.edu.au/ammoffat/sigir98/abstracts/carbonell.html ; limits: post-processing, pairwise only [S] https://arxiv.org/pdf/2212.14464
- Elasticsearch has a `diversify` retriever applying MMR over a child retriever's top-N: requires a `dense_vector` field, `lambda` 0..1 (higher = more similar to query), `rank_window_size`, `size`; preview from 9.3, listed GA from 9.4 per one doc build; order of survivors is preserved [S] https://www.elastic.co/docs/reference/elasticsearch/rest-apis/retrievers/diversify-retriever . Check your exact 9.x minor.
- Note: MMR is similarity-based, so near-duplicate short abstracts get penalised but "one doc, many chunks" is better solved by collapsing on doc id [E].
**Evidence of gain for our failure:** none found [S: none]. It is a guard against crowding, not the root fix.
**Cost.** O(k^2) on ~50 vectors, sub-ms to a few ms [E].
**Fit.** Cap chunks per doc (e.g. <= 2) before the reranker (ES `collapse`/`inner_hits` or in app code), then optional MMR lambda ~0.7 [E]. Postgres access re-check should run after diversity, on the final ~10, to avoid wasted checks; but over-fetch so dropped hits can be replaced [E].

## 7. Evaluation

- Pinecone: Recall@k = TP@k / (TP@k + FN@k), useful when missing a relevant doc is costly; recall ignores rank, MRR is rank-aware; also MAP@K, NDCG@K [S] https://www.pinecone.io/learn/offline-evaluation and https://www.pinecone.io/learn/series/rag/rag-evaluation/
- Hit rate = share of queries with >= 1 relevant hit in top k; can look good while coverage is poor [S] https://getautonoma.com/blog/how-to-test-rag-retrieval (third-party)
- Practice: freeze a corpus, label query -> expected doc/chunk IDs, track recall@k and MRR, gate in CI [S] https://www.kunalganglani.com/blog/rag-evaluation-metrics-retrieval-quality (third-party).
**Recommended small set [E]:** 40-80 queries, each as a triple: bare ("robots"), filler ("find things on robots", "what do we have on robots?"), and multi-turn follow-up; label at doc level (a long paper should count when any chunk hits). Metrics: doc-level recall@10 and recall@50 (pre-rerank), MRR, plus "long-doc share in top 10". Seed it with this very failure. Compare the bare vs filler gap: the goal is gap -> 0 (filler recall@10 ~ bare recall@10). Include a latency column (p50/p95 time to first token).

## 8. What big vendors do (context)

- Glean: enterprise-specific embeddings plus classical IR, personalisation, query understanding for acronyms/jargon (secondary sources) [S] https://www.glean.com/blog/unlocking-the-power-of-vector-search-in-enterprise ; https://www.zenml.io/llmops-database/building-robust-enterprise-search-with-llms-and-traditional-ir
- Perplexity: hybrid scoring by embeddings and full text on Vespa; no public detail on query rewriting found [S] https://blog.vespa.ai/perplexity-show-what-great-rag-takes/
- Azure: LLM query planning, decomposition, parallel subqueries (see section 1).
- Not found within effort: primary Pinecone, Weaviate, or Vertex posts specifically on filler removal. Weaviate's hybrid docs give an alpha weight between BM25 and vector (sources disagree on the default: 0.5 vs 0.75) [S] https://docs.weaviate.io/weaviate/concepts/search/hybrid-search

## 9. Latency budget sketch (target first token <= 2-3 s) [E]

| Step | Est. |
|---|---|
| Regex clean | <1 ms |
| Optional 8B rewrite (resident) | 200-500 ms |
| Embed query (embeddinggemma) | 20-60 ms |
| ES 3-4 parallel sub-queries | 30-100 ms |
| TEI rerank 50 pairs | 100-300 ms |
| Postgres access check | 10-30 ms |
Total before generation with rewrite ~0.5-1.0 s; leaves room for the answer model's prefill. Skip HyDE in the default path.

## 10. Ranked recommendation

**Tier 1, cheap (hours, no new models).**

1. Deterministic filler stripper for the BM25 leg only (section 2); fall back to raw if empty. Dense and reranker keep the raw query.
2. Field-boosted lexical query: `combined_fields`/multi_match over title^3, tags^3, abstract^2, body; add the bare-cleaned query as a second BM25 list in the `rrf` retriever, with weights (raw 0.5, cleaned 1.0) [S for retriever params: https://www.elastic.co/docs/reference/elasticsearch/rest-apis/retrievers/rrf-retriever].
3. Cap chunks per doc before rerank (collapse on doc id). Build the 50-query eval set and record the baseline gap.

**Tier 2, medium (days).**
4. Re-index with a title + tags prefix on each chunk text before embedding and BM25 (cheap cousin of contextual retrieval, which measured 35-49% fewer failures with LLM context [S https://www.anthropic.com/news/contextual-retrieval]).
5. Add a doc-level vector (title+abstract+tags) as an additional RRF list.
6. Local LLM condense/rewrite with JSON output (standalone + keywords), only when history exists or the cleaner leaves > 6 content words; pin the model with keep_alive.

**Tier 3, advanced (only if evals still show gaps).**
7. Multi-query (2-3 variants) + RRF on the lexical and dense legs, original always included (risk of drift per RAG-Fusion [S https://arxiv.org/abs/2402.03367]).
8. LLM-generated contextual chunk headers at ingest (one-off GPU cost, offline).
9. ES `diversify` MMR (needs your minor version >= 9.3/9.4 [S see section 6]).
10. HyDE/Query2doc as an opt-in "deep search", never default (hallucination risk on unfamiliar queries [S https://arxiv.org/abs/2505.12694]).

## 11. Status and next steps (2026-11-17)

Done (Tier 1, items 1-3; no reindex):

- `chat/intent.py` `lexical_query`: closed English filler list for the chat BM25 leg only; kNN and the reranker keep
  the raw question; empty result falls back to raw. Non-English words are never in the list, so never stripped.
- `chat/lexical.py`: chat BM25 over `title^3`, `abstract^2` (head chunk), `text`/`headings`/`context` (1, tie-breaker
  0.3) plus tag clauses (exact term boost 3; prefix of the de-pluralised word, 5+ chars, boost 1.5).
- `ROR_CHAT_CHUNKS_PER_DOC` (default 3): per-document cap on the fused list before the rerank; not applied when the
  scope is too few documents to fill the context (open tabs, tiny collections). 3 rather than the suggested 2 keeps
  the best paper's second and third passages for the answer while still leaving 60+ documents in a 200-chunk window.
- Eval: `backend/tests/eval/query_recall.yaml` (43 queries: bare / filler / follow-up / tag-only) and
  `tests/test_eval_query_recall.py` (doc-level recall@8 on the chat path, seeded ES, hash embedder, kNN floor 0, no
  reranker). Before: bare 1.000, filler 0.911, follow-up 0.974, tag-only 0.500; the three robots filler variants
  cited only the toddler papers. After: 1.000 on all four.

Next steps (out of scope here):

- Measure on the live corpus with the TEI reranker on: the offline eval has no cross-encoder, so a filler-sensitive
  reranker could still demote the long paper (`rerank_blend` 0.3 limits the damage).
- Second BM25 list with the raw question at low RRF weight (item 2's "raw 0.5, cleaned 1.0").
- Tier 2: title + tags prefix on chunk text at index time (reindex), doc-level vector as an extra RRF list, LLM
  condense/rewrite only when history exists (real follow-ups carry the topic in earlier turns; the eval's
  "follow-up" variants are single-turn).
