# Evaluation

Evaluation measures how well retrieval finds the right papers for the right people. You keep **gold sets** (questions with the papers that answer them), **run** them, watch the **trend**, and use **compare** to decide whether a retrieval feature may be switched on. Everything here is admin-only, because a gold question or a gold paper can reveal that a private paper exists. The method, the recorded reports and the SciFact numbers are in [docs/eval](../eval/README.md). Terms are in the [glossary](../glossary.md).

The work is split between two places:

| Task | Where |
|---|---|
| Browse sets, approve or delete drafts, generate drafts, make a set the reference, run now, read the trend | **Admin, Eval** in the web app |
| Add or import queries, patch or delete them | API only (`/api/admin/eval/...`) |
| Seed the SciFact mirror, run from a terminal, **compare** with a gate verdict | `roracle eval ...` on the command line |
| Fixture check in the pipeline | `roracle eval ci` |

<!-- screenshot: admin-eval: the Eval tab with the sets table, the run controls and the trend chart -->

## Gold sets

A gold set is a named list of queries. Each query has:

- the **text**, up to 2000 characters, and a **kind**: `fact`, `multihop` or `global`;
- one or more **principals**, the users the question is asked as. At least one in the set must be a non-admin user, because an admin sees every paper and tests nothing. A set whose principals are all admins is refused with `evaluate as at least one non-admin principal`;
- the **gold papers**: each is a document id with a quote or a page as anchor and a grade from 1 to 3 (default 3). Chunk ids are never stored, so a re-chunked paper keeps its gold;
- an optional reference answer, and **as_pat**, which also asks the question as that user's read-only access token;
- a **status**: `draft` or `active`. Runs use active queries only.

Only the papers a principal can see count: a gold paper the principal may not see is left out of that principal's expected list.

### Where the questions come from

**SciFact mirror.** A public benchmark, imported offline from a local directory you have already downloaded and carried in:

```text
roracle eval import-beir <dir> --set scifact --owner <non-admin user> [--principals A,B,C] [--share-group GROUP] [--visibility private|shared|everyone]
```

