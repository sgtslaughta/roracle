# Retrieval-quality evaluation: how the reports here are produced

`roracle eval` scores gold sets per principal through the production retrieval path (ADR-0015). A report in this directory
is the output of `roracle eval compare` for ONE feature; a default flips on only when its report says **PASS**.

## Gold sets
- **Fixture** (YAML/JSON, `backend/tests/eval/fixture.yaml`): a synthetic corpus with principals and labels; `roracle eval ci --fixture
  ... --floors tests/eval/floors.json --out ...` seeds it into a scratch index and fails on any ACL violation or a metric under the floors
  (GitLab job `eval`, no GPU, no network).
- **Admin sets** (Postgres, Eval page / `/admin/eval/*`): curated queries with the principals that may see each answer and a
  quote or page as relevance anchor (chunk ids are never stored). A run refuses a set whose queries reference only admins.
- **SciFact baseline** (offline): download BEIR SciFact once, then `roracle eval import-beir <dir> --set scifact --owner <non-admin
  user> --principals a,b,c` (ingests each abstract as a document; idempotent). LLM-generated own-corpus questions come from the
  Eval page (`POST /admin/eval/sets/{id}/generate`, drafts that an admin reviews).
- **Reference set**: the one set with `is_reference` (set it with `PATCH /admin/eval/sets/{id}`); the gate needs >= 200 active
  queries and >= 3 distinct principals (>= 1 non-admin), otherwise the verdict is INSUFFICIENT. Automatic runs (after a re-embed
  cutover or a rebuild, `ROR_EVAL_ON_REINDEX`) use it.

## A/B report (what produces the files here)
```
uv run roracle eval compare --set <reference set> --path chat --scope <all|fact|multihop|global> [--judge] \
  --baseline "k=v,..." --candidate "k=v,..." [--candidate-index <shadow index>] [--latency-budget-ms N] \
  --out docs/eval/<date>-<feature>.md
```
- Both configurations run on the same corpus; overrides are `KEY=VAL` of the eval-only keys (`rerank_*`, `chat_candidates`,
  `context_notes`, `context_model`, `raptor`, `router_llm`, `agentic_*`; `eval/cli.py::EVAL_KEYS`) or of the admin runtime settings
  (`router`, `rerank_depth`, ...). Index-time features (context notes, RAPTOR) are built into a **shadow index** with
  `roracle rebuild --what ... --into chunks-<slug>-shadow` and compared with `--candidate-index` (runbook, "Rebuild and shadow
  indices").
- The report is the gate table: deltas with a paired-bootstrap 95% interval over queries, the verdict
  (**PASS** / **FAIL** / **INSUFFICIENT**) and its reasons, the latency check, and `acl_violations` (any hit the principal may not
  see: Postgres `visible_documents`, plus `llm_allowed` on the chat path) which fails the run. `--judge` answers and judges a
  deterministic sample of non-PAT chat pairs (`ROR_EVAL_JUDGE_SAMPLE`, model `ROR_EVAL_JUDGE_MODEL`) for faithfulness and citation support.
- Gate constants (`eval/gate.py`): `MIN_QUERIES` 200, `MIN_SCOPE_QUERIES` 50, `MIN_PRINCIPALS` 3, passage nDCG@10 gain 0.02 **or**
  Recall@10 gain 0.02 with a CI lower bound > 0, Recall@100 loss <= 0.005, judge loss <= 0.02; `global` scope: document-level
  Recall@10 gain 0.05 and `fact` nDCG@10 loss <= 0.005.

## Reading the files
- `PENDING` = the command is recorded but the run has not been made on a >= 200-query reference set. The feature stays off.
- A recorded run that did not pass is kept in the file next to its diagnosis (see `2026-11-16-rerank.md`).
- Files: rerank, context notes, RAPTOR, router, agentic. The compare command overwrites the file with the gate table; keep
  latency notes and PENDING context in the PR description.
- Trend: `GET /admin/eval/runs` and the Eval page show the stored runs per set (runs of `trigger` cli, ci, admin, model_change, reindex,
  compare). Performance budgets are a separate gate: `docs/perf/README.md`.
