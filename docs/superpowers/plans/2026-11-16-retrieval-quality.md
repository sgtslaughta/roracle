# P13 Retrieval Quality Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

> **Roadmap:** spec §13 row 13 "Retrieval quality" — runs **before** row 12 (Documentation). Branch `feat/retrieval-quality` off `feat/sensitivity-labels`.

**Goal:** Measure retrieval and answer quality per principal, then add — each only if it measurably helps — a GPU cross-encoder reranker, LLM contextual chunk notes, per-document summary units (RAPTOR), a query router with query-time global map-reduce, and an agentic decomposition loop. No corpus-wide GraphRAG.

**Architecture:** New package `roracle/eval/` (`gold.py` schema + loaders, `metrics.py` pure scoring, `runner.py` per-principal runs through the production path, `judge.py` LLM faithfulness, `stats.py` paired bootstrap, `gate.py` gain verdict, `report.py` Markdown/JSON reports, `fakes.py` CI models, `cli.py`) + `models_eval.py`; admin REST `api/routes/admin_eval.py`; admin UI `features/admin/Eval*.tsx`. Retrieval changes stay in `retrieval.py` (rerank adapter, units, expansion) with new modules `ingest/context.py` (contextual notes), `ingest/raptor.py` (summary units), `ingest/rebuild.py` (backfill jobs), `chat/router.py`, `chat/global_answer.py`, `chat/agentic.py`. Reranker = Hugging Face TEI on `game-01` (ADR-0015 §3), script in `deploy/game-01/`.

**Tech Stack:** P10/P11b stack + labels. No new Python or npm dependencies (PyYAML is already a direct dependency; trend chart is inline SVG). New runtime image: `ghcr.io/huggingface/text-embeddings-inference:89-1.9`; new model weights `BAAI/bge-reranker-v2-m3`.

**Spec:** `docs/spec.md` §5, §5.1, §13; ADR-0002, 0005, 0006, 0007, 0014, **0015** (binding, already committed). User decisions 2026-11-16 (memory "P13 Retrieval quality") are binding.

## Global Constraints

- **Migration** `0013_eval.py`: `revision = "0013"`, `down_revision` = what `alembic heads` prints at execution (expected `"0012"`; if another branch took 0013, use the next number). Exactly one head after upgrade. No other migration in this plan (notes/units live in MinIO + ES).
- **Settings** (all `ROR_`-prefixed env, `config.py`; runtime-overridable ones also in `runtime.SettingsIn`):

  | Setting | Default | Runtime override |
  |---|---|---|
  | `eval_judge_model` | `""` (= first chat model) | – |
  | `eval_judge_sample` | `100` (query×principal pairs judged per run) | – |
  | `eval_on_reindex` | `true` (enqueue `eval_run` on reference set after re-embed cutover / rebuild) | – |
  | `rerank_api` | `"cohere"` (`Literal["cohere","tei"]`) | – |
  | `rerank_depth` | **`200`** (was 50) | `10–500` (was ≤100) |
  | `rerank_timeout_s` | `3.0` | – |
  | `rerank_max_chars` | `2000` (per passage sent) | – |
  | `chat_candidates` | `200` (BM25 and kNN list size on the chat path; was hard-coded 50) | – |
  | `context_notes` | `false` → flips per Task 5 gate | – |
  | `context_model` | `""` (= `summary_model`) | – |
  | `context_doc_chars` | `24000` | – |
  | `context_max_tokens` | `120` (hard cap; prompt asks 50–100) | – |
  | `raptor` | `false` → flips per Task 6 gate | – |
  | `raptor_model` | `""` (= `summary_model`) | – |
  | `raptor_section_chars` | `12000` | – |
  | `router` | `false` → flips per Task 7 gate | `bool` |
  | `router_model` | `""` (= `summary_model`) | – |
  | `global_max_docs` | `40` | – |
  | `global_concurrency` | `8` | – |
  | `global_timeout_s` | `90.0` (deadline for the whole map phase) | – |
  | `global_max_concurrent` | `16` (process-wide in-flight map calls; one global question per user; busy => fact path) | – |
  | `global_call_timeout_s` / `global_mcp_budget_s` | `20.0` / `45.0` | – |
  | `agentic_retrieval` | `false` → flips per Task 8 gate | `bool` |
  | `agentic_max_steps` | `3` | – |
  | `agentic_max_subqueries` | `4` | – |

- **ES fields** (added to `search_index._mapping` and to existing indices by an additive `put_mapping` in `ensure_index`, same pattern as `_ensure_label_mapping`): `context` `text` (english analyzer), `unit` `keyword` (`"section"` | `"document"`; **absent ≡ chunk**), `source_chunks` `keyword`. BM25 `multi_match` fields become `["text^1", "title^2", "headings", "context"]`. Unit `_id`/`chunk_id`: `"{version_id}:s{i}"` (section i), `"{version_id}:d"` (document). Units carry every `acl_fields` field (incl. `label_id`), `is_latest`, `version_id`, `doc_id`, `pages` (union of sources), `bboxes: []`.
- **Gold schema** (YAML or JSON, identical keys; `eval/gold.py` pydantic, `extra="forbid"`): fixture files carry `principals: {key: {groups: [str], admin: bool=false}}`, `labels` (optional), `documents: [{key, title, owner, visibility, org_unit?, shares?: [principal key], label?, chunks: [{text, headings?: [str], pages?: [int]}]}]`; both fixtures and Postgres sets carry `queries: [{key, text (1–2000), kind: fact|multihop|global, principals: [ref] (≥1), as_pat: bool=false, relevant: [{doc, quote?, page?, grade: 1|2|3}] (≥1), reference_answer?}]`. In Postgres, `doc`/principal refs are UUIDs; in fixtures, keys.
- **Metrics** (`eval/metrics.py`, pure functions, all in [0,1]):
  - `norm(s)` = casefold, `-\n` → `""`, whitespace collapsed. Hit `h` matches gold `g` iff `h.doc_id == g.doc` and (`g.quote`: `norm(g.quote) in norm(h.text)`; elif `g.page`: `g.page in h.pages`; else any passage of that doc). Each gold is credited once, at its first matching rank.
  - `R` = gold items whose doc is visible to the principal **on the evaluated path** (`visible_documents(p)`; chat path also `llm_allowed()`). Pairs with empty `R` are excluded from means and counted in `empty_pairs` (still ACL-checked).
  - `recall_at(k)` = |golds matched in top k| / |R|, k ∈ {5, 10, 50, 100}; `mrr` = 1/rank of first matching hit within 100, else 0; `ndcg10` = DCG@10 / IDCG@10 with gain `2^grade − 1`, discount `log2(rank + 1)`, IDCG from `R` grades sorted desc.
  - Passage level = ranked passage list (depth 100). Document level = unique doc ids in first-appearance order vs distinct gold docs (grade = max grade per doc). Keys: `p_recall@5 … p_recall@100, p_mrr, p_ndcg@10, d_recall@5 … d_ndcg@10`.
  - Means are macro over (query, principal) pairs; reported `overall`, `by_kind`, `by_principal`.
  - `es_acl_violations` = hits returned by ES whose doc fails the Postgres check (before R19 post-filter); `acl_violations` = hits surviving the pipeline that fail it. `acl_violations > 0` ⇒ run `status="failed"`; CI fails on either.
  - `faithfulness` = supported answer claims / claims (a claim = answer sentence with a substantive assertion; uncited claim = unsupported); `citation_support` = supported (claim, citation) pairs / pairs. `latency_p50_ms`, `latency_p95_ms` of the retrieval call per pair.