It ingests each abstract as a paper owned by `--owner`, so the worker does the processing. Run the command again after the queue drains to finish; it is safe to repeat, keyed by `--dataset` (default `scifact`). It exits 2 on bad input and 1 on a failure. See the [reference](../reference/cli.md#roracle-eval-import-beir).

**Generated from your own library.** Open a set, choose how many questions (1 to 200) and a principal, and press **Generate drafts**. The server queues an `eval_generate` job (API: `POST /api/admin/eval/sets/{set_id}/generate`). For each sampled paper it picks one random passage, asks the model for a question and a verbatim quote, and keeps the pair only if the quote really occurs in the passage.

- The principal must be a non-admin user. The job reads only papers that user can see and whose label allows LLM use (`exclude_llm` papers are never sampled). It checks the label again in Postgres right before each passage is sent to the model.
- The model is `ROR_EVAL_JUDGE_MODEL`, or the first chat model when that is empty.
- The results are saved as drafts with kind `fact`. Nothing is evaluated until you approve them.

**By hand or from a file.** The API creates one query (`POST /api/admin/eval/sets/{set_id}/queries`) or imports a YAML or JSON file with a top-level `queries` list (`POST /api/admin/eval/sets/{set_id}/import`, up to 2 MiB; a repeated key in the file, or a key already used in the set, is refused). Principals are user ids and gold papers are document ids. A document that does not exist or is deleted is refused with `422`.

### Curating

Open a set from the **Sets** table. Drafts are listed first. **Approve** makes a draft active, and **Delete** removes a query. Approving checks again that the principals exist and the gold papers still exist. Read each draft before approving: the generator only checks that the quote exists, not that the question is a good one.

Sets also have a **Make reference** button. Exactly one set is the **reference set**; making another one takes the flag from the old one. The reference set is the only set a compare gate or an automatic run uses.

## Runs and the trend

Under **Runs** pick a set, a **path** and press **Run now**.

- `chat` follows the chat retrieval path (what the model would get). `search` follows library search.
- **Judge answers** is only available on `chat`. It has the model answer a sample of questions and grade the answers for faithfulness and citation support. The sample is `ROR_EVAL_JUDGE_SAMPLE` pairs (default 100), the same pairs on every run of a set.
- The run is a job of kind `eval_run`; follow it in **Admin, Jobs**. An eval run that fails is not retried automatically, because a judged run is expensive.

Each run is stored with its **trigger**: `cli`, `ci`, `admin` (Run now), `compare`, `model_change` and `reindex`. `reindex` runs start themselves: when a [re-embed](index-maintenance.md#change-the-embedding-model) finishes its last catch-up round, and after a [rebuild into the active index](index-maintenance.md#rebuild-derived-index-fields), a judged run on the reference set is queued. Switch that off with `ROR_EVAL_ON_REINDEX=false`. A run that finds a paper a principal may not see ends as `failed` with `acl_violations: <n>`. Treat that as a security finding.

The chart plots one line per ticked metric across runs, oldest first. The table lists runs with their scores, and **Details** shows the scores by kind and by principal and the twenty worst question and principal pairs by recall@10 with the top papers returned. The metrics are `p_ndcg@10`, `p_recall@10`, `p_recall@100`, `p_mrr` (passage level) and, from judged runs, `faithfulness` and `citation_support`.

A fall in the trend after a model change or a rebuild is the signal to roll back. API: `GET /api/admin/eval/runs?set_id=` and `GET /api/admin/eval/runs/{run_id}`.

CLI: `roracle eval run --set NAME [--path chat|search] [--judge] [--label L] [--set-override KEY=VAL] [--index INDEX] [--json OUT]` scores a stored set and prints a table. It exits 1 on an ACL violation and 2 on unusable input. `--index` reads a named index instead of the active one.

## Compare and the gates

Compare is how a feature earns its place. It runs the reference set twice, a **baseline** and a **candidate** that differ in a few settings, and writes a report with a verdict. It is a command, not a screen:

```text
roracle eval compare --set <reference set> --path chat --scope all \
  --baseline "k=v,..." --candidate "k=v,..." [--candidate-index <shadow index>] \
  [--latency-budget-ms N] [--judge] --out docs/eval/<date>-<feature>.md
```

`--scope` is `all`, `fact`, `multihop` or `global`. A feature that needs new index content (context notes, RAPTOR) is built into a [shadow index](index-maintenance.md#rebuild-derived-index-fields) and judged with `--candidate-index`. The overrides are `KEY=VAL` pairs of the runtime settings or of the evaluation-only keys listed in [docs/eval](../eval/README.md). The command always writes the report when both runs finish. It exits 1 if either run found an ACL violation and 2 on bad input. A FAIL verdict is a result, not an error.

The verdict is one of three words.

| Verdict | Meaning |
|---|---|
| **PASS** | A safe, significant gain on a large enough reference set |
| **FAIL** | A firm problem, whatever the size of the set |
| **INSUFFICIENT** | The set is too small or the wrong shape to judge, or there is no gain |

FAIL beats INSUFFICIENT. The rules, from the gate code:

- **FAIL** on any ACL violation, a recall@100 loss over 0.005 (the `global` scope guards document recall@40 instead), a judge score loss over 0.02, or a latency p95 over the budget you gave. With `--scope global` it also fails if `fact` nDCG@10 drops by more than 0.005.
- **INSUFFICIENT** when the runs are not on the reference set, or differ in set or path, or have fewer than 200 queries in both, fewer than 3 principals with at least one non-admin, fewer than 50 queries in a chosen scope, or a metric is missing (a chat gate needs `--judge` on both sides).
- **PASS** needs, with a confidence interval whose lower bound is above 0, an nDCG@10 gain of at least 0.02 or a recall@10 gain of at least 0.02. For `global` scope the gain is document recall@10 of at least 0.05. A missing gain on a set that is too small is INSUFFICIENT; on a large enough set it is FAIL.

The recorded reports and their verdicts are in [docs/eval](../eval/README.md). A default flips on only when its report says PASS.

## The CI job

The pipeline job `eval` runs `roracle eval ci --fixture tests/eval/fixture.yaml --floors tests/eval/floors.json --out eval-report.json`. It seeds a small synthetic corpus (documents, principals and labels) into a scratch index and a database transaction that is rolled back, runs both paths with offline stand-in models and fails on any ACL violation or any metric under its floor. It needs no GPU and no network, and it never touches your library. Exit 0 is a pass, 1 a violation or error, 2 bad input. It guards access control and gross regressions; it does not replace a compare on your own papers.

## The router and RAPTOR preconditions

Two features stay off until their report in [docs/eval](../eval/README.md) holds a PASS: the query **router** (`router`, a runtime setting) and RAPTOR summary units (`ROR_RAPTOR`). Both reports currently say PENDING, with no own-corpus run recorded. Before turning either on:

- Build a reference set of at least 200 active queries with at least 3 principals, one or more of them non-admin, and, for a scoped compare, at least 50 queries of that kind.
- RAPTOR is built into a shadow index first (`roracle rebuild --what raptor --into chunks-<slug>-shadow`) and judged with `--candidate-index`. `rebuild --what raptor` turns RAPTOR on for the rebuild even while the setting is off, so a large rebuild is a large number of model calls. Each paper gets at most `ROR_RAPTOR_MAX_SECTIONS` (default 64) section summaries.
- The in-place RAPTOR rebuild (`roracle rebuild --what raptor` without `--into`) re-chunks each paper and checks only that the embedding model is unchanged. There is no chunking-version guard, so do not run it while the chunker is being changed.
- With `router_llm` on, the router's model call runs while the request still holds its database session. Leave `router_llm` off (the default, environment-only). The heuristic router, which makes no model call, is not affected.

See also [index maintenance](index-maintenance.md), [runtime settings](runtime-settings.md) and the [audit log](audit.md#what-is-recorded) for the `eval.*` actions.
