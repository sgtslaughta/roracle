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
- **Reference set**: the one set with `is_reference` (set it with `PATCH /api/admin/eval/sets/{id}`); the gate needs >= 200 active
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