- **Gain gate** (`eval/gate.py`, constants exactly): `MIN_QUERIES = 200`, `MIN_PRINCIPALS = 3` (≥ 1 non-admin), `NDCG_GAIN = 0.02`, `RECALL10_GAIN = 0.02`, `RECALL100_MAX_LOSS = 0.005`, `JUDGE_MAX_LOSS = 0.02`, `BOOTSTRAP_N = 1000`, `BOOTSTRAP_SEED = 0`. PASS iff candidate vs baseline on the **reference set** (same corpus, same commit except the flag): (Δ`p_ndcg@10` ≥ 0.02 **or** Δ`p_recall@10` ≥ 0.02) with paired-bootstrap 95% CI lower bound > 0 for that metric, Δ`p_recall@100` ≥ −0.005, Δ`faithfulness` and Δ`citation_support` ≥ −0.02, `acl_violations == 0`, and the task's latency budget. Set too small ⇒ verdict `INSUFFICIENT` (treated as FAIL). Tasks 7/8 use the `scope` given in their task.
- **Default flip rule:** a feature task ends by running `roracle eval compare` on the dev reference set, committing the report to `docs/eval/2026-11-<dd>-<feature>.md`, and changing the `config.py` default to on **only** if the verdict is PASS. Otherwise the default stays off and the report says why. The task reviewer checks the report's verdict against the diff.
- **Labels gates (ADR-0014) respected:** no `exclude_llm` document text reaches any LLM (context notes, RAPTOR summaries, router, global map/reduce, agentic loop, eval judge, eval question generation) — label is re-read (`session.refresh(d, ["label_id"])`) immediately before each generation, as ingest summary does. Units carry `label_id`, so PAT `block_agents` and chat `exclude_llm` ES filters apply unchanged; Postgres stays the authority.
- **R19:** every new consumer of `search_chunks` or of ES units (unit expansion, global candidates, agentic sub-queries, eval runner) post-filters against `access.visible_documents(p)` (+ `llm_allowed()` on LLM paths) and only `searchable` latest versions for non-owners. Not visible ⇒ 404 `{"detail":"not found"}`; eval results never reveal invisible docs to non-admins (eval API is admin-only).
- **Never cross-document:** no stored artefact (note, summary, unit, cache) may combine content of two documents. Caches are keyed per version (`derived/{version_id}/...`).
- **Air-gap:** models only via configured endpoints; TEI runs with `HF_HUB_OFFLINE=1` from a mounted directory; nothing downloads at runtime.
- **Tests:** unit tests never need the GPU, ES or a live LLM (httpx `MockTransport`, `tests/ingest_helpers.FakeLLM`, `eval/fakes.py`). Integration tests use `flock /tmp/roracle-integ.lock uv run pytest -m integration …`. Live-GPU tests are marked `integration` and skip unless `ROR_RERANK_URL` answers `/health`.
- Files < 500 lines (`chat/retrieve.py` is 256, `retrieval.py` 230, `search_index.py` 374 — split before crossing); ruff check + format clean; Conventional Commits ending with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

1. Eval is ACL-correct: per-principal expected sets, independent Postgres visibility check, admin-only sets refused, PAT variant exercised — Tasks 1, 2 (opus review).
2. No `exclude_llm` text reaches an LLM through any new path; PAT never sees `block_agents` units — Tasks 5, 6, 7, 8 (opus review).
3. Units never outlive or out-scope their document: deleted on version delete/purge, `acl_sync` reaches them, `delete_stale_chunks` does not delete them, re-embed copies them — Task 6.
4. Citations from units/global answers resolve to real passages (doc, version, page, chunk) — Tasks 6, 7.
5. Defaults flipped only with a PASS report in `docs/eval/` — Tasks 4–8.

## Rulings (plan-level)

