# Eval harness

`roracle eval` scores gold sets per principal through the production retrieval path, as each real principal, and re-checks every hit against Postgres independently of the retrieval code. A surviving hit on a document the principal cannot see is an ACL violation and fails the run. Admins run it from the UI ([admin evaluation guide](../admin/eval.md)); developers run it from the CLI. The methodology, gate constants and the reports that gate retrieval defaults are in [docs/eval](../eval/README.md); the rationale is ADR-0015 in the [ADR index](../adr/README.md). Flags are in the [CLI reference](../reference/cli.md).

Code: `backend/src/roracle/eval/` (`cli.py` entry, `ci.py` the CI command, `runner.py`, `gold.py`, `metrics.py`, `gate.py`, `compare.py`, `fakes.py`, `fixture.py`, `beir.py`).

## Commands

Exit codes for all subcommands: 0 ok, 1 an ACL violation or a run error, 2 unusable input.

| Command | Use |
|---|---|
| `roracle eval ci --fixture F --floors FL --out OUT` | seed the synthetic fixture and fail on ACL violations or metrics under the floors; offline (no GPU, no network) |
| `roracle eval run --set NAME [--path chat\|search] [--judge] [--set-override KEY=VAL] [--index IDX] [--json OUT]` | score one admin gold set stored in Postgres; `--fixture` is not implemented (use `eval ci`) |
| `roracle eval compare --set NAME --out docs/eval/FILE.md ...` | A/B two configurations on the same corpus and write the gate report (PASS, FAIL or INSUFFICIENT) |
| `roracle eval import-beir DIR --set NAME --owner USER` | seed a local BEIR (SciFact) directory as documents plus a gold set; idempotent, re-run after the worker drains |

`--set-override KEY=VAL` and `--baseline` or `--candidate` accept the eval-only keys in `EVAL_KEYS` (`eval/cli.py`: reranker, contextual notes, RAPTOR, router, agentic and `chat_candidates` settings) and the admin runtime settings. `rerank_api_key` is deliberately excluded so it never reaches the stored run config. `--index` and `--candidate-index` must name one existing index (not an alias) built with the effective embedding model and dimensions.

## The CI job `eval`

Defined in `.gitlab-ci.yml`: stage `test`, an `elasticsearch:9.5.6` service (512 MB heap) and Postgres, database `roracle_eval`. Its script:

```sh
python -m roracle migrate --wait 60 && python -m roracle eval ci \
  --fixture tests/eval/fixture.yaml --floors tests/eval/floors.json --out eval-report.json
```

What `eval ci` does (`eval/ci.py`):

1. Waits up to 120 s for Elasticsearch, then creates the throwaway index `chunks-evalci-1` and deletes it at the end, pass or fail.
2. Seeds the fixture inside one Postgres transaction that is rolled back at the end, using the offline fakes (hashed bag-of-words embeddings, an extractive answerer, a word-overlap judge) and an empty `rerank_url`.
3. Runs both paths (`chat` with the judge, `search`) as every principal in the fixture.
4. Fails (exit 1) on any `acl_violations` or `es_acl_violations`, or when a metric is under its floor in `tests/eval/floors.json`. Floors are keyed by path (`chat`, `search`), for example `p_recall@10`, `p_ndcg@10`, `d_recall@10`, and on chat `faithfulness` and `citation_support`.
5. Writes `eval-report.json` (status `pass`, `fail` or `error`), kept as a job artifact.

The fixture (`backend/tests/eval/fixture.yaml`) is synthetic: 32 documents, 4 principals (one admin), every visibility, one label with `exclude_llm` and one with `block_agents`, and 40 queries. `gen_fixture.py` next to it generated it; it may be edited by hand. Run it locally against the dev stack:

```sh
cd backend
uv run python -m roracle eval ci --fixture tests/eval/fixture.yaml --floors tests/eval/floors.json --out /tmp/eval-report.json
```

Use a scratch database (the command only touches a rolled-back transaction, but it expects a migrated schema).

## When to run an A/B

A change that alters ranking or context (reranker, contextual notes, RAPTOR, router, agentic planning) is gated by a report in `docs/eval/`. Index-time features are built into a shadow index (`roracle rebuild --what ... --into chunks-<slug>-shadow`) and compared with `--candidate-index`; see [index maintenance](../admin/index-maintenance.md). A default flips on only when the report says PASS.
