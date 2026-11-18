# Retrieval-quality evaluation: how the reports here are produced

`roracle eval` scores gold sets per principal through the production retrieval path (ADR-0015). A report in this directory
is the output of `roracle eval compare` for ONE feature; a default flips on only when its report says **PASS**.

## Gold sets

- **Fixture** (YAML/JSON, `backend/tests/eval/fixture.yaml`): a synthetic corpus with principals and labels; `roracle eval ci --fixture
  ... --floors tests/eval/floors.json --out ...` seeds it into a scratch index and fails on any ACL violation or a metric under the floors
  (GitLab job `eval`, no GPU, no network).
- **Admin sets** (Postgres, Eval page / `/api/admin/eval/*`): curated queries with the principals that may see each answer and a
  quote or page as relevance anchor (chunk ids are never stored). A run refuses a set whose queries reference only admins.
- **SciFact baseline** (offline): download BEIR SciFact once, then `roracle eval import-beir <dir> --set scifact --owner <non-admin
  user> --principals a,b,c` (ingests each abstract as a document; idempotent). LLM-generated own-corpus questions come from the
  Eval page (`POST /api/admin/eval/sets/{id}/generate`, drafts that an admin reviews).
- **Own-corpus generation**: `roracle eval generate --kind fact|multihop|global` (the Eval page job) drafts questions and
  `roracle eval curate` LLM-verifies, dedupes and activates them; [`own-corpus-v1`](own-corpus-v1.md) is built this way.
- **Reference set**: the one set with `is_reference` (today [`own-corpus-v1`](own-corpus-v1.md)) (set it with `PATCH /api/admin/eval/sets/{id}`); the gate needs >= 200 active
  queries and >= 3 distinct principals (>= 1 non-admin), otherwise the verdict is INSUFFICIENT. Automatic runs (after a re-embed
  cutover or a rebuild, `ROR_EVAL_ON_REINDEX`) use it.

## A/B report (what produces the files here)

```text
uv run roracle eval compare --set <reference set> --path chat --scope <all|fact|multihop|global> [--judge] \
  --baseline "k=v,..." --candidate "k=v,..." [--candidate-index <shadow index>] [--latency-budget-ms N] \
  --out docs/eval/<date>-<feature>.md
```