- R1 Reranker server = HF TEI `89-1.9` + in-app adapter `ROR_RERANK_API=tei` (ADR-0015 §3). TEI request `{query, texts, truncate: true, raw_scores: false}`, response `[{index, score}]`; Cohere request/response unchanged. Fallback server documented: llama.cpp `/v1/rerank` (`cohere` mode, no adapter) — cost: ~20 lines of adapter code.
- R2 Reranker "on by default" is a deployment default (dev `.env`, Helm values `rerank.url`), not a `config.py` default — the URL is site-specific. `rerank_depth=200` and `chat_candidates=200` are code defaults regardless (cheap without a reranker: RRF over deeper lists) — cost: chat ES queries fetch 200 instead of 50 per list.
- R3 Relevance unit = quote-anchored passage (survives re-chunking/re-embedding); chunk ids are never stored in gold sets — cost: gold items need a short verbatim quote or a page.
- R4 Eval evaluates the chat path by default (`rank_for_chat`, depth 100, scope all, empty viewer); `--path search` evaluates library ranking. Expected sets differ by path (chat drops `exclude_llm` docs) — cost: two numbers per set when both paths matter.
- R5 Admin-curated sets may name any existing users as principals; a run refuses (`ValueError("evaluate as at least one non-admin principal")`, CLI exit 2, API 422) a set whose active queries reference only admins. `as_pat: true` additionally evaluates `replace(p, via="pat", scopes={"read"})` — cost: none.
- R6 Reference set = the single `eval_sets.is_reference` row (partial unique index). The dev reference set is curated in the dev stack from the dev corpus via `eval_generate` drafts + admin review; until it has ≥ 200 active queries every gate verdict is `INSUFFICIENT` and all flags stay off — cost: features may wait on curation (**needs user attention: which corpus/queries form the dev reference set**).
- R7 Index-time A/B (Tasks 5, 6): the candidate is built into a **shadow index** (`roracle rebuild --into chunks-<slug>-shadow`) that joins `chunks-all` (so `acl_sync` and deletes reach it) but never the read alias; `eval compare --candidate-index` reads it in-process via `settings.embed_index`. Adoption = rebuild into the active index; shadow dropped by `roracle rebuild --drop <name>` — cost: temporary double storage.
- R8 Embedding prompt version: `ingest/embed.EMBED_PROMPT_VERSION = "2"` is hashed into `prompt_sha`; with a note the prompt is `doc_prompt(title, f"{note}\n\n{chunk.contextual}")` (summary prefix dropped — the note supersedes it); without a note it is unchanged. Bumping the version makes the next ingest/rebuild re-embed every chunk once — cost: one full re-embed, which the rebuild job does anyway.
- R9 Context notes: one LLM call per chunk; messages = `[system, user: "<document>…</document>"` (stable prefix) `, user: "<chunk>…</chunk> Situate…"]`, chunks of one document processed sequentially so Ollama/vLLM prefix caching applies (no provider-specific cache API exists on our OpenAI-spec endpoint). Document text truncated to `context_doc_chars` around the chunk (window centred on the chunk's offset). Notes cached in `derived/{version_id}/context-v{CONTEXT_PROMPT_VERSION}.json` (`{sha256(chunk.text): note}`); `exclude_llm` at generation time ⇒ note = `" > ".join(headings)` (may be `""`), no LLM call; cached notes from before a later label raise are reused (same as summaries, ADR-0014 R8). Ollama must serve the context model with `num_ctx ≥ 16384` (Modelfile `PARAMETER num_ctx 16384`, model name e.g. `roracle-context`) — cost: operator step, documented in Task 9.
- R10 RAPTOR scope: sections = consecutive chunks sharing `headings[0]` (no heading ⇒ windows of 8 chunks); a section longer than `raptor_section_chars` is summarised in windows, then summary-of-summaries. Document unit = summary of section summaries (150–250 words; distinct from the 1–3-sentence index summary). Exclude-LLM docs get **no** units. Summaries cached in `derived/{version_id}/raptor-v{RAPTOR_PROMPT_VERSION}.json` — cost: docs without headings get coarser sections.
- R11 Unit hits never reach the LLM as text on the fact/multi-hop path: `expand_units` replaces each with up to 2 of its own source chunks (ACL-filtered ES `ids` fetch, reranked if a reranker is configured), so the LLM reads and cites real passages. Library search keeps `units=("chunk",)` until Task 6's gate passes for `--path search` too — cost: summaries help recall, not wording.
- R12 Global path: candidates = top `global_max_docs` distinct docs from `search_chunks(units=("document","section","chunk"), exclude_llm=True)`, then Postgres `visible_documents(p) & llm_allowed()` + latest searchable version; map (≤ `global_concurrency` parallel, `global_timeout_s` total, timeouts drop that doc) reads only that doc's own units; reduce cites `[n]` per document; citation n resolves to the best source chunk of the section the map step named. If fewer than 3 candidates survive, fall back to the fact path — cost: very broad questions see at most 40 documents (stated in the answer meta `global: {docs_considered}`).
- R13 Router output ∈ {`fact`, `multihop`, `global`}; LLM JSON at temperature 0, invalid/timeout ⇒ `fact`. `multihop` uses the agentic loop only when `agentic_retrieval` is on, else `fact`. Router decision returned in SSE `meta.route` and MCP `ask` result `route` — cost: one small-model call per turn when enabled.
- R14 GraphRAG / cross-document RAPTOR rejected (ADR-0015). Any future proposal must solve per-caller post-filtering first.

---

### Task 1: Eval core — gold sets, metrics, runner, storage, CLI `eval run` (opus review)

**Files:** `migrations/versions/0013_eval.py`; `models_eval.py` (`EvalSet`, `EvalQuery`, `EvalRun`, `EvalResult`; imported by `models.py`); `eval/__init__.py`, `eval/gold.py`, `eval/metrics.py`, `eval/runner.py`, `eval/store.py`, `eval/cli.py`; `__main__.py` (`eval` subcommand); `chat/retrieve.py` (extract `rank_for_chat`); Test `tests/test_eval_metrics.py`, `tests/test_eval_gold.py`, `tests/test_eval_runner.py`, `tests/test_eval_schema.py`.
**Schema:** `eval_sets(id uuid pk, name text unique 1–80, description text ≤ 2000, is_reference bool default false — partial unique index WHERE is_reference, created_by uuid fk users null, created_at, updated_at)`; `eval_queries(id uuid pk, set_id fk CASCADE, key text, text, kind CHECK fact|multihop|global, principals uuid[] NOT NULL, as_pat bool default false, relevant jsonb NOT NULL, reference_answer text null, status CHECK draft|active default active, created_at; unique(set_id, key))`; `eval_runs(id uuid pk, set_id fk SET NULL, set_name text, trigger CHECK cli|ci|admin|model_change|reindex|compare, label text, path CHECK chat|search, status CHECK running|done|failed, config jsonb, metrics jsonb, error text, started_at, finished_at)`; `eval_results(run_id fk CASCADE, query_key text, principal text, kind text, metrics jsonb, ranked jsonb /* top-100 [[chunk_id, doc_id]], ids only */, judge jsonb null, latency_ms int, pk(run_id, query_key, principal))`.
**Interfaces:**
- `gold.GoldSet` / `GoldQuery` / `GoldItem` (pydantic); `gold.load_file(path) -> FixtureSet` (YAML/JSON by suffix); `gold.load_db(session, set_id) -> GoldSet` (active queries only); `gold.resolve_principals(session, gs) -> dict[str, Principal]` (via `auth.principal_for_user`; adds `":pat"` variants for `as_pat`); raises `ValueError` per R5.
- `metrics.Hit(doc_id: str, text: str, pages: list[int])`; `metrics.match_ranks(hits: list[Hit], gold: list[GoldItem]) -> list[tuple[int, int]]` (rank, grade); `metrics.score_pair(hits, gold) -> dict[str, float]` (all passage + doc keys); `metrics.PairResult(query_key, principal, kind, scores: dict[str, float] | None, acl_violations: int, es_acl_violations: int, latency_ms: int)` (`scores=None` for empty-`R` pairs); `metrics.aggregate(rows: list[PairResult]) -> dict` (`overall`, `by_kind`, `by_principal`, `empty_pairs`, `acl_violations`, `es_acl_violations`, latency p50/p95).
- `chat.retrieve.rank_for_chat(session, es, llm, settings, p, question, *, viewer: Viewer | None = None, scope: str = "all", excludes: list[str] = (), depth: int = 100, excluded: list[dict] | None = None) -> list[Passage]` — post-filtered, boosted, ranked list before truncation/pins/selection/notes; `retrieve_for_chat` calls it (behaviour unchanged).
- `runner.run_set(session, es, llm, settings, gs: GoldSet, *, path: Literal["chat","search"] = "chat", trigger: str, label: str = "", judge: bool = False, overrides: dict | None = None, sf=None) -> UUID` (creates `eval_runs` row, one `eval_results` row per pair, final `metrics`; `overrides` applied with `runtime.apply`); `runner.visible_ids(session, p, doc_ids, path) -> set[str]` (independent SQL check).
- CLI: `roracle eval run (--set NAME | --fixture PATH) [--path chat|search] [--label TEXT] [--set-override KEY=VAL]... [--index NAME] [--json OUT]` → prints overall table; exit 1 if `acl_violations > 0`, exit 2 on R5.
- [ ] Tests (metrics): quote match is case/whitespace/hyphen-insensitive; gold credited once; recall@k/MRR/nDCG on a hand-computed 3-gold example (exact floats); doc-level dedupe; empty `R` pair excluded and counted.
- [ ] Tests (gold): YAML and JSON parse identically; unknown key → error; `grade` outside 1–3 → error; admin-only principals → `ValueError` (R5); `as_pat` yields a `via="pat"` principal.
- [ ] Tests (runner, fake `rank` callable injected): expected set shrinks to docs visible to that principal; a hit on an invisible doc counts `acl_violations` and run `status="failed"`; chat path drops `exclude_llm` gold; PAT variant drops `block_agents` gold; results store ids only (no text in `ranked`); `rank_for_chat` + truncation equals previous `retrieve_for_chat` output (regression on existing chat tests).
- [ ] Tests (schema): migration up/down/up; one head; second `is_reference` row → IntegrityError.
- [ ] **Commit** `feat(eval): gold sets, per-principal retrieval metrics and eval run CLI`

### Task 2: Judge, statistics, gate, compare reports, CI fixture job, auto-runs (opus review)

**Files:** `eval/judge.py`, `eval/stats.py`, `eval/gate.py`, `eval/report.py`, `eval/fakes.py`, `eval/cli.py` (`compare`, `ci`), `eval/jobs.py` (`handle_eval_run`), `worker.py` (`eval_run` handler), `reembed.py` (`_cutover` enqueues `eval_run`), `.gitlab-ci.yml` (`eval` job), `backend/tests/eval/fixture.yaml`, `backend/tests/eval/floors.json`; Test `tests/test_eval_judge.py`, `tests/test_eval_gate.py`, `tests/test_eval_cli.py`, `tests/test_eval_fixture_es.py` (integration).
**Interfaces:**
- `judge.answer(session, llm, settings, p, question, passages) -> str` (same `build_messages` + default chat model as chat; non-streaming); `judge.claims(answer: str) -> list[Claim(text, cites: list[int])]`; `judge.judge(llm, model, claims, passages) -> dict` (`faithfulness`, `citation_support`, per-claim verdicts; one JSON call per (claim, cite) `{"supported": bool}`, invalid JSON = unsupported). Passages are exactly those the chat path produced (already label-gated); assert no `exclude_llm` doc id is passed.
- `stats.paired_bootstrap(a: list[float], b: list[float], n=BOOTSTRAP_N, seed=BOOTSTRAP_SEED) -> tuple[float, float, float]` (delta, lo, hi).
- `gate.Verdict(status: Literal["PASS","FAIL","INSUFFICIENT"], reasons: list[str], deltas: dict)`; `gate.verdict(base_run, cand_run, *, scope: Literal["all","fact","multihop","global"] = "all", latency_budget_ms: int | None = None) -> Verdict` (constants in Global Constraints).
- `report.markdown(base, cand, verdict) -> str` (metric table A/B/Δ/CI, by kind, config diff, verdict); `report.json(run) -> dict`.
- CLI: `roracle eval compare --set NAME [--path …] --baseline "k=v,…" --candidate "k=v,…" [--candidate-index NAME] [--scope …] [--latency-budget-ms N] [--judge] --out docs/eval/<file>.md` (two runs, `trigger="compare"`); `roracle eval ci --fixture PATH --floors PATH --out PATH` (fresh index `chunks-evalci-1`, seeds users/docs/labels from the fixture, `fakes.HashEmbedder` 768-d hashed bag-of-words, `fakes.OverlapJudge`; exit 1 on any violation or metric below floor).
- Job `eval_run` payload `{set_id, trigger, judge: bool}`; `_cutover` enqueues it for the reference set when `eval_on_reindex` and one exists.
- CI job `eval` (stage test): services `postgres:16` + `docker.elastic.co/elasticsearch/elasticsearch:8.15.3` (`discovery.type=single-node`, `xpack.security.enabled=false`); script `python -m roracle migrate && python -m roracle eval ci --fixture tests/eval/fixture.yaml --floors tests/eval/floors.json --out eval-report.json`; artifact `eval-report.json`.
- Fixture: ≥ 30 docs, 4 principals (alice `/eng`, bob `/sales`, carol no groups, root admin), every visibility, one `exclude_llm` and one `block_agents` label, 40 queries (24 fact / 8 multihop / 8 global), each with ≥ 2 principals of which ≥ 1 cannot see some gold doc, ≥ 5 `as_pat`.
- [ ] Tests: claim splitter keeps `[n]` markers; judge with FakeLLM returns exact ratios; bootstrap deterministic for seed 0; gate PASS/FAIL/INSUFFICIENT on synthetic runs incl. Recall@100 loss and ACL violation; markdown contains verdict line; `eval ci` exits 1 when a floor is raised above the measured value (unit, fake runner); cutover enqueues `eval_run` only with a reference set.
- [ ] Integration (`test_eval_fixture_es`): full `eval ci` on the fixture against ES → 0 violations, metrics ≥ floors; PAT pairs never return `block_agents` docs.
- [ ] **Commit** `feat(eval): LLM judge, gain gate, compare reports and CI eval job`

### Task 3: Admin eval API, question generation, Eval page

**Files:** `api/routes/admin_eval.py`, `api/app.py`, `eval/generate.py` (`eval_generate` job), `worker.py`; frontend `features/admin/EvalPage.tsx`, `EvalSetPage.tsx`, `EvalTrend.tsx` (inline SVG, no deps), `evalApi.ts`, `AdminLayout.tsx` (nav "Eval"), router entry, MSW mocks; Test `tests/test_admin_eval.py`, `frontend/src/features/admin/eval.test.tsx`.
**Interfaces (all `require_admin`, PAT → 403, audit `eval.*`):** `GET/POST /admin/eval/sets`, `PATCH/DELETE /admin/eval/sets/{id}` (`is_reference` true unsets the previous one in the same transaction); `GET/POST /admin/eval/sets/{id}/queries`, `PATCH/DELETE /admin/eval/queries/{id}` (principals must exist, docs must exist and not be deleted → 422); `POST /admin/eval/sets/{id}/import` (gold `queries` YAML/JSON body, ≤ 2 MiB) → `{created}`; `POST /admin/eval/sets/{id}/generate {n: 1–200, principal: uuid}` → 202 (job samples chunks of docs visible to `principal` **and** `llm_allowed`, LLM writes one self-contained question + quote per chunk, rows `status="draft"`); `POST /admin/eval/runs {set_id, path, judge}` → 202 `{job_id}`; `GET /admin/eval/runs?set_id=&limit≤200` → `{items:[{id, started_at, trigger, label, path, status, metrics.overall}]}`; `GET /admin/eval/runs/{id}` → run + `by_kind` + `by_principal` + worst 20 pairs (query text, principal username, `p_recall@10`, top-5 doc titles).
- [ ] Tests: CRUD + 422s; non-admin 403, PAT 403; second reference unsets first; generate skips `exclude_llm` docs (FakeLLM prompt capture) and invisible docs; drafts excluded from runs until activated; run enqueue + listing ordered by `started_at`; audit rows.
- [ ] Frontend tests: trend renders one series per selected metric with run points; metric selector; draft → active approve; R5 error surfaced; long query text truncates with tooltip; +K overflow on principal chips.
- [ ] **Commit** `feat(eval): admin eval sets, question generation and trend page`

### Task 4: Reranker on game-01 (TEI) + adapter, budget, fallback

**Files:** `deploy/game-01/reranker.sh` (new), `deploy/game-01/README.md` (new, 1 page), `retrieval.py` (`_rerank` adapter/timeout/truncation), `chat/retrieve.py` (`chat_candidates`), `config.py`, `runtime.py` (`rerank_depth` 10–500), `metrics.py` (`RERANK_SECONDS` histogram, `RERANK_FALLBACK{reason}` counter), `deploy/dev/.env.example`, Helm `values.yaml` (`rerank.url/api/model`), `docs/enterprise/airgap-mirror-manifest.md` (TEI image row with digest-or-"pin in mirror", model row with HF commit + sha256 list); Test `tests/test_retrieval.py` (extend), `tests/test_rerank_live.py` (integration, skips without endpoint).
**Script** (`reranker.sh`, idempotent, `set -euo pipefail`): `pull` — on a connected host `hf download BAAI/bge-reranker-v2-m3 --revision <pinned sha> --local-dir bge-reranker-v2-m3` + `sha256sum` manifest, `docker pull` + `docker save` TEI image; `install` — copy to `game-01:/srv/roracle/models/bge-reranker-v2-m3`, `docker load`; `run` —
`docker run -d --name roracle-reranker --restart unless-stopped --gpus all -p 127.0.0.1:28080:80 -v /srv/roracle/models:/data:ro -e HF_HUB_OFFLINE=1 ghcr.io/huggingface/text-embeddings-inference:89-1.9 --model-id /data/bge-reranker-v2-m3 --dtype float16 --max-client-batch-size 256 --max-batch-tokens 65536 --auto-truncate`;
`health` — `curl -fsS localhost:28080/health` then a smoke `/rerank` asserting the relevant text ranks first. Dev access: SSH tunnel `localhost:21480 → game-01:127.0.0.1:28080` (same pattern as Ollama 21434); `ROR_RERANK_URL=http://localhost:21480/rerank ROR_RERANK_API=tei ROR_RERANK_MODEL=bge-reranker-v2-m3`.
**Interfaces:** `_rerank(settings, query, passages, http) -> list[Passage]` keeps its contract; `_rerank_request(settings, query, texts) -> dict` and `_rerank_order(settings, body) -> list[int]` (pure, per `rerank_api`); passages truncated to `rerank_max_chars`; `httpx` timeout `rerank_timeout_s`; any error/timeout ⇒ fused order + `RERANK_FALLBACK.labels(reason)` (`timeout|http|malformed`).
**Budget:** p95 rerank call ≤ 800 ms at depth 200 on the 4090 (measured by `test_rerank_live` over 50 runs, recorded in the eval report); compare with `--latency-budget-ms` = baseline p95 + 800.
- [ ] Tests (unit, MockTransport): TEI request body has `texts`, not `documents`; TEI list response reorders; Cohere path unchanged; timeout → fused order + counter; texts truncated; depth 200 sends 200; runtime override 501 → 422; chat search uses `chat_candidates` list size.
- [ ] Live (integration, skip without endpoint): `/health` ok; relevant passage first; p95 within budget.
- [ ] Eval: `roracle eval compare --baseline "rerank_url=" --candidate "rerank_url=$URL,rerank_api=tei" --latency-budget-ms …` → `docs/eval/2026-11-xx-rerank.md`; on PASS set R2 deployment defaults, else document flag only.
- [ ] **Commit** `feat(retrieval): TEI cross-encoder reranker at depth 200 with timeout fallback`

### Task 5: Contextual chunk notes + rebuild job (opus review)

**Files:** `ingest/context.py` (new), `ingest/embed.py` (`EMBED_PROMPT_VERSION`, `chunk_prompt`), `ingest/pipeline.py` (`_index` uses notes when `context_notes`), `reembed.py` (`_copy` uses `chunk_prompt` incl. `context`), `search_index.py` (`context` field + `index_chunks(..., contexts=None)` + mapping put), `retrieval.py` (BM25 fields), `ingest/rebuild.py` (new: `rebuild_units`, `rebuild_watch` jobs, shadow index R7), `api/routes/admin_index.py` (`POST /admin/retrieval/rebuild`), `__main__.py` (`roracle rebuild`), `worker.py`; Test `tests/test_context_notes.py`, `tests/test_rebuild.py`, `tests/test_rebuild_es.py` (integration).
**Interfaces:**
- `context.CONTEXT_PROMPT_VERSION = "1"`; `context.notes_for(session, llm, settings, store, v: DocumentVersion, d: Document, doc_text: str, chunks: list[Chunk]) -> list[str]` (cache read/write per R9; label re-read; returns one note per chunk; `max_tokens=context_max_tokens` on the call, output truncated to 600 chars).
- `embed.chunk_prompt(title: str, contextual: str, summary: str | None, context: str | None) -> str` (R8); `embed.prompt_sha(prompt)` hashes `f"v{EMBED_PROMPT_VERSION}\n{prompt}"`.
- `rebuild.enqueue_all(session, what: set[Literal["context","raptor"]], into: str | None) -> int` (one `rebuild_units` job per latest searchable non-deleted version via `INSERT … SELECT`, plus one `rebuild_watch`); `rebuild.handle_rebuild_units(session, payload, *, settings, store, es, llm, session_factory)` (re-runs `pipeline._index` for that version into `into` or the active index; skips deleted/non-searchable); `rebuild.handle_rebuild_watch(...)` (re-enqueues itself every 60 s while `rebuild_units` jobs are pending, then enqueues `eval_run` trigger `reindex` if `eval_on_reindex`); `rebuild.create_shadow(es, settings, name)`, `rebuild.drop_shadow(es, name)` (refuses the active index).
- `POST /admin/retrieval/rebuild {what: ["context"|"raptor"], into?: str}` → 202 `{jobs}` (`require_admin`, audit `retrieval.rebuild`). CLI `roracle rebuild --what context[,raptor] [--into NAME] | --drop NAME`.
- [ ] Tests: FakeLLM sees `<document>` prefix identical across a doc's chunks (prefix-cache order) and chunks sent sequentially; `exclude_llm` doc → zero LLM calls, notes = heading paths; cache hit on second run → zero calls; `prompt_sha` differs from v1 for the same prompt; `chunk_prompt` drops summary when a note exists; reembed `_copy` reproduces pipeline prompts (same shas) for chunks with `context`; BM25 query includes `context`; rebuild skips deleted/non-searchable; watch enqueues `eval_run` once.
- [ ] Integration: rebuild into shadow → shadow has `context`, read alias untouched, `acl_sync` of a visibility change reaches the shadow; drop shadow refuses active index.
- [ ] Eval: `eval compare --baseline "" --candidate-index chunks-<slug>-shadow` (shadow built with `--what context`) → `docs/eval/2026-11-xx-context-notes.md`; PASS ⇒ `context_notes: bool = True` in `config.py` and rebuild into active index documented as an upgrade step.
- [ ] **Commit** `feat(ingest): LLM contextual chunk notes with cached rebuild job`

### Task 6: Per-document RAPTOR summary units (opus review)

**Files:** `ingest/raptor.py` (new), `ingest/pipeline.py` (`_index` builds units when `raptor`), `ingest/rebuild.py` (`raptor`), `search_index.py` (`unit`, `source_chunks` fields; `index_units`; `delete_stale_chunks` keeps units; `delete_stale_units`), `reembed.py` (`_copy` embeds units with `doc_prompt(title, text)`), `retrieval.py` (`units` kwarg, `Passage.unit/source_chunks`, `expand_units`), `chat/retrieve.py` (units on chat path when `raptor`, expansion before R19 `_live` filter); Test `tests/test_raptor.py`, `tests/test_units_retrieval.py`, `tests/test_raptor_es.py` (integration).
**Interfaces:**
- `raptor.RAPTOR_PROMPT_VERSION = "1"`; `raptor.Unit(kind: Literal["section","document"], key: str, text: str, headings: list[str], source_chunks: list[str], pages: list[int])`; `raptor.sections(chunks: list[Chunk]) -> list[list[Chunk]]` (R10, pure); `raptor.build_units(session, llm, settings, store, v, d, chunks) -> list[Unit]` (`[]` for `exclude_llm`; cached).
- `search_index.index_units(es, v, d, units, vectors, acl, model, *, title, is_latest, tags, index) -> int`; `search_index.delete_stale_units(es, version_id, keep_ids: list[str], alias=MUTATE_ALIAS)`; `delete_stale_chunks` adds `must_not exists unit`.
- `retrieval.search_chunks(..., units: tuple[str, ...] = ("chunk",))` (filter: chunk ⇒ `must_not exists unit` OR `terms unit`); `retrieval.expand_units(es, settings, p, policy, query, passages, *, exclude_llm: bool, per_unit: int = 2, http=None) -> list[Passage]` (R11).
- [ ] Tests: `sections` groups by `headings[0]`, windows of 8 without headings; `exclude_llm` → no units, no LLM call; unit ES docs carry ACL fields + `label_id` and `source_chunks`; `delete_stale_chunks` leaves units, `delete_stale_units` removes old ones; visibility change via `acl_sync` updates units (integration); doc delete removes units (integration); PAT search never returns a `block_agents` unit; chat passages contain no `unit != chunk` items and every citation has a real `chunk_id` + pages; expanded chunks re-pass ACL filter (unit of visible doc whose source chunk id points elsewhere is dropped); library search unchanged by default.
- [ ] Eval: shadow built with `--what context,raptor` (or `raptor` alone if Task 5 FAILED) vs Task 5's adopted baseline → `docs/eval/2026-11-xx-raptor.md`; budget = baseline p95 + 300 ms; PASS ⇒ `raptor: bool = True`.
- [ ] **Commit** `feat(retrieval): per-document summary units with source-passage citations`

### Task 7: Router + query-time global map-reduce (opus review)

**Files:** `chat/router.py` (new), `chat/global_answer.py` (new), `api/routes/chats.py` (route + SSE `meta.route`/`meta.global`), `mcp/tools_write.py` (`_ask` route + `route` field), `runtime.py` (`router` override), `eval/runner.py` (global candidates as ranked list for `kind: global` when router on); Test `tests/test_router.py`, `tests/test_global_answer.py`.
**Interfaces:**
- `router.classify(llm, settings, question: str) -> Literal["fact","multihop","global"]` (R13; prompt carries only the question).
- `global_answer.candidates(session, es, llm, settings, p, question) -> list[DocUnits]` (`DocUnits(doc_id, version_id, title, units: list[Passage])`; R12 incl. Postgres `visible_documents(p) & llm_allowed()` + searchable latest; ≤ `global_max_docs`); `global_answer.map_docs(llm, settings, question, docs) -> list[MapNote]` (`MapNote(doc_id, text, section_key | None)`; bounded concurrency/timeout; `"NONE"` dropped); `global_answer.build(session, es, settings, p, question, notes) -> tuple[list[dict], list[Passage]]` (reduce messages + one real source Passage per cited doc, aligned with `[n]`).
- Chat: when `router` on and route is `global`, `chats.py` streams the reduce via existing `stream_answer(..., messages, passages, …)`; `excluded_by_label` semantics unchanged.
- [ ] Tests: invalid router JSON → `fact`; global candidates exclude invisible, `exclude_llm` and (PAT) `block_agents` docs even when ES returns them (fake ES); map receives only one doc's own units per call; fan-out ≤ `global_max_docs`, concurrency ≤ `global_concurrency`; timed-out doc dropped; < 3 candidates → fact path; citation n resolves to a chunk of the cited doc with pages; SSE meta has `route` and `docs_considered`; MCP `ask` returns `route`.
- [ ] Eval: `eval compare --baseline "router=false" --candidate "router=true" --scope global --judge` → `docs/eval/2026-11-xx-router.md`; PASS requires gate on `global` scope (doc-level: Δ`d_recall@10` ≥ 0.05 replaces the passage criterion for this scope) **and** `fact` scope Δ`p_ndcg@10` ≥ −0.005; budget: router call p95 ≤ 1500 ms, global answer ≤ `global_timeout_s`. PASS ⇒ `router: bool = True`.
- [ ] **Commit** `feat(chat): query router with per-document global map-reduce`

### Task 8: Agentic decomposition loop (chat + MCP ask)

**Files:** `chat/agentic.py` (new), `chat/retrieve.py` (hook when route `multihop` or router off and `agentic_retrieval` on), `api/routes/chats.py` (`meta.subqueries`), `mcp/tools_write.py`, `runtime.py` (`agentic_retrieval` override); Test `tests/test_agentic.py`.
**Interfaces:** `agentic.plan(llm, settings, question: str, seen: list[Passage]) -> Plan(subqueries: list[str], done: bool)` (≤ `agentic_max_subqueries`, JSON, invalid ⇒ `done=True`); `agentic.retrieve(session, es, llm, settings, p, question, *, viewer, scope, excludes) -> tuple[list[Passage], list[str]]` (≤ `agentic_max_steps` rounds; each sub-query through `rank_for_chat` — so ES ACL + `exclude_llm` + R19 hold; fused with `rrf`; dedupe by `chunk_id`; returns passages + sub-queries for meta). Planner prompts contain only the question and texts of already-gated passages.
- [ ] Tests: step and sub-query caps; invalid plan JSON stops the loop; every sub-query result passes `rank_for_chat` (spy) with the caller's principal; `exclude_llm` passage text never appears in planner prompts; PAT principal never gets `block_agents` passages; flag off ⇒ zero planner calls.
- [ ] Eval: `eval compare --baseline "agentic_retrieval=false" --candidate "agentic_retrieval=true" --scope multihop --judge` → `docs/eval/2026-11-xx-agentic.md`; PASS needs multihop gate + `all` scope Δ`p_ndcg@10` ≥ −0.005; budget: **absolute (ledger ruling 2026-11-17): added latency p95 ≤ 1.0 s per planning round, ≤ 3 rounds** (replaces "retrieval p95 ≤ 3 × baseline"). PASS ⇒ `agentic_retrieval: bool = True`.
- [ ] **Commit** `feat(chat): eval-gated agentic query decomposition`

### Task 9: Enterprise docs

**Files:** `docs/enterprise/deployment-guide.md` (reranker service on GPU host, Ollama `num_ctx` model variant R9, new env vars table), `docs/enterprise/operations-runbook.md` (eval CLI, reference set curation, rebuild + shadow workflow, reading eval trend, rerank fallback metric/alert), `docs/enterprise/airgap-mirror-manifest.md` (verify Task 4 rows), `docs/enterprise/security.md` (eval data admin-only, per-principal eval, no cross-document artefacts, LLM gate coverage of notes/units/router/global/agentic/judge/generation), `docs/enterprise/agent-integration.md` (MCP `ask` `route`/`subqueries`), `docs/adr/0015-retrieval-quality.md` (append "Outcomes" with each verdict + report link), `docs/eval/README.md` (how reports are produced).
- [ ] Every statement verified against code by the task reviewer; no spec edits (spec §5.1 and §13 were committed with this plan).
- [ ] **Commit** `docs(retrieval): operator, security and eval docs for retrieval quality`

---

## Self-review

- **Spec coverage:** spec §5.1 bullets → eval (T1–T3), rerank 200 + timeout (T4), contextual notes + `exclude_llm` fallback + prompt hash + rebuild (T5), per-doc units + ACL/label + citations (T6), router + global over visible/LLM-allowed docs + bounded fan-out (T7), agentic loop behind setting (T8), GraphRAG rejection (ADR-0015, R14). Per-principal eval, CI, re-index/model-change runs, admin trend: T1, T2, T3, T5 (`rebuild_watch`).
- **Type consistency:** `Passage` gains `unit: str = "chunk"`, `source_chunks: list[str] = []` (T6) used by T7; `rank_for_chat` (T1) consumed by T2 judge, T6, T8; `gate.verdict` scopes match T7/T8; settings names identical in table and tasks.
- **Proportion:** tasks list files, signatures, tests and commits only; no code transcripts beyond the deployment command.

---

## Task 10: Performance at scale (added 2026-11-16 per user requirement; source: perf audit)

Budgets (binding): /search p95 ≤400 ms (≤500 filtered), ≤25 ms PG; browse p95 ≤100 ms @10M docs; chat TTFT ex-LLM ≤800 ms (≤1.1 s RAPTOR); claim p99 ≤5 ms @100k queued; ingest ≥4 docs/min/worker, ≤2 s overhead/doc, near-dup ≤50 ms; rebuild ≥1,000 chunks/s with 4 embed replicas; reconcile with no drift = 0 ES writes.

User decision: Elasticsearch 9.5.6 (fresh cluster + rebuild, no 8.19 hop) — Task 10.10 adopts bbq_hnsw (explicit index_options, oversample≈3 rescore; eval-gated vs int8, int4 fallback), ACORN filtered kNN (filters inside knn), vectors excluded from _source (fix _source vector reads), refresh_interval -1 during bulk; skip native rrf/linear retrievers + bbq_disk (non-Basic). See .superpowers/sdd/rq/es9-research.md.

#### Task 10.1: Synthetic data generators and the load-test harness

**Files:** `backend/src/roracle/perf/__init__.py`, `perf/synth_pg.py`, `perf/synth_es.py`, `perf/load.py`,
`perf/report.py`, `perf/cli.py` (registered in `__main__.py` as `roracle perf ...`); tests
`tests/test_perf_report.py`, `tests/test_perf_synth.py` (integration).
**Interfaces:**
- `synth_pg.seed(url: str, *, docs: int, users: int, shares_pct=10, coauthor_every=20, citations_per_doc=5,
  tags_per_doc=2, jobs_done: int, jobs_queued: int, audit_rows: int, seed: int = 1) -> dict[str, int]`.
  It runs pure `generate_series` SQL with deterministic `md5` ids (the same shape as this audit used: 1M docs
  in about 3 min) and finishes with `VACUUM (ANALYZE, PARALLEL 0)`.
- `synth_es.seed(es, index: str, *, chunks: int, dims: int, docs: int, personas: dict, seed=1, batch=2000)`.
  It writes unit-norm random vectors (numpy), Zipf-distributed vocabulary text, and ACL fields drawn from the
  persona mix: everyone 60%, org_unit 15%, private 15%, shared 10%.
- `load.run(base_url: str, tokens: list[str], scenario: Literal["search","search_filtered","browse","chat_retrieve","mcp_search","doc_read"], *, rps: float, duration_s: int, concurrency: int) -> Report`.
  Open-loop arrivals with `httpx.Client` per thread.
- `report.Report(p50, p95, p99, errors, n)`; `report.percentile(xs, q)`; `report.gate(report, budget_ms) -> bool`.
- `perf.cli`: `roracle perf seed-pg|seed-es|load|explain --budget docs/perf/budgets.yaml`.
- `perf.explain.plan(session, stmt) -> dict` (`EXPLAIN (FORMAT JSON, ANALYZE)`) and
  `perf.explain.seq_scans(plan) -> set[str]`.

- [ ] Tests: percentile math (nearest-rank); a deterministic seed produces the same ids; `seq_scans` finds a
  nested `Seq Scan` node; `gate` fails above budget.
- [ ] Integration: `seed(docs=20_000)` row counts; `synth_es.seed(chunks=10_000)` count and mapping
  (`perf-` index).
- [ ] `docs/perf/budgets.yaml` (table (b)) and `docs/perf/README.md` (how to run).
- [ ] **Commit** `feat(perf): synthetic corpus generators and load-test harness`

#### Task 10.2: Queue indexes, claim, retention and metrics (M11, H1 parts 1-4)

**Files:** `migrations/versions/0014_perf_jobs.py`, `models.py` (Job indexes), `queue.py` (`claim` two-step),
`maintenance.py` (`prune_jobs` step), `metrics.py` (`_DEPTH_SQL`); tests `tests/test_queue_claim.py`,
`tests/test_perf_plans.py` (integration).
**Interfaces:**
- Indexes:
  - `ix_jobs_due_user (run_after, id) WHERE status='queued' AND kind <> 'rebuild_units'`
  - `ix_jobs_due_bulk (run_after, id) WHERE status='queued' AND kind = 'rebuild_units'`
  - `ix_jobs_kind_status (kind, status)`
  - drop `ix_jobs_status_run_after`.
- `queue.claim(session, kinds)`: first selects non-bulk kinds ordered by `(run_after, id)`, then bulk kinds
  (same order). The SQL must repeat each index predicate literally (`kind <> 'rebuild_units'`).
- `maintenance.prune_jobs(session, *, done_days=7, failed_days=30, batch=10_000) -> int`, chained like `purge`.
- `_DEPTH_SQL` filters `status IN ('queued','running','failed')`.

- [ ] Tests: bulk jobs never win over due user jobs; ties are FIFO by id; prune keeps `queued`/`running`.
- [ ] Plan test: 200k synthetic queued jobs, `claim` uses `ix_jobs_due_user` and has no Sort node; `_scheduled`
  uses `ix_jobs_kind_status`.
- [ ] Perf: `roracle perf explain claim` p99 ≤ 5 ms at 100k queued + 3M done.
- [ ] **Commit** `perf(queue): partial claim indexes, job retention, cheap queue metrics`

#### Task 10.3: Lease-based job execution (H1 part 5, ADR-0003 amendment, opus review)

**Files:** `docs/adr/0003-postgres-job-queue.md` (amendment), `migrations/versions/0015_job_lease.py`
(`lease_until timestamptz`), `queue.py` (`run_once`, `heartbeat`, `requeue_expired`), `worker.py`;
test `tests/test_queue_lease.py`.
**Interfaces:**
- `claim` sets `status='running', lease_until=now()+lease_s` and COMMITs.
- The handler runs in a fresh session and transaction. Stage writes already use `sf`.
- `heartbeat(sf, job_id, lease_s)` runs in a daemon thread every `lease_s/3`.
- `complete` and `fail` are guarded by `WHERE status='running' AND id=:id`.
- `requeue_expired(session) -> int` (in maintenance; at-least-once preserved).
- Setting `ROR_JOB_LEASE_S=300`.
- [ ] Tests: a crashed worker (no heartbeat) is requeued after its lease expires; a slow but alive worker is never
  double-claimed; a handler exception rolls back the handler work only; `attempts` increments exactly once per
  claim.
- [ ] **Commit** `perf(queue): lease-based execution, no long-lived claim transactions`

#### Task 10.4: No-op-aware, batched ACL sync and a drift-only reconcile (C1)

**Files:** `search_index.py` (`_ACL_SCRIPT` noop, `sync_acl_many`, `acl_hash` field, `_ensure_label_mapping`
moved into `ensure_index`), `acl_sync.py` (`acl_hash(acl, title) -> str`), `maintenance.py`
(`handle_reconcile_tags` drift detection), `queue.BULK_KINDS += ("reconcile_tags",)`; tests
`tests/test_acl_sync.py`, `tests/test_acl_reconcile_es.py` (integration).
**Interfaces:**
- The script ends with `if (unchanged) { ctx.op = 'noop' }`.
- `sync_acl_many(es, items: dict[str, tuple[dict, str]])` runs one `update_by_query` over
  `terms doc_id` (≤ 500) with `params.acl[doc_id]`.
- Reconcile reads 500 doc ids and their PG `acl_hash`, then queries ES for
  `terms doc_id ∧ ¬terms acl_hash` (`collapse doc_id`) and syncs only the drifted docs.
- [ ] Tests: an unchanged document produces `noop` (ES `updated == 0`); a changed share reaches every chunk and
  unit; a reconcile of 1,000 in-sync docs performs 0 updates and enqueues ≤ 3 jobs; a stale chunk is found and
  fixed.
- [ ] Adding `reconcile_tags` to `models.BULK_KINDS` needs migration 0014b: recreate `ix_jobs_due_user`/`ix_jobs_due_bulk`
  with `kind NOT IN (...)`/`IN (...)` (CONCURRENTLY, drop-invalid-first, verify `indisvalid`);
  `tests/test_queue_claim.py::test_every_bulk_kind_is_covered...` fails until then.
- [ ] Perf: reconcile of 100k synthetic docs with 0 drift ≤ 60 s, 0 writes.
- [ ] **Commit** `perf(acl): noop-aware batched ACL sync, drift-only reconcile`

#### Task 10.5: Near-duplicate candidates through LSH and ES (C2, opus review)

**Files:** `migrations/versions/0016_version_lsh.py`, `models.py` (`VersionLsh`), `ingest/neardup.py`
(`bands(minhash) -> list[tuple[int,int]]`, `find_near_duplicate` rewritten), `ingest/pipeline.py` (`_dedupe`
writes bands), `maintenance.py` (one-off backfill job `lsh_backfill`); test `tests/test_neardup_lsh.py`.
**Interfaces:**
- `bands(mh: bytes, b=16, r=8) -> list[(band, hash64)]`.
- Candidates: `SELECT DISTINCT version_id FROM version_lsh WHERE (band,hash) IN (...)` joined to the existing
  visibility, R16 and stage predicates, plus ES kNN on `summary_embedding` (k=10, `acl_filter` of the owner).
  The thresholds are unchanged.
- [ ] Tests: a near-copy (0.95 Jaccard) is found; an unrelated doc is not; an invisible candidate is never
  returned; the summary-only path still works; the result equals the old linear scan on a 2k-doc fixture
  (property test).
- [ ] Perf: ≤ 50 ms at 1M synthetic docs.
- [ ] **Commit** `perf(ingest): LSH + kNN near-duplicate candidates`

#### Task 10.6: Citation counts and `best_targets` scoped to the requested ids (C3)

**Files:** `citations.py` (`cited_by_counts`, `best_targets(p, *, targets=None, sources=None)`),
`api/routes/citations.py`; test `tests/test_citations.py` (+ plan test).
**Interfaces:** `best_targets(p, targets: list[UUID] | None = None, sources: list[UUID] | None = None)`. With
`targets`, it pre-selects `DISTINCT (src_version_id, ref_no)` groups containing a target before the window, as
in the audit's tested SQL.
- [ ] Tests: counts are identical to the old query on the fixture, including tie rules (confidence, then oldest
  doc, then id) and invisible-candidate cases.
- [ ] Plan test: no `WindowAgg` input larger than 10x the citations of the given ids, and no `Seq Scan` on
  `doc_citations`.
- [ ] **Commit** `perf(citations): scope best-target ranking to requested ids`

#### Task 10.7: Hot-path indexes and narrow loads (C4, H7, M1, M3)

**Files:** `migrations/versions/0017_perf_indexes.py`, `models.py`, `api/routes/search.py` (`_load` uses
`load_only`, keyset `after` cursor for browse), `api/routes/me.py` (keyset), `labels/policy.py`
(`audit_inline_view` index use); test `tests/test_perf_plans.py`.
**Interfaces:**
- Indexes:
  - `ix_versions_latest_searchable (created_at DESC, document_id) WHERE is_latest AND stage='searchable'`
  - `ix_tags_tag (tag, document_id)`
  - `ix_document_shares_principal (principal_kind, principal, document_id)`
  - `ix_documents_vis_org (visibility, org_unit) WHERE deleted_at IS NULL`
  - `ix_audit_label_view (user_id, target, at) WHERE action='label.view'`
- `_load(..., columns=ROW_COLUMNS)`.
- `GET /search?after=<created_at,id>` for browse; the response keeps `total` capped at 200.
- [ ] Tests: browse order and paging are unchanged; the deferred columns are never touched by `Row`.
- [ ] Plan tests at 200k docs: browse has no Sort node spilling to disk and no `Seq Scan on document_versions`;
  tag filter uses `ix_tags_tag`; shares lookup uses `ix_document_shares_principal`.
- [ ] Perf: browse p95 ≤ 100 ms and `_load(ids=200)` ≤ 15 ms wall at 1M docs.
- [ ] **Commit** `perf(db): hot-path indexes, narrow row loads, keyset browse`

#### Task 10.8: Lean `/search` and chat retrieval (H2, H6, M4)

**Files:** `retrieval.py` (`_msearch` for BM25/kNN/sKNN, shared client, `expand_units` batched),
`api/routes/search.py` (light universe pass, single `_votes`, result cache), `chat/retrieve.py` (one
`Policy` per turn), `labels/policy.py` (`Policy.cached(session, ttl=5)`), `config.py`
(`search_rerank_depth=50`, `embed_cache_size=10000`, `search_cache_s=60`), `api/app.py` (`app.state.http`);
tests `tests/test_retrieval.py`, `tests/test_search_cache.py`, `tests/test_units_retrieval.py`.
**Interfaces:**
- `search_chunks(..., rerank_depth: int | None = None)` makes 1 `msearch` call: a fake ES records 1 request with
  3 bodies.
- `embed_query` is memoized by `(model, prompt_sha(query_prompt(q)))`.
- `expand_units` makes ≤ 1 ES call and ≤ 1 rerank call per turn.
- The cache key is `(user_id, groups, via, q, filters, sort, label policy version)`. ACL-relevant changes are
  covered by the short TTL plus the R19 PG recheck, which still runs on every request.
- [ ] Tests: RRF order is unchanged against the 3-call version (fixture); the universe pass sends no rerank;
  rerank uses one keep-alive client; a page-2 request within the TTL makes 0 ES calls; the cache is never shared
  between principals; R19 still drops a doc made private during the TTL.
- [ ] Perf: `/search` p95 ≤ 400 ms and chat retrieve ≤ 800 ms on the 50M-chunk synthetic index.
- [ ] **Commit** `perf(search): one msearch round trip, light facet pass, cached embeds and pages`

#### Task 10.9: API concurrency and connection budget (H3)

**Files:** `db.py` (`pool_size`, `max_overflow`, `pool_timeout` from settings), `config.py`, `api/app.py`
(anyio limiter at startup: `ROR_API_THREADS=200`), `api/routes/search.py` and `chat/retrieve.py` (commit before
external I/O), `deploy/helm/roracle/templates/data-postgres.yaml` (CNPG `Pooler`, `max_connections`,
`shared_buffers`, autovacuum for `jobs`), `values.yaml`; tests `tests/test_db_pool.py`, `helm template` check in
CI.
- [ ] Tests: a search does not hold a checked-out connection during the (faked) ES/rerank call (pool
  `checkedout()` == 0 inside the fake); 100 concurrent fake SSE streams do not block `/healthz`.
- [ ] Perf: 200 concurrent chats plus 50 rps of search per pod without queueing beyond budget.
- [ ] **Commit** `perf(api): explicit pool and thread budgets, PgBouncer pooler`

#### Task 10.10: ES index settings, refresh policy and capacity (C5, H4)

**Files:** `search_index.py` (`_settings(settings)` passed to `indices.create`; `refresh=False` on intermediate
ops; memoized `ensure_index`), `ingest/pipeline.py` (skip `delete_stale_chunks` on the first index of a version
and `mark_previous_versions` for v1; one final refresh), `config.py` (`es_shards`, `es_replicas`,
`es_refresh_interval`), `values.yaml` (node sizing presets for 1M/10M docs, mmap enabled through a sysctl
DaemonSet option), `docs/enterprise/capacity-planning.md` (table from C5); tests `tests/test_search_index.py`,
`tests/test_ingest_refresh.py`.
- [ ] Tests: a new index gets the configured shards and refresh interval; one ingest issues exactly 1 refresh
  (a fake ES counts `refresh` params); a searchable stage is set only after that refresh.
- [ ] Perf: ingest at 100 docs/min keeps search p95 within 10% of the idle value (harness runs both together).
- [ ] **Commit** `perf(es): sized index settings, single refresh per ingest`

#### Task 10.11: Parallel re-embed, batched rebuild, concurrent context notes (H5)

**Files:** `reembed.py` (`slices` in payload, PIT + slice, `BATCH=1000`, embed concurrency, target
`refresh_interval=-1`/replicas 0 during copy and restore before cutover), `ingest/embed.py`
(`embed(..., concurrency=1)` with a thread pool), `ingest/context.py` (bounded concurrency, label check every
32 chunks), `ingest/rebuild.py` (25 versions per job); tests `tests/test_reembed.py`,
`tests/test_context_notes.py`, `tests/test_rebuild.py`.
- [ ] Tests: slices cover every chunk exactly once (fake ES); cutover waits for all slices; settings are restored
  even on failure; notes keep their order under concurrency; an `exclude_llm` raise mid-document stops LLM calls
  within 32 chunks; the cache still works.
- [ ] Perf: re-embed of 1M synthetic chunks against a fake embedder (fixed 5 ms per request) at ≥ 2,000
  chunks/s, i.e. pipeline overhead < 10%.
- [ ] **Commit** `perf(reindex): sliced parallel re-embed and batched rebuild`

#### Task 10.12: Frontend load shaping (M7)

**Files:** `frontend/src/app/GlobalSearch.tsx` (300 ms, at least 3 chars),
`frontend/src/features/palette/CommandPalette.tsx` (`mode=quick`), `frontend/src/features/library/api.ts`
(fetch the result set once, page on the client), `backend/src/roracle/api/routes/search.py` (`mode=quick`: BM25
on titles, size ≤ 10, no kNN or rerank); tests `library.test.tsx`, `palette.test.tsx`,
`tests/test_search_quick.py`.
- [ ] Tests: typing "abc" quickly sends 1 request; page 2 sends no request; quick mode applies the ACL filter and
  makes no embed or rerank call.
- [ ] **Commit** `perf(web): quieter search typing, quick palette search, client paging`
- **Deviation (ruling, 2026-11-17):** paging stays server-side. The 10.8 page cache makes page 2 cost 0 ES/embed/rerank
  calls, and a client-held 200-result set (with passages and notes) would be a larger payload than the 20 shown.
  `mode=quick` takes only `q` (422 otherwise).

#### Task 10.13: Budget gate and report

**Files:** `.gitlab-ci.yml` (nightly `perf` job: seed 200k docs / 10M chunks, run scenarios, `roracle perf gate`),
`docs/perf/2026-11-xx-baseline.md` (before/after for every finding), `.superpowers/sdd/rq/progress.md`.
- [ ] Gate fails when any p95 exceeds `docs/perf/budgets.yaml`; the report lists the `EXPLAIN` of each hot
  query.
- [ ] **Commit** `ci(perf): nightly budget gate with synthetic corpus`

Suggested order: 10.1 → 10.2 → 10.4 → 10.6 → 10.5 → 10.7 → 10.8 → 10.9 → 10.10 → 10.3 → 10.11 → 10.12 → 10.13.
C1, C3 and the M11 part of H1 are small changes with the largest impact. Ship them first.
