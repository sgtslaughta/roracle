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
  | `global_timeout_s` | `90.0` | – |
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
- [ ] Eval: `eval compare --baseline "agentic_retrieval=false" --candidate "agentic_retrieval=true" --scope multihop --judge` → `docs/eval/2026-11-xx-agentic.md`; PASS needs multihop gate + `all` scope Δ`p_ndcg@10` ≥ −0.005; budget: retrieval p95 ≤ 3 × baseline. PASS ⇒ `agentic_retrieval: bool = True`.
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