- Both configurations run on the same corpus; overrides are `KEY=VAL` of the eval-only keys (`eval/cli.py::EVAL_KEYS`: `rerank_url`, `rerank_api`, `rerank_model`,
  `rerank_timeout_s`, `rerank_max_chars`, `rerank_max_batch`, `rerank_blend`, `chat_candidates`, `context_notes`, `context_model`,
  `raptor`, `router_llm`, `agentic_retrieval`, `agentic_max_steps`, `agentic_max_subqueries`, `agentic_timeout_s`,
  `agentic_call_timeout_s`, `agentic_planner_model`; never `rerank_api_key`, which follows the process env) or of the admin runtime settings
  (`router`, `rerank_depth`, ...). Index-time features (context notes, RAPTOR) are built into a **shadow index** with
  `roracle rebuild --what ... --into chunks-<slug>-shadow` and compared with `--candidate-index` (runbook, "Rebuild and shadow
  indices").
- The report is the gate table: deltas with a paired-bootstrap 95% interval over queries, the verdict
  (**PASS** / **FAIL** / **INSUFFICIENT**) and its reasons, the latency check, and `acl_violations` (any hit the principal may not
  see: Postgres `visible_documents`, plus `llm_allowed` on the chat path) which fails the run. `--judge` answers and judges a
  deterministic sample of non-PAT chat pairs (`ROR_EVAL_JUDGE_SAMPLE`, model `ROR_EVAL_JUDGE_MODEL`) for faithfulness and citation support.
- Gate constants (`eval/gate.py`): `MIN_QUERIES` 200, `MIN_SCOPE_QUERIES` 50, `MIN_PRINCIPALS` 3, passage nDCG@10 gain 0.02 **or**
  Recall@10 gain 0.02 (either gain counts only with a CI lower bound > 0), Recall@100 loss <= 0.005 (the `global` scope guards document Recall@40 instead), judge loss <= 0.02; `global` scope: document-level
  Recall@10 gain 0.05 and `fact` nDCG@10 loss <= 0.005.

## Run the pending gates in the target network

Context notes, RAPTOR, the router and agentic retrieval are **PENDING**: on 2026-11-18 the only GPU here (one RTX 4090 on game-01, shared by
the LLM, the reranker and the shadow builds) was too slow, so these gates run where there is more capacity. Steps:

1. **Reference set over your corpus.** Create an empty set on Admin -> Eval, then (as in [own-corpus-v1](own-corpus-v1.md)):

   ```text
   uv run roracle eval generate --set <ref> --principal <non-admin owner> --kind fact -n 15      # repeat per kind
   uv run roracle eval generate --set <ref> --principal <non-admin owner> --kind multihop -n 15  # until each kind
   uv run roracle eval generate --set <ref> --principal <non-admin owner> --kind global -n 15    # keeps >= 50
   uv run roracle eval curate --set <ref> --principals <user2>,<user3>
   ```

   Need >= 200 active queries, >= 50 per scope and >= 3 principals (>= 1 non-admin); review the set and mark it `is_reference`
   (`PATCH /api/admin/eval/sets/{id}`). Expect about 45-70 % of generated multihop and global drafts to be rejected.
2. **One model name for every LLM call.** Ollama reloads the model on every switch between names (here: 150 s timeouts).
   Set the same name in `ROR_CHAT_MODELS`, `ROR_SUMMARY_MODEL`, `ROR_EVAL_JUDGE_MODEL`, `ROR_CONTEXT_MODEL`,
   `ROR_RAPTOR_MODEL`, `ROR_ROUTER_MODEL` and `ROR_AGENTIC_PLANNER_MODEL` for the CLI process. In `config.py` RAPTOR and the router fall back to `summary_model`
   (`qwen2.5:3b`); the runs recorded here used `qwen2.5-14b-ctx16k`. Pick the model you will run in production and record
   it in the report. Context notes need a variant with `num_ctx >= 16384`.
3. **Shadow indices** (the live index is untouched; [runbook](../enterprise/runbook-routine.md#rebuild-derived-index-fields-and-shadow-indices)):

   ```text
   uv run roracle rebuild --what context --into chunks-<slug>-shadow-ctx
   uv run roracle rebuild --what raptor  --into chunks-<slug>-shadow-raptor
   ```

   Measured here: about 0.5-1 document version per second on one RTX 4090 (LLM-bound; Ollama saturates near 1/s, so two
   workers are enough). 5,190 versions took about 2.5-3 h per index.
4. **Compares** (one compare per feature; both arms run the whole set, the scope only picks the verdict). The reranker
   passed, so keep it on in both arms (`R="rerank_url=<url>,rerank_api=tei,rerank_model=bge-reranker-v2-m3"`):

   ```text
   uv run roracle eval compare --set <ref> --path chat --scope all --judge --baseline "$R" \
     --candidate "$R" --candidate-index chunks-<slug>-shadow-ctx --out docs/eval/2026-11-16-context-notes.md
   uv run roracle eval compare --set <ref> --path chat --scope all --judge --baseline "$R" \
     --candidate "$R,raptor=true" --candidate-index chunks-<slug>-shadow-raptor \
     --latency-budget-ms <baseline p95 + 300> --out docs/eval/2026-11-16-raptor.md
   uv run roracle eval compare --set <ref> --path chat --scope global --judge --baseline "$R,router=false" \
     --candidate "$R,router=true" --latency-budget-ms <baseline p95 + 1500> --out docs/eval/2026-11-16-router.md
   uv run roracle eval compare --set <ref> --path chat --scope multihop --judge --baseline "$R" \
     --candidate "$R,agentic_retrieval=true" --out docs/eval/2026-11-17-agentic.md
   ```

   Agentic also needs the `all` scope `p_ndcg@10` delta >= -0.005 on the same two runs, and its planner p95 recorded
   (budget: added p95 <= 1.0 s per round). Use a bigger judge sample than here (`ROR_EVAL_JUDGE_SAMPLE`, default 100):
   with 10-30 judged pairs the judge intervals are very wide. The router compare timed out here during its map-reduce answers: raise `ROR_LLM_TIMEOUT_S` (here 120 s) or use an unshared GPU. Timing here (one 4090): a retrieval-only arm of 924 pairs
   took about 3-8 min, 30 judged pairs about 5 min more, router map-reduce answers longer.
5. On a PASS follow the feature report's "On PASS" section; afterwards `roracle rebuild --drop chunks-<slug>-shadow-...`.

## Reading the files

- `PENDING` = the command is recorded but the run has not been made on a >= 200-query reference set. The feature stays off.
- A recorded run that did not pass is kept in the file next to its diagnosis (see `2026-11-16-rerank.md`).
- Files: [rerank](2026-11-16-rerank.md), [context notes](2026-11-16-context-notes.md), [RAPTOR](2026-11-16-raptor.md), [router](2026-11-16-router.md), [agentic](2026-11-17-agentic.md). The compare command overwrites the file with the gate table; keep
  latency notes and PENDING context in the PR description.
- Trend: `GET /api/admin/eval/runs` and the Eval page show the stored runs per set (runs of `trigger` cli, ci, admin, model_change, reindex,
  compare). Performance budgets are a separate gate: `docs/perf/README.md`.

## Recorded SciFact evidence (BEIR, 300 queries, 1,200 query-principal pairs, 0 ACL violations)

- Baseline, library search: [`scifact/baseline-search.md`](scifact/baseline-search.md); chat path: [`scifact/baseline-chat.md`](scifact/baseline-chat.md).
- Reranker (TEI bge-reranker-v2-m3, depth 200, scores replaced): chat [`scifact/rerank-chat-200.md`](scifact/rerank-chat-200.md) (FAIL),
  search [`scifact/rerank-search-200.md`](scifact/rerank-search-200.md) (INSUFFICIENT); diagnosis and the title+text / blend variants:
  [`scifact/rerank-diagnosis.md`](scifact/rerank-diagnosis.md). Summary and verdict: [`2026-11-16-rerank.md`](2026-11-16-rerank.md).
