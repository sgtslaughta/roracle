# Library Search Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the library page into a full research-paper search: bibliographic, library/org and signal filters; include or exclude on every facet value; AND/OR for authors and tags; corpus-wide facet counts; nine sorts; list/compact/table views; all state in the URL; saved searches; and an editable "query sentence".

**Architecture:** Fields that never depend on the viewer (venue, DOI/abstract flags, page count, added date, sort keys) are denormalized onto every ES chunk. Signals that change (net votes, global cited-by upper bound, a `searchable` flag) live only on each version's **head chunk** (chunk 0, `doc_head: true`), written by a new `search_signals` job. Empty-query browsing moves from Postgres to an ES head-chunk query (one hit per document, ES sort + aggregations under `acl_filter`), and Postgres re-checks every returned row (R19). Query-path facets reuse the same aggregation builder, limited to the PG-re-checked candidate ids. The viewer-relative cited-by count is never stored in ES: it stays a Postgres computation over a bounded window. Saved searches are a new owner-only table (migration 0019).

**Tech Stack:** FastAPI 0.163 (query-parameter models), SQLAlchemy/Alembic, Elasticsearch 9.x Python client, React 19 + TanStack Router/Query + Radix + vitest/msw. No new dependencies.

**Spec:** The user-approved scope in the controller brief (items 1–9, quoted under "Spec items" below), `docs/spec.md` (binding, §3 access, §5 retrieval), `.superpowers/teams/README.md` (invariants), memory `roracle-design.md` (UI direction). Executors read all three.

### Spec items (verbatim summary of the approved scope)

1. Bibliographic (+ scope addition: a Journal facet keyed by ISSN, labelled by its most common venue, include/exclude; a "Has identifier" filter over DOI/arXiv/PMID/PMCID/ISBN/ACL/OpenReview/ISSN — see Task 17): publication-year range with histogram plus draggable range; authors (searchable, include/exclude); venue; has DOI / has abstract; field-scoped query (anywhere | title | authors | abstract | full text); exact phrase and `-exclude` terms (syntax plus a UI builder).
2. Library/org: tags, collections, sensitivity label, file type, org unit, owner (mine / shared with me / everyone visible), date-added range.
3. Signals: votes ≥ N, cited-by ≥ N within the library, "cites paper X", page-count range, has references.
4. Every facet value can be included or excluded; AND/OR toggle for authors and tags.
5. Facet counts cover everything the caller can see (ES aggregations under the ACL filter), replacing "Counts cover the 200 newest documents". Find box for authors, venue and tags.
6. Sorts: relevance, newest/oldest published, recently added, most votes, most cited (in library), title A–Z, first author A–Z, page count.
7. All state in the URL; saved searches stored per user (table + migration 0019), CRUD API, owner-only.
8. Views: list | compact | table with sortable columns (title, authors, year, venue, cited-by, votes, added) and a column picker.
9. Keep the visual identity (serif titles, topographic background, existing tokens). Editable query sentence; sentence case, no all-caps, keyboard/focus/reduced motion, works at phone width.

## Global Constraints

- R19: every row shown comes from `search_load._load` (Postgres `visible_documents`, latest `searchable` version) after ES. ES ACL is an optimisation only.
- Not visible ⇒ indistinguishable from absent: `cites=<invisible id>` behaves exactly like `cites=<random uuid>` (empty result, 200). Saved searches of another user ⇒ 404 `{"detail":"not found"}`.
- Labels never widen access: label filters/facets only narrow. `acl_filter(p, policy)` (with PAT `block_agents`) is in the `filter` of every ES request this plan adds, including every aggregation and the facet find endpoint.
- Viewer-relative cited-by counts are never written to ES. ES holds only `cited_by_global` (an upper bound used to prefilter/pre-rank); the number shown, filtered on and sorted by is `citations.cited_by_counts` (visible citers only).
- Files < 500 lines. CI: backend `ruff check . && ruff format --check . && pytest -q -m "not integration and not e2e"`; frontend `pnpm exec tsc --noEmit && pnpm exec vitest run && pnpm build`. Integration tests: `flock /tmp/roracle-integ.lock uv run pytest -m integration ...`.
- Conventional Commits; every commit ends with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. No push/PR/merge.
- Migration `backend/migrations/versions/0019_saved_searches.py`: `revision = "0019"`, `down_revision = "0018"`. Exactly one head on this branch.
- Limits (constants, 422 on violation): `Q_MAX=1000`, `LIST_MAX=50` values per list key, `VALUE_MAX=200` chars per value, `FIND_MAX=100` chars, `FACET_MAX=50` values per facet, `MAX_DOCS=200` (query path and cited window), `BROWSE_WINDOW=10_000` (`from+size` limit; deeper paging uses `after`), `SCOPE_IDS_MAX=10_000` doc ids per collection/cites scope, saved searches: name 1–100 chars, query ≤ 4000 chars, ≤ 200 per user.
- UI copy: sentence case everywhere; no `text-transform: uppercase`, no all-caps labels. Animations wrapped in `@media (prefers-reduced-motion: no-preference)`. Layout works at 360 px wide with no horizontal page scroll (the table scrolls inside its own wrapper).
- Perf budgets in `docs/perf/budgets.yaml` (`search` p95 400 ms, `search_filtered` p95 500 ms) stay green.
- `VERSION` bumps 0.1.0 → 0.2.0 in the last task (new feature, minor).

## Review Focus

1. **ES/PG ACL drift in counts and totals** — a revoked share or deleted doc must stop contributing to facet counts and `total` as soon as the revoking request returns (its `sync_acl` runs with `refresh=True`). Test in Task 7: revoke via API, then browse facets omit the doc.
2. **PAT principals and `block_agents`** — facet counts, histogram buckets, find-box values and totals never include docs with a `block_agents` label for a PAT caller. Tests in Task 6 (clause builder carries `acl_filter`) and Task 7 (real ES, PAT token).
3. **Same value included and excluded** (`tags=ml&tags_not=ml`, from a hand-edited URL or a stale saved search) — exclusion wins, no 422; the UI never produces it (toggling one side clears the other). Tests in Task 4 and Task 10.
4. **Malformed or stale URL state** (`year_min=abc`, `year_min>year_max`, `cites=not-a-uuid`, unknown collection id, deleted label id, unknown keys) — backend: type errors and inverted ranges 422, unknown/foreign ids give empty results; frontend: `validateLibrarySearch` drops invalid values and never crashes. Tests in Task 4 and Task 10.
5. **Find box input with regex/special characters and case** (`O'Brien (ed.)`, `smith`, `Ñúñez`, `.*`) — matches case-insensitively as a literal substring, never as a regex, never 500. Test in Task 6.

## Rulings (plan-level)

- R1 Where signals live: static bibliographic fields on every chunk (needed for kNN/BM25 filter pushdown); `votes_net`, `cited_by_global` and `searchable` on head chunks only (a vote rewrites one ES doc per version, not every chunk — each rewrite re-inserts the vector into HNSW). Query-path filters on votes/cited run as a Postgres post-filter over the ≤ `MAX_DOCS` candidates — cost if wrong: a votes filter on a query can return fewer than `MAX_DOCS` documents.
- R2 Cited-by: the "most cited" sort and `cited_min` use a **cited window**: the top `MAX_DOCS` head docs by `cited_by_global` (an upper bound, so the filter never misses), then visible counts from Postgres, filter, sort and page inside the window; `total_capped=true` when ES had more. No viewer ever sees an order or a number derived from citers they cannot see — cost: "most cited" can page only through 200 documents.
- R3 Browse moves to ES (one hit per document via `doc_head`) for every sort; Postgres re-checks each page and the page is topped up from ES (`BROWSE_TRIES=3`) if rows drop. Totals and facet counts are ES counts under `acl_filter` (corpus-wide, `track_total_hits=10000`, `total_capped` above that). The existing `after` cursor stays (opaque), now encoding ES sort values, and works for every browse sort — cost: `total` can briefly include a document whose ACL change has not reached ES yet.
- R4 Query-path facet counts come from the same aggregation builder over the head docs of the PG-re-checked candidate set (≤ `MAX_DOCS`), so they are exact. Browse counts are corpus-wide — cost: one extra ES request per filtered query (it replaces today's light second pass).
- R5 Mapping upgrade: all new fields are additive, so `ensure_index` puts them on existing indices (new ingests fill them at once). New indices carry `_meta.search_fields = 1`. Until the active index has that marker, `/search` runs the **legacy** path (today's code, moved verbatim to `search_legacy.py`), and requests that use new parameters get 503 `{"detail":"search index upgrade in progress"}`. Backfill = the existing re-embed generation path (`POST /admin/reindex` with the current model and dims): `_copy` now writes search fields and signals from Postgres, and a same-model generation reuses the stored vectors (no embedding calls); the cutover alias swap makes the new index active, and the marker turns the new path on within 30 s — cost: the legacy module lives until every deployment has reindexed (`ponytail:` debt comment).
- R6 Query syntax (own parser, not ES `simple_query_string` — with OR semantics, `foo -bar` would match nearly everything): `"…"` = exact phrase (an ES filter on both BM25 and kNN); `-word` / `-"…"` = exclude (`must_not`); everything else = words for BM25 and the embedding. Exclusions are per chunk for `anywhere`/`fulltext` (a document can still match through a chunk without the word) and exact for `title`/`authors`/`abstract` (document-level fields). A query with no positive word or phrase → 422 "add at least one word to search for".
- R7 Field scope: `title`, `authors`, `abstract` search head chunks only with BM25 (no kNN); `fulltext` = text/headings/context with kNN; `anywhere` = today's behaviour.
- R8 `added_at` = `Document.created_at` (when the paper entered the library; a new version does not re-add it). The "newest" sort keeps its name and means recently added.
- R9 The `pages` sort is ascending (shortest first); `published_desc`/`published_asc` put missing years last; every ES sort ends with `doc_id` asc as the tiebreak. A `venue` sort (A–Z) is added because the table's venue column must be sortable.
- R10 Year: the `year` list parameter is removed from the API (replaced by `year_min`/`year_max`). Old URLs `?year=2020&year=2021` are converted by the frontend to `year_min=2020&year_max=2021`. Years cannot be excluded (range only).
- R11 `owner=mine` = `owner_id == me`; `owner=shared` = visible and `owner_id != me` (co-authored and shared docs); absent = everyone visible.
- R12 Facet find box: case-insensitive literal substring via an ES `terms` `include` regex built from per-character classes (`smi` → `.*[sS][mM][iI].*`, every other character escaped). No new normalizer (would need a closed index or a new generation).
- R13 Head chunks of a version get `searchable: true` only when the `search_signals` job (enqueued by the `citations` job that `mark_searchable_and_enqueue` creates) runs, so a document mid-ingest is never counted in facets/totals.
- R14 Saved searches store the URL query string (without `?`), not a parsed object: the frontend owns its URL grammar. The server checks length and ownership only.
- R15 Journal facet (scope addition): keyed by ISSN (`issn` keyword list = canonical `ids.issn` + `ids.eissn`, so print and electronic ISSNs of one journal both match); its label is the most common `venue` string among the counted docs for that ISSN (terms sub-agg, size 1), falling back to the ISSN. `meta["ids"]` (from feat/ref-identifiers) is read defensively: absent, non-dict, or a malformed ISSN (not `^\d{4}-\d{3}[\dX]$`) contributes nothing — cost: documents ingested before ref-identifiers stay journal-less until that branch's backfill runs AND the Task 3 reindex runs after it.

---

## File structure

Backend (`backend/src/roracle/`):
- `search_fields.py` (new) — ES properties + per-version field builders + readiness marker.
- `search_signals.py` (new) — head-chunk signals: compute from PG, sync to ES, job handler, enqueue helper.
- `search_params.py` (new) — `SearchParams` query model, constants, validation.
- `search_filters.py` (new) — `SearchParams` → ES clauses (facet, plain, head).
- `search_scope.py` (new) — collection and cites doc-id scopes from Postgres.
- `search_query.py` (new) — query-string parser, BM25 clause, phrase/exclude clauses.
- `search_facets.py` (new) — disjunctive aggregation builder, reader, find-box body.
- `search_browse.py` (new) — ES head-chunk browse, sorts, cursor, PG top-up, cited window.
- `search_post.py` (new) — query-path PG post-filters and in-memory sorts.
- `search_ids.py` (new) — identifier helpers for the journal facet and the has-identifier filter (Task 17).
- `search_legacy.py` (new, moved code) — today's PG browse and Python facets, used until the index is upgraded.
- `models_saved.py` (new) + `api/routes/saved_searches.py` (new) + migration `0019_saved_searches.py`.
- Modified: `search_index.py`, `ingest/pipeline.py`, `reembed.py`, `retrieval.py`, `search_cache.py`, `cursor.py`, `citations.py`, `api/routes/search.py`, `api/routes/search_load.py`, `api/routes/library.py`, `mcp/server.py`, `worker.py`, `api/app.py`, `models.py`.

Frontend (`frontend/src/`):
- `lib/searchParams.ts` (rewrite) — URL grammar.
- `features/library/`: `useLibraryNav.ts`, `api.ts` (modify); `FacetGroup.tsx`, `FacetRail.tsx` (replace `Facets.tsx`), `YearFacet.tsx`, `RangeFields.tsx`, `SignalFilters.tsx`, `queryBuilder.ts`, `QueryBar.tsx`, `querySentence.ts`, `QuerySentence.tsx` (replaces `ActiveFilters.tsx`), `ClauseEditor.tsx`, `ViewSwitch.tsx`, `CompactRow.tsx`, `ResultTable.tsx`, `ColumnPicker.tsx`, `savedSearches.ts`, `SavedSearchesMenu.tsx`.
- `styles/library-search.css` (new, imported from `styles/globals.css`), `api/types.ts`, `mocks/search.ts` (+ `mocks/searchFilters.ts` if `search.ts` would pass 500 lines).

---

### Task 1: Search fields on ES chunks + readiness marker

**Files:**
- Create: `backend/src/roracle/search_fields.py`
- Modify: `backend/src/roracle/search_index.py` (`_mapping`, `_ensure_index` missing-field rule, `_base`, `index_chunks`)
- Modify: `backend/src/roracle/ingest/pipeline.py` (`_index`: pass `signals=`)
- Test: `backend/tests/test_search_fields.py` (unit), `backend/tests/test_search_fields_es.py` (integration)

**Interfaces:**
- Produces:
  - `SEARCH_FIELDS_VERSION = 1`
  - `SEARCH_PROPS: dict[str, dict]` = `venue` kw, `venue_sort` kw, `has_doi` boolean, `has_abstract` boolean, `has_refs` boolean, `page_count` integer, `added_at` date, `title_sort` kw, `first_author_sort` kw, `authors_text` text (analyzer `standard`), `abstract` text (analyzer `english`), `doc_head` boolean, `votes_net` integer, `cited_by_global` integer, `searchable` boolean.
  - `sort_key(s: str | None) -> str | None` — `casefold()`, whitespace collapsed, first 128 chars; None/blank → None.
  - `doc_fields(version: DocumentVersion, document: Document) -> dict` — keys `venue`, `venue_sort`, `has_doi`, `has_abstract`, `has_refs`, `page_count`, `added_at` (ISO 8601 of `document.created_at`), `title_sort` (of `document.title or meta.title or filename`), `first_author_sort`, `authors_text` (authors joined with `"; "`).
  - `head_fields(version: DocumentVersion) -> dict` — `{"doc_head": True, "abstract": meta.get("abstract") or None}`.
  - `fields_for(session, version_ids: list[UUID]) -> dict[str, tuple[dict, dict]]` — `{str(version_id): (doc_fields, head_fields)}`, one query (versions joined to documents, columns needed only — never `minhash`/`summary_embedding`).
  - `fields_ready(es, settings) -> bool` — `_meta.search_fields >= SEARCH_FIELDS_VERSION` on `active_index(settings)`; `NotFoundError` → True; memoized per process for 30 s (`time.monotonic`).
  - `search_index._mapping(dims, settings)` returns `{"_meta": {"search_fields": SEARCH_FIELDS_VERSION}, "properties": {...existing, **SEARCH_PROPS}}`.
  - `search_index.index_chunks(..., signals: dict | None = None)` — every chunk gets `doc_fields(version, document)`; chunk `n == 0` also gets `head_fields(version)` and `signals or {}`.

- [ ] **Step 1: Write the failing unit tests** (`test_search_fields.py`, no DB: build `DocumentVersion`/`Document` objects in memory)

```python
def test_doc_fields_from_meta():
    v = DocumentVersion(filename="a.pdf", page_count=12, references=[{"title": "x"}],
                        meta={"venue": "NeurIPS", "doi": "10.1/x", "abstract": "Abs", "authors": ["Émile Zola", "B"]})
    d = Document(title="  The  Title ", created_at=datetime(2025, 3, 1, tzinfo=UTC))
    f = doc_fields(v, d)
    assert f == {"venue": "NeurIPS", "venue_sort": "neurips", "has_doi": True, "has_abstract": True,
                 "has_refs": True, "page_count": 12, "added_at": "2025-03-01T00:00:00+00:00",
                 "title_sort": "the title", "first_author_sort": "émile zola", "authors_text": "Émile Zola; B"}

def test_doc_fields_missing_meta():
    f = doc_fields(DocumentVersion(filename="a.pdf", meta={}, references=[]), Document(title=None, created_at=NOW))
    assert (f["venue"], f["has_doi"], f["has_abstract"], f["has_refs"], f["first_author_sort"]) == (None, False, False, False, None)
    assert f["title_sort"] == "a.pdf"

def test_sort_key_caps_and_blank():
    assert sort_key("x" * 300) == "x" * 128 and sort_key("  ") is None and sort_key(None) is None

def test_head_fields():
    assert head_fields(DocumentVersion(meta={"abstract": "A"})) == {"doc_head": True, "abstract": "A"}

def test_mapping_has_marker_and_props():
    m = search_index._mapping(4)
    assert m["_meta"] == {"search_fields": 1} and set(SEARCH_PROPS) <= set(m["properties"])

def test_index_chunks_head_only_on_chunk_zero(monkeypatch):  # capture helpers.bulk actions
    ...  # chunks n=0,1 -> action[0] has doc_head/abstract/votes_net from signals={"votes_net": 2}; action[1] has neither; both have venue
```

- [ ] **Step 2: Run** `cd backend && uv run pytest tests/test_search_fields.py -v` — Expected: FAIL (`ModuleNotFoundError: roracle.search_fields`).
- [ ] **Step 3: Implement** `search_fields.py` as specified; in `search_index.py` merge `SEARCH_PROPS` and `_meta` into `_mapping`, extend the `_ensure_index` `missing` rule to `... or f in SEARCH_PROPS` (all additive types), add `doc_fields` to `_base`'s return (pass `document` already available), add the `signals` kwarg to `index_chunks`. Keep `search_index.py` < 500 lines.
- [ ] **Step 4: Implement** in `pipeline._index`: `sig = search_signals.signals_for(session, [d.id]).get(str(v.id))` is introduced in Task 2 — for now pass `signals=None`; Task 2 replaces it. (Keeps this task independently green.)
- [ ] **Step 5: Write the integration test** (`test_search_fields_es.py`, reuse the `es`/`wired` fixture pattern of `test_search_es.py`): an index created by `ensure_index` has `_meta.search_fields == 1`; `fields_ready` is True for it; for an index created with the pre-change mapping (`_mapping` minus `_meta`, via `es.indices.create`), `fields_ready` is False and `ensure_index` adds `venue` to its mapping.
- [ ] **Step 6: Run** unit + `flock /tmp/roracle-integ.lock uv run pytest -m integration tests/test_search_fields_es.py tests/test_search_es.py tests/test_search_index.py -v` — Expected: PASS.
- [ ] **Step 7: Commit** `feat(search): denormalize bibliographic fields onto ES chunks`

### Task 2: Head-chunk signals job

**Files:**
- Create: `backend/src/roracle/search_signals.py`
- Modify: `backend/src/roracle/api/routes/library.py` (`put_vote`), `backend/src/roracle/citations.py` (`handle_citations`), `backend/src/roracle/worker.py` (handler map), `backend/src/roracle/ingest/pipeline.py` (`_index` passes signals)
- Test: `backend/tests/test_search_signals.py` (unit, DB), `backend/tests/test_search_signals_es.py` (integration)

**Interfaces:**
- Consumes: `search_index_acl.run_update_by_query`, `ACL_BATCH`, `MUTATE_ALIAS`; `queue.enqueue`.
- Produces:
  - `signals_for(session, doc_ids: list[UUID]) -> dict[str, dict]` — `{str(version_id): {"votes_net": up - down, "cited_by_global": n, "searchable": stage == "searchable"}}` for every version of the given non-deleted documents. `cited_by_global` = count of distinct citing documents (non-deleted, latest searchable version) with any `DocCitation` row targeting the document (any confidence) — always ≥ any viewer's visible count.
  - `sync_signals(es, session_factory, doc_ids: list[str]) -> int` — `update_by_query` on `MUTATE_ALIAS`, query `bool.filter: [terms doc_id, term doc_head true]`, painless script keyed by `ctx._source.version_id`, `noop` when unchanged (same pattern as `_ACL_SCRIPT`); `compute` re-reads PG on each try; batches of `ACL_BATCH`.
  - `enqueue_signals(session, doc_ids: Iterable[UUID]) -> None` — `enqueue(session, "search_signals", {"doc_ids": sorted(map(str, set(doc_ids)))})`; no-op when empty.
  - `handle_search_signals(session, payload, *, es, session_factory) -> None` — job kind `search_signals`.

- [ ] **Step 1: Write failing tests** (`test_search_signals.py`, `db_session` + `api_seed.seed`):

```python
def test_signals_counts(db_session, ana, bob):
    d, v = seed(db_session, ana); c1, cv1 = seed(db_session, bob, visibility="private"); c2, cv2 = seed(db_session, bob)
    db_session.add_all([DocCitation(src_version_id=cv1.id, ref_no=1, target_document_id=d.id, confidence=1.0),
                        DocCitation(src_version_id=cv2.id, ref_no=1, target_document_id=d.id, confidence=0.93),
                        DocCitation(src_version_id=cv2.id, ref_no=2, target_document_id=d.id, confidence=1.0),
                        Vote(user_id=ana, document_id=d.id, value=1), Vote(user_id=bob, document_id=d.id, value=-1)])
    db_session.flush()
    assert signals_for(db_session, [d.id])[str(v.id)] == {"votes_net": 0, "cited_by_global": 2, "searchable": True}

def test_vote_enqueues_signals(client, as_ana, ana, db_session):  # PUT /documents/{id}/vote -> one queued search_signals job with that doc id

def test_citations_job_enqueues_old_and_new_targets(db_session, ...):  # handle_citations on a version whose refs moved from target A to B -> payload doc_ids == sorted([A, B, citing doc])
```

- [ ] **Step 2: Run** `uv run pytest tests/test_search_signals.py -v` — Expected: FAIL (module missing).
- [ ] **Step 3: Implement** `search_signals.py`; in `put_vote` call `enqueue_signals(session, [doc_id])` before `session.commit()`; in `handle_citations` read `old = targets of v.id` before `resolve_references`, then `enqueue_signals(session, old | new | {d.id})` after `backfill_for` (same transaction: never commits); register `"search_signals": partial(handle_search_signals, es=..., session_factory=session_factory)` in `worker.build_handlers`; in `pipeline._index` pass `signals=signals_for(session, [d.id]).get(str(v.id))` to `index_chunks` (a rebuild must not wipe head signals; a first index writes `searchable: False`).
- [ ] **Step 4: Write the integration test** (`test_search_signals_es.py`): index two chunks of one version, `sync_signals` → chunk 0 has `votes_net`, `cited_by_global`, `searchable`; chunk 1 has none; a second identical sync returns 0 updated (noop).
- [ ] **Step 5: Run** unit + integration (`flock …`) — Expected: PASS.
- [ ] **Step 6: Commit** `feat(search): head-chunk vote/citation signals job`

### Task 3: Backfill through the re-embed generation path

**Files:**
- Modify: `backend/src/roracle/reembed.py` (`_copy`, `_scan`)
- Modify: `docs/enterprise/operations-runbook.md`, `docs/enterprise/capacity-planning.md`
- Test: `backend/tests/test_reembed_search_fields.py` (integration, reuse `tests/reembed_support.py`)

**Interfaces:**
- Consumes: `search_fields.fields_for`, `search_signals.signals_for`.
- Produces: a copied chunk carries `doc_fields` from Postgres (never from the source); a copied head chunk (`chunk_id` ends `":0"`, no `unit`) also carries `head_fields` and signals. When `target["model"]` equals the source chunk's `embedding_model`, `_scan` requests vectors (`source={"exclude_vectors": False}` style, as `cached_vectors` does) and `_copy` reuses `embedding`/`summary_embedding` of length `target["dims"]`, embedding only chunks without one.

- [ ] **Step 1: Write failing tests:** (a) after a full same-model reindex of a pre-change index (no `_meta`, no search fields), the new generation has `_meta.search_fields == 1`, chunk 0 has `venue`, `doc_head`, `searchable`; (b) the fake LLM's embed call count during that same-model reindex is 0; (c) a different-model reindex still embeds every chunk.
- [ ] **Step 2: Run** `flock /tmp/roracle-integ.lock uv run pytest -m integration tests/test_reembed_search_fields.py -v` — Expected: FAIL.
- [ ] **Step 3: Implement** in `_copy`: load `fields_for` and `signals_for` inside the existing `with sf() as s:` block, `doc.update(...)` after the ACL update; vector reuse as specified. Keep `reembed.py` < 500 lines (move `_copy` to `reembed_copy.py` if needed and re-export).
- [ ] **Step 4: Run** the new test plus `tests/test_reembed*.py` — Expected: PASS.
- [ ] **Step 5: Document** in the runbook ("Library search upgrade"): after deploying, run `POST /admin/reindex {"model": <current embed model>, "dims": <current dims>}`; it copies vectors (no embedding load), writes the new fields, and swaps the `chunks` alias at cutover; until then the library uses the previous search (new filters answer 503 "search index upgrade in progress"); `DELETE /admin/reindex/old` afterwards. Capacity: the head-chunk fields add < 2 KB per document (abstract).
- [ ] **Step 6: Commit** `feat(reindex): backfill search fields and reuse vectors for same-model generations`

### Task 4: Search parameters, filter clauses and doc-id scopes

**Files:**
- Create: `backend/src/roracle/search_params.py`, `backend/src/roracle/search_filters.py`, `backend/src/roracle/search_scope.py`
- Test: `backend/tests/test_search_params.py` (unit), `backend/tests/test_search_scope.py` (DB)

**Interfaces:**
- Produces (`search_params.py`):
  - `Scope = Literal["anywhere", "title", "authors", "abstract", "fulltext"]`
  - `Sort = Literal["relevance", "newest", "published_desc", "published_asc", "votes", "cited", "title", "first_author", "venue", "pages"]`
  - `FACETS = ("tags", "author", "venue", "file_type", "org_unit", "label", "collection")`
  - `class SearchParams(BaseModel)` (`extra="forbid"`): `q: str = ""` (≤ `Q_MAX`), `in_: Scope = Field("anywhere", alias="in")`, for each facet `k`: `k: list[str] = []` and `k_not: list[str] = []` (≤ `LIST_MAX` × `VALUE_MAX`), `tags_op`/`author_op: Literal["or","and"] = "or"`, `year_min`/`year_max: int | None` (0..3000), `pages_min`/`pages_max: int | None` (≥ 0), `votes_min: int | None`, `cited_min: int | None` (≥ 1), `added_min`/`added_max: date | None`, `has_doi`/`has_abstract`/`has_refs: Literal["yes","no"] | None`, `owner: Literal["mine","shared"] | None`, `cites: UUID | None`, `sort: Sort = "relevance"`, `page: int = 1` (≥ 1), `size: int = 20` (1..50), `after: str | None` (≤ 100), `notes: bool = True`, `mode: Literal["full","quick"] = "full"`. Model validator: each `min ≤ max` else `ValueError("year_min must be <= year_max")` (same wording per pair); `label`/`label_not` keep only UUID strings; a value in both `k` and `k_not` is dropped from `k` (exclusion wins).
  - `uses_new_params(f: SearchParams) -> bool` — True if anything outside the legacy set (q, tags, author, file_type, org_unit, collection, label, sort ∈ {relevance, newest, votes, title}, page, size, after, notes, mode) is set.
  - `cache_tuple(f: SearchParams) -> tuple` — canonical, hashable tuple of every ES-relevant field (`model_dump` sorted, excluding page/size/after/notes/mode/sort).
- Produces (`search_filters.py`):
  - `facet_clauses(f, skip: str | None = None) -> tuple[list[dict], list[dict]]` — `(filter, must_not)` for the 7 facets (except `skip`) plus the year range (as facet `"year"`). Field map: tags→`tags`, author→`authors`, venue→`venue`, file_type→`file_type`, org_unit→`org_unit`, label→`label_id`, year→`year`. `or` = one `terms`; `and` = one `term` per value. Excludes = one `terms` in `must_not`. `collection` is not handled here (doc-id scopes, below).
  - `plain_clauses(f, p: Principal, scope: DocScope) -> tuple[list[dict], list[dict]]` — `pages`/`added_at` ranges, `has_*` (`term` true/false; "no" also matches a missing field via `must_not term true`), `owner`, and `scope` (`terms doc_id` include / exclude).
  - `head_clauses(f) -> list[dict]` — `term doc_head true`, `term searchable true`, `range votes_net gte`, `range cited_by_global gte` (when `cited_min`).
- Produces (`search_scope.py`):
  - `@dataclass DocScope: include: list[str] | None; exclude: list[str]; colls: dict[str, tuple[str, list[str]]]` (caller's collection id → (name, doc ids), for facet counts)
  - `resolve_scope(session, p, f) -> DocScope | None` — `None` means "no result" (a `collection` value the caller does not own, or `cites` not visible/absent). Collection include = union of owned selected collections' docs; `collection_not` → exclude; `cites` → docs whose latest searchable visible version's best visible target (`citations.best_targets(p, targets=[X])`, `rn == 1`) is X, intersected with include. Any list longer than `SCOPE_IDS_MAX` → `BadInput("collection or citation scope too large")`. `colls` = the caller's 50 most recently created collections.

- [ ] **Step 1: Write failing unit tests** (`test_search_params.py`):

```python
def test_exclusion_wins():
    f = SearchParams(tags=["ml", "cv"], tags_not=["ml"])
    assert f.tags == ["cv"] and f.tags_not == ["ml"]

@pytest.mark.parametrize("bad", [{"year_min": 2024, "year_max": 2018}, {"pages_min": 9, "pages_max": 1},
                                 {"added_min": "2025-02-01", "added_max": "2025-01-01"}])
def test_inverted_ranges_rejected(bad):
    with pytest.raises(ValidationError, match="must be <="): SearchParams(**bad)

def test_unknown_key_rejected():
    with pytest.raises(ValidationError): SearchParams(colour="red")

def test_label_keeps_uuids_only():
    assert SearchParams(label=["x", str(U)]).label == [str(U)]

def test_and_or_clauses():
    flt, _ = facet_clauses(SearchParams(author=["A", "B"], author_op="and"))
    assert {"term": {"authors": "A"}} in flt and {"term": {"authors": "B"}} in flt
    flt, nn = facet_clauses(SearchParams(tags=["a", "b"], tags_not=["c"]))
    assert {"terms": {"tags": ["a", "b"]}} in flt and nn == [{"terms": {"tags": ["c"]}}]

def test_skip_leaves_one_facet_out():
    flt, _ = facet_clauses(SearchParams(tags=["a"], venue=["V"]), skip="tags")
    assert flt == [{"terms": {"venue": ["V"]}}]

def test_year_range_is_a_facet():
    assert facet_clauses(SearchParams(year_min=2018, year_max=2024))[0] == [{"range": {"year": {"gte": 2018, "lte": 2024}}}]

def test_owner_shared(principal):
    _, nn = plain_clauses(SearchParams(owner="shared"), principal, DocScope(None, [], {}))
    assert {"term": {"owner_id": str(principal.user_id)}} in nn

def test_head_clauses():
    assert head_clauses(SearchParams(votes_min=3)) == [{"term": {"doc_head": True}}, {"term": {"searchable": True}},
                                                       {"range": {"votes_net": {"gte": 3}}}]

def test_uses_new_params():
    assert not uses_new_params(SearchParams(q="x", tags=["a"], sort="newest"))
    assert uses_new_params(SearchParams(tags_not=["a"])) and uses_new_params(SearchParams(sort="cited"))
```

- [ ] **Step 2: Write failing DB tests** (`test_search_scope.py`): foreign collection id → `None`; own collection → include ids; `collection_not` → exclude ids; `cites=<invisible doc>` → `None` and `cites=<uuid4()>` → `None` (identical); `cites=X` visible → exactly the visible citing docs whose best visible target is X.
- [ ] **Step 3: Run** `uv run pytest tests/test_search_params.py tests/test_search_scope.py -v` — Expected: FAIL (modules missing).
- [ ] **Step 4: Implement** the three modules as specified.
- [ ] **Step 5: Run** — Expected: PASS.
- [ ] **Step 6: Commit** `feat(search): search parameter model, ES filter clauses and doc-id scopes`

### Task 5: Query syntax and field scope

**Files:**
- Create: `backend/src/roracle/search_query.py`
- Modify: `backend/src/roracle/retrieval.py` (`search_chunks` kwargs)
- Test: `backend/tests/test_search_query.py` (unit), extend `backend/tests/test_search_es.py` (integration)

**Interfaces:**
- Produces:
  - `@dataclass(frozen=True) ParsedQuery: words: str; phrases: tuple[str, ...]; excluded: tuple[str, ...]`
  - `parse_query(q: str) -> ParsedQuery` — tokens: `"…"` phrase; `-word` / `-"…"` exclusion; a lone `-` or unbalanced quote is a plain character; raises `BadInput("add at least one word to search for")` when `words` and `phrases` are both empty.
  - `SCOPE_FIELDS: dict[Scope, list[str]]` — anywhere `["text^1","title^2","headings","context"]`, title `["title"]`, authors `["authors_text"]`, abstract `["abstract"]`, fulltext `["text","headings","context"]`.
  - `head_only(scope) -> bool` — True for title/authors/abstract.
  - `bm25_clause(pq, scope, units) -> dict` — `multi_match` of `words or " ".join(phrases)` over `SCOPE_FIELDS[scope]`, wrapped in today's unit `boosting` when units include summary kinds.
  - `text_clauses(pq, scope) -> tuple[list[dict], list[dict]]` — phrases → `multi_match type=phrase` in filter; excluded → `multi_match` (phrase type for multi-word) in `must_not`.
  - `embed_text(pq) -> str` — words + phrases, no exclusions.
  - `retrieval.search_chunks(..., bm25: dict | None = None, extra_filter: list[dict] | None = None, must_not: list[dict] | None = None, knn: bool = True)` — `bm25` replaces `_bm25_clause(query, units)`; `extra_filter` is appended to `flt`; `must_not` goes to `bool.must_not` of the BM25 query and of each kNN `filter`; `knn=False` sends only the BM25 body. Existing callers unchanged.

- [ ] **Step 1: Write failing unit tests:**

```python
@pytest.mark.parametrize("q,want", [
    ('graph "neural network" -survey', ParsedQuery("graph", ("neural network",), ("survey",))),
    ('-"related work" transformers', ParsedQuery("transformers", (), ("related work",))),
    ('state-of-the-art "unclosed', ParsedQuery('state-of-the-art "unclosed', (), ())),
    ('"only a phrase"', ParsedQuery("", ("only a phrase",), ())),
])
def test_parse(q, want): assert parse_query(q) == want

@pytest.mark.parametrize("q", ["-a -b", '""', "   "])
def test_needs_a_positive_term(q):
    with pytest.raises(BadInput, match="add at least one word"): parse_query(q)

def test_scope_fields_and_head_only():
    assert bm25_clause(parse_query("x"), "abstract", ("chunk",))["multi_match"]["fields"] == ["abstract"]
    assert head_only("authors") and not head_only("fulltext")

def test_text_clauses():
    flt, nn = text_clauses(parse_query('a "b c" -d'), "title")
    assert flt == [{"multi_match": {"query": "b c", "type": "phrase", "fields": ["title"]}}]
    assert nn == [{"multi_match": {"query": "d", "fields": ["title"]}}]
```

- [ ] **Step 2: Write the failing integration test** (in `test_search_es.py`): two visible docs, chunk text "alpha beta" vs "alpha gamma"; `search_chunks` with `must_not` from `text_clauses(parse_query("alpha -gamma"), "anywhere")` returns only the first doc's chunk; `knn=False` + `extra_filter=[{"term": {"doc_head": True}}]` + `bm25=bm25_clause(parse_query("Zola"), "authors", ("chunk",))` returns only the doc whose authors include "Zola".
- [ ] **Step 3: Run** unit and integration — Expected: FAIL.
- [ ] **Step 4: Implement** `search_query.py` and the `search_chunks` kwargs (keep `retrieval.py` < 500 lines).
- [ ] **Step 5: Run** — Expected: PASS.
- [ ] **Step 6: Commit** `feat(search): phrase/exclude query syntax and field-scoped search`

### Task 6: Disjunctive facet aggregations and the find box

**Files:**
- Create: `backend/src/roracle/search_facets.py`
- Test: `backend/tests/test_search_facets.py` (unit)

**Interfaces:**
- Consumes: `facet_clauses`, `DocScope.colls`, `Policy`, `label_json`.
- Produces:
  - `facet_aggs(f: SearchParams, colls: dict[str, tuple[str, list[str]]]) -> dict` — one `filter` agg per facet named `f_<name>` whose filter is `facet_clauses(f, skip=name)` (+ for `collection`, every other facet's clauses) containing `terms` (`size=FACET_MAX`, field per the Task 4 map) — `f_year` contains `histogram` (`field=year`, `interval=1`, `min_doc_count=1`) and `f_collection` contains a `filters` agg with one `terms doc_id` filter per collection in `colls`.
  - `read_facets(aggs: dict, policy: Policy, colls) -> dict[str, list[dict]]` — `{name: [{"value": str, "count": int, ...}]}`; `year` sorted by year ascending, others by `(-count, value)`; `label` entries add `"label": label_json(policy.get(value))`; `collection` entries add `"label": name` and omit zero counts.
  - `find_aggs(name: Literal["author","venue","tags"], find: str, f: SearchParams) -> dict` — one terms agg with `include` = `ci_regex(find)` and `size=FACET_MAX`, under `facet_clauses(f, skip=name)`.
  - `ci_regex(s: str) -> str` — `.*` + per char: letters → `[xX]` (both cases via `str.lower`/`str.upper`; when they are equal, the escaped char), every other char escaped for Lucene regex (`.?+*|{}[]()"\#@&<>~`) + `.*`.

- [ ] **Step 1: Write failing unit tests:**

```python
def test_each_facet_skips_itself():
    a = facet_aggs(SearchParams(tags=["a"], venue=["V"]), {})
    assert a["f_tags"]["filter"] == {"bool": {"filter": [{"terms": {"venue": ["V"]}}], "must_not": []}}
    assert a["f_venue"]["filter"] == {"bool": {"filter": [{"terms": {"tags": ["a"]}}], "must_not": []}}

def test_year_is_histogram():
    assert facet_aggs(SearchParams(), {})["f_year"]["aggs"]["v"]["histogram"] == {"field": "year", "interval": 1, "min_doc_count": 1}

@pytest.mark.parametrize("s,want", [("smi", ".*[sS][mM][iI].*"), ("O'B (ed.)", ".*[oO]'[bB] \\([eE][dD]\\.\\).*"), (".*", ".*\\.\\*.*")])
def test_ci_regex(s, want): assert ci_regex(s) == want

def test_read_facets_shapes(policy): ...  # year ascending; tags by -count; label carries label JSON; collection named, zero omitted
```

- [ ] **Step 2: Run** `uv run pytest tests/test_search_facets.py -v` — Expected: FAIL.
- [ ] **Step 3: Implement** `search_facets.py`.
- [ ] **Step 4: Run** — Expected: PASS.
- [ ] **Step 5: Commit** `feat(search): disjunctive facet aggregations and case-insensitive find`

### Task 7: ES browse path, legacy fallback, find endpoint

**Files:**
- Create: `backend/src/roracle/search_browse.py`, `backend/src/roracle/search_legacy.py` (code moved from `search.py`: `_keep`, `_facets`, `_response`, the empty-q branch and the old `search()` body as `legacy_search(...)`)
- Modify: `backend/src/roracle/api/routes/search.py` (route takes `Annotated[SearchParams, Query()]`; empty q → browse; `GET /search/facets/{name}`), `backend/src/roracle/cursor.py`, `backend/src/roracle/api/routes/search_load.py` (`ROW_COLUMNS` + `page_count`), `backend/src/roracle/mcp/server.py` (build `SearchParams(q=query, collection=cols, size=limit, notes=False)`)
- Test: `backend/tests/test_search_browse_es.py` (integration); move `tests/test_browse_keyset.py` and the browse cases of `tests/test_search_api.py` (`test_empty_q_browses_without_es`, `test_browse_filters_keep_limit`, `test_browse_author_file_type_sql_semantics`, `test_paging`) to run against `legacy_search` (they now describe the legacy path) — do not delete them.

**Interfaces:**
- Consumes: Tasks 1, 4, 6.
- Produces:
  - `cursor.make_sort_cursor(values: list) -> str` / `parse_sort_cursor(s: str) -> list` — urlsafe base64 of compact JSON; `ValueError` when malformed or not a list.
  - `ES_SORTS: dict[Sort, list[dict]]` — relevance/newest `added_at desc`; `published_desc` `year desc missing _last`; `published_asc` `year asc missing _last`; `votes` `votes_net desc missing _last`; `title` `title_sort asc`; `first_author` `first_author_sort asc missing _last`; `venue` `venue_sort asc missing _last`; `pages` `page_count asc missing _last`; `cited` `cited_by_global desc`; each then `added_at desc`, `doc_id asc`.
  - `browse(session, es, settings, p, policy, f, scope: DocScope) -> dict` → `{"rows": list[Row], "total": int, "total_capped": bool, "next": str | None, "facets": dict}`. One ES `search` on `active_index`: `query.bool.filter = [acl_filter, is_latest, *head_clauses(f), *plain filter]`, `must_not = plain must_not`, `post_filter = facet_clauses(f)`, `aggs = facet_aggs(f, scope.colls)`, `sort = ES_SORTS[f.sort]`, `_source=["doc_id"]`, `track_total_hits=BROWSE_WINDOW`, `size = 2 * f.size`, `from` = `(page-1)*size` (422 "use after to page this deep" past `BROWSE_WINDOW`) or `search_after = parse_sort_cursor(f.after)`. PG re-check via `_load(session, p, ids)` keeping ES order; top up with the next ES page (no aggs) up to `BROWSE_TRIES = 3`. `next` = cursor of the last ES hit consumed when more remain.
  - Cited window (`f.sort == "cited"` or `f.cited_min`): one ES request of `MAX_DOCS` ids sorted by `cited_by_global desc`, PG re-check, `cited_by_counts`, keep `>= cited_min`, sort by visible count desc (ties `added_at` desc, id), page in memory; `total_capped = ES total > MAX_DOCS`; `next` is None (paging by `page`).
  - Route: `search(p, session, es, llm, settings, http, f: Annotated[SearchParams, Query()]) -> dict`. Order: `mode == "quick"` → `_quick` (unchanged rules); `not fields_ready(es, settings)` → `uses_new_params(f)` ? 503 `{"detail":"search index upgrade in progress"}` : `legacy_search(...)` with `"upgrading": True` in the body; `resolve_scope` `None` → empty response; empty `q` → `browse`; else query path (Task 8). Response: `{total, total_capped, page, next, results, facets}`; `_hit` adds `"page_count"` and `"added_at"` (ISO).
  - `GET /search/facets/{name}` (`name` ∈ author|venue|tags else 404) with `f: Annotated[SearchParams, Query()]` and `find: str` (1..`FIND_MAX`) → `{"values": [{"value", "count"}]}`; ES `size=0` request with the browse filters (when `q` is set: browse filters only — the find box searches the whole visible library) and `find_aggs`.

- [ ] **Step 1: Write failing integration tests** (`test_search_browse_es.py`, fixture pattern of `test_search_es.py`, index via `index_chunks` then `sync_signals`):
  - facets count every visible doc (seed 205 docs; `facets.tags` count is 205, `total` 205, `total_capped` False) and never the private doc of another user;
  - a doc whose head lacks `searchable` (no `sync_signals`) is in neither `results`, `total` nor facets;
  - Review Focus 1: share a doc with ana, browse as ana counts it; revoke via `DELETE`/`PATCH` share API; browse facets and total drop it immediately;
  - Review Focus 2: a PAT principal (token from the existing PAT test helpers) gets facet/find counts without a `block_agents`-labelled doc that the same user sees in the UI;
  - each sort orders as specified (seed years 2018/2024/missing, titles "b"/"A", page counts) with missing values last;
  - `after` paging walks the same order as `page` paging for `sort=title`;
  - cited window: doc A cited by 2 visible docs and 3 private docs, doc B cited by 3 visible → `sort=cited` gives B, A with `cited_by_count` 3, 2; `cited_min=3` gives only B;
  - `cites=X` invisible and `cites=<uuid4>` return identical bodies;
  - find: `/search/facets/author?find=zol` returns `Émile Zola`; `find=.*` returns no values and status 200;
  - legacy: on an index created without `_meta`, `GET /search` returns `upgrading: true`, `GET /search?tags_not=x` returns 503.
- [ ] **Step 2: Run** `flock /tmp/roracle-integ.lock uv run pytest -m integration tests/test_search_browse_es.py -v` — Expected: FAIL.
- [ ] **Step 3: Implement** `cursor.py` additions, `search_browse.py`, move legacy code to `search_legacy.py` (add `# ponytail: delete once every deployment has run the search-fields reindex (R5)`), rewire `search.py` and `mcp/server.py`. Keep `search.py` < 500 lines.
- [ ] **Step 4: Run** the new tests and `uv run pytest -q -m "not integration and not e2e"` (moved legacy tests stay green) — Expected: PASS.
- [ ] **Step 5: Commit** `feat(search): corpus-wide browse and facets from Elasticsearch`

### Task 8: Query path with new filters, post-filters and sorts

**Files:**
- Create: `backend/src/roracle/search_post.py`
- Modify: `backend/src/roracle/api/routes/search.py` (`_query_rows`, query branch), `backend/src/roracle/search_cache.py` (`cache_key(..., extra: tuple = ())`)
- Test: `backend/tests/test_search_post.py` (unit), extend `backend/tests/test_search_api.py` (FakeES) and `backend/tests/test_search_browse_es.py` (integration)

**Interfaces:**
- Consumes: Tasks 4–7; `cited_by_counts`.
- Produces:
  - `post_filter(rows: list[Row], f: SearchParams, cited: dict[str, int]) -> list[Row]` — `votes_min` on `up - down`, `cited_min` on `cited`.
  - `sort_rows(rows: list[Row], sort: Sort, cited: dict[str, int]) -> None` — stable, in place, same keys and missing-last rules as `ES_SORTS` (`relevance` = score).
  - Query branch: `pq = parse_query(f.q)`; `extra_filter = facet_clauses(f)[0] + plain filter + text filter (+ [doc_head] when head_only)`, `must_not` likewise; `search_chunks(..., bm25=bm25_clause(pq, f.in_, units), extra_filter=..., must_not=..., knn=not head_only(f.in_), doc_ids=scope.include)` with the embedding of `embed_text(pq)`; `cache_key(..., extra=cache_tuple(f))`. The light universe pass runs with every non-facet clause but no facet clauses; facets = one ES `size=0` request on head docs with `terms doc_id` = PG-re-checked universe ids + `facet_aggs`. `cited` computed with one `cited_by_counts` call over the candidate ids only when `cited_min` or `sort == "cited"`; otherwise only for the returned page (today's behaviour).

- [ ] **Step 1: Write failing unit tests** (`test_search_post.py`): `sort_rows` for every `Sort` value on three `Row`s with a missing year/venue/page count (missing last), ties keep input order; `post_filter` with `votes_min=1` and `cited_min=2`.
- [ ] **Step 2: Write failing API tests** (`test_search_api.py`, FakeES records `msearch` bodies): `q='x -y' in=title` sends no kNN body and a `must_not` with `y` on `title`; `tags_not=a` reaches the BM25 `must_not`; the first-stage cache key differs between `tags_not=a` and `tags=a`; `q="-a"` → 422 "add at least one word to search for".
- [ ] **Step 3: Write the failing integration test**: query-path facets count exactly the PG-visible candidates (a private matching doc of another user is absent from `facets.tags`).
- [ ] **Step 4: Run** — Expected: FAIL.
- [ ] **Step 5: Implement** `search_post.py`, the query branch and `cache_key` extra.
- [ ] **Step 6: Run** unit, API and integration tests plus `tests/test_search_cache.py tests/test_search_quick.py tests/test_mcp*.py` — Expected: PASS.
- [ ] **Step 7: Commit** `feat(search): advanced filters, scopes and sorts on the query path`

### Task 9: Saved searches API

**Files:**
- Create: `backend/src/roracle/models_saved.py`, `backend/migrations/versions/0019_saved_searches.py`, `backend/src/roracle/api/routes/saved_searches.py`
- Modify: `backend/src/roracle/models.py` (import at the bottom like `models_labels`), `backend/src/roracle/api/app.py` (include router)
- Test: `backend/tests/test_saved_searches.py`

**Interfaces:**
- Produces:
  - Table `saved_searches`: `id uuid pk`, `owner_id uuid fk users.id ON DELETE CASCADE` (indexed), `name varchar(100)`, `query text` (≤ 4000), `created_at`, `updated_at` timestamptz default now; `UNIQUE(owner_id, name)`.
  - JSON: `{"id", "name", "query", "created_at", "updated_at"}`.
  - `GET /me/saved-searches` → list of the caller's, `updated_at desc`. `POST /me/saved-searches {name, query}` → 201. `PATCH /me/saved-searches/{sid} {name?, query?}` → 200. `DELETE /me/saved-searches/{sid}` → 204. Same principal dependency as `POST /collections`.
  - Errors: other user's or unknown id → 404 `{"detail":"not found"}`; duplicate name → 409 `{"detail":"a saved search with this name exists"}`; 201st → 422 `{"detail":"at most 200 saved searches"}`; `query` starting with `?` or containing a newline → 422; name stripped, 1–100 chars.

- [ ] **Step 1: Write failing tests:** CRUD round trip; bob GET/PATCH/DELETE ana's id → 404 with the exact body; duplicate → 409; limit → 422; user deletion cascades (DB-level); `alembic heads` prints exactly `0019 (head)` (existing migration test helpers).
- [ ] **Step 2: Run** `uv run pytest tests/test_saved_searches.py -v` — Expected: FAIL.
- [ ] **Step 3: Implement** model, migration (with the `down_revision` comment from Global Constraints), routes.
- [ ] **Step 4: Run** — Expected: PASS.
- [ ] **Step 5: Commit** `feat(search): owner-only saved searches (migration 0019)`

### Task 10: Frontend URL state, nav and API types

**Files:**
- Modify: `frontend/src/lib/searchParams.ts` (rewrite), `frontend/src/features/library/useLibraryNav.ts`, `frontend/src/features/library/api.ts`, `frontend/src/api/types.ts`, `frontend/src/mocks/search.ts`
- Test: `frontend/src/lib/searchParams.test.ts`, `frontend/src/features/library/useLibraryNav.test.tsx`

**Interfaces:**
- Produces:
  - `FACET_KEYS = ["tags","author","venue","file_type","org_unit","collection","label"] as const; type FacetKey`; `type NotKey = \`${FacetKey}_not\``; `ARRAY_KEYS` = facet + not keys (existing importers keep working).
  - `SORTS` = the 10 backend sorts; `SCOPES`; `VIEWS = ["list","compact","table"]`; `COLUMNS = ["title","authors","year","venue","cited","votes","added"]`; `DEFAULT_COLUMNS = COLUMNS`.
  - `type LibrarySearch` = `{ q?, in?: Scope, sort?: Sort, page?: number, tags_op?: "and", author_op?: "and", year_min?, year_max?, pages_min?, pages_max?, votes_min?, cited_min?: number, added_min?, added_max?: string (YYYY-MM-DD), has_doi?, has_abstract?, has_refs?: "yes" | "no", owner?: "mine" | "shared", cites?: string, view?: View, cols?: Column[] } & { [K in FacetKey | NotKey]?: string[] }`. Defaults (`in=anywhere`, ops `or`, `view=list`, `cols=all`) are never written to the URL; `cols` serialises as one comma-separated value.
  - `parseSearch`, `stringifySearch(s, extra)` (URL, includes view/cols), `toApiQuery(s, extra)` (drops view/cols), `validateLibrarySearch(raw)` — numbers must be integers in range, dates `^\d{4}-\d{2}-\d{2}$`, `cites` a UUID, invalid values dropped; legacy `year=[…]` → `year_min=min, year_max=max`; a value in both `k` and `k_not` keeps only `k_not`.
  - `useLibraryNav()` → `{ search, toggle(k: FacetKey, v, mode: "include" | "exclude"), patch(p: Partial<LibrarySearch>), clear(), setSort(s), setPage(n) }` — `toggle` include removes the value from `k_not` and vice versa; every call except `setPage` resets `page`; `clear` keeps `q`, `in`, `sort`, `view`, `cols`. `patch` with `undefined` removes a key.
  - `SearchResponse` gains `total_capped: boolean`, `next: string | null`, `upgrading?: boolean`; `FacetName` adds `venue`; `SearchHit` adds `page_count: number | null`, `added_at: string`.
  - `useSearch` uses `toApiQuery`. `RESULT_CAP` is removed; callers use `total_capped`.
  - Mock `/search` honours `_not` keys, ops, ranges, `has_*`, sorts and returns `total_capped: false`.

- [ ] **Step 1: Write failing tests:**

```ts
it("round-trips every key and omits defaults", () => {
  const s: LibrarySearch = { q: 'a "b" -c', in: "title", author: ["Smith", "Lee"], author_op: "and", tags_not: ["x"], year_min: 2018, year_max: 2024,
    added_min: "2025-01-01", has_doi: "yes", owner: "mine", cites: U, sort: "cited", view: "table", cols: ["title", "year"] };
  expect(validateLibrarySearch(parseSearch(stringifySearch(s)))).toEqual(s);
  expect(stringifySearch({ in: "anywhere", view: "list", cols: [...COLUMNS] })).toBe("");
});
it("drops malformed values", () => {
  expect(validateLibrarySearch(parseSearch("?year_min=abc&cites=nope&added_min=2025-13&pages_min=-1&sort=weird&foo=1"))).toEqual({});
});
it("converts legacy year lists", () => expect(validateLibrarySearch(parseSearch("?year=2021&year=2019"))).toEqual({ year_min: 2019, year_max: 2021 }));
it("exclusion wins in a hand-edited URL", () => expect(validateLibrarySearch(parseSearch("?tags=ml&tags_not=ml"))).toEqual({ tags_not: ["ml"] }));
it("api query drops view and cols", () => expect(toApiQuery({ q: "x", view: "table", cols: ["title"] })).toBe("?q=x"));
// useLibraryNav: toggle("tags","ml","exclude") after include leaves tags undefined and tags_not ["ml"]; page reset
```

- [ ] **Step 2: Run** `cd frontend && pnpm exec vitest run src/lib/searchParams.test.ts src/features/library/useLibraryNav.test.tsx` — Expected: FAIL.
- [ ] **Step 3: Implement** as specified; fix existing importers (`SearchPage`, `FiltersButton`, `ActiveFilters`, `Facets`) to compile.
- [ ] **Step 4: Run** `pnpm exec tsc --noEmit && pnpm exec vitest run` — Expected: PASS (existing library tests updated only where `RESULT_CAP`/year URLs changed).
- [ ] **Step 5: Commit** `feat(web): URL grammar for advanced library search`

### Task 11: Facet rail with include/exclude, AND/OR and find

**Files:**
- Create: `frontend/src/features/library/FacetGroup.tsx`, `frontend/src/features/library/FacetRail.tsx`, `frontend/src/styles/library-search.css`
- Delete: `frontend/src/features/library/Facets.tsx` (move `FACET_META` into `FacetRail.tsx`, add `venue: { label: "Venue", icon: BookOpen }`)
- Modify: `frontend/src/features/library/FiltersButton.tsx`, `frontend/src/features/library/api.ts` (`useFacetFind`), `frontend/src/styles/globals.css`, `frontend/src/mocks/search.ts` (find handler)
- Test: `frontend/src/features/library/facetRail.test.tsx`

**Interfaces:**
- Consumes: Task 10 nav.
- Produces:
  - `useFacetFind(name: "author" | "venue" | "tags", find: string, s: LibrarySearch)` — debounced 200 ms, disabled when `find` is empty, GET `/search/facets/${name}?find=…&<toApiQuery(s)>`.
  - `<FacetGroup k facet values selected onToggle onOp?>` — each row: checkbox (include) with `aria-label="Include <Facet>: <value>"`, the count, and an exclude toggle button (`aria-pressed`, `aria-label="Exclude <Facet>: <value>"`, icon `MinusCircle`); an excluded row shows the value struck through. Author and tags groups show a two-option "Match any / Match all" radiogroup when ≥ 2 values are included. Author, venue and tags groups show a find input (`aria-label="Find <facet>"`) whose results replace the list while non-empty.
  - `<FacetRail facets search nav>` — order: collection, tags, author, venue, file type, org unit, sensitivity (labels most sensitive last, as today). Replaces the two `fnote` lines with one: "Counts cover everything you can see." (browse) or "Counts cover these results." (query).

- [ ] **Step 1: Write failing tests:** clicking "Exclude Tags: ml" puts `tags_not=ml` in the URL and unchecks the include box; "Match all" for authors sets `author_op=and`; typing "zol" in "Find author" shows "Émile Zola" from the mock find handler and selecting it adds `author=Émile Zola`; no element inside the rail has computed `text-transform: uppercase`.
- [ ] **Step 2: Run** `pnpm exec vitest run src/features/library/facetRail.test.tsx` — Expected: FAIL.
- [ ] **Step 3: Implement**; styles in `library-search.css` using existing tokens only (`--primary`, `--primary-soft`, `--muted`, `--border`, `--r`), focus rings as `.result:focus-visible`.
- [ ] **Step 4: Run** `pnpm exec tsc --noEmit && pnpm exec vitest run` — Expected: PASS.
- [ ] **Step 5: Commit** `feat(web): facet rail with exclude, match all and find`

### Task 12: Year histogram, ranges and signal filters

**Files:**
- Create: `frontend/src/features/library/YearFacet.tsx`, `frontend/src/features/library/RangeFields.tsx`, `frontend/src/features/library/SignalFilters.tsx`
- Modify: `frontend/src/features/library/FacetRail.tsx`, `frontend/src/styles/library-search.css`, `frontend/src/mocks/search.ts`
- Test: `frontend/src/features/library/rangeFilters.test.tsx`

**Interfaces:**
- Produces:
  - `<YearFacet buckets={facets.year} min max onCommit(min?: number, max?: number)>` — SVG bars (one per bucket, height ∝ count, bars inside the selected range use `--primary`, others `--border`; `role="img"` with `aria-label="Publication years histogram"`), two native `<input type="range">` (`aria-label` "Earliest year" / "Latest year", the thumbs cannot cross) plus two number inputs. Dragging updates a live caption ("2018–2024") but writes the URL only on `pointerup`, `keyup` or `change` of the number inputs. Clicking a bar sets that single year.
  - `<RangeFields label kind="number" | "date" min max onCommit>` — two inputs, commit on blur or Enter; invalid or inverted input shows "Earliest must be before latest" and does not commit.
  - `<SignalFilters search nav>` — "Date added" (`RangeFields` date), "Page count" (`RangeFields` number), "At least N votes", "Cited by at least N library papers", three tri-state selects ("Has a DOI", "Has an abstract", "Has references": any / yes / no), owner radiogroup ("Everyone's", "Mine", "Shared with me"), and, when `cites` is set, a "Citing <title>" row with a remove button (title via the existing document query; on 404 show "Citing an unavailable paper").

- [ ] **Step 1: Write failing tests:** keyboard: focus "Earliest year", press ArrowRight twice, keyup → URL has `year_min` = first bucket + 2; typing 2030 / 2020 in the number inputs shows "Earliest must be before latest" and leaves the URL unchanged; choosing "Shared with me" sets `owner=shared`; "Has a DOI: no" sets `has_doi=no`; at 360 px viewport width (`window.innerWidth = 360` + container) the filter popover content has no element wider than its container (scrollWidth ≤ clientWidth).
- [ ] **Step 2: Run** `pnpm exec vitest run src/features/library/rangeFilters.test.tsx` — Expected: FAIL.
- [ ] **Step 3: Implement**; bar transitions only under `prefers-reduced-motion: no-preference`.
- [ ] **Step 4: Run** `pnpm exec tsc --noEmit && pnpm exec vitest run` — Expected: PASS.
- [ ] **Step 5: Commit** `feat(web): year histogram, range and signal filters`

### Task 13: Query builder and field scope

**Files:**
- Create: `frontend/src/features/library/queryBuilder.ts`, `frontend/src/features/library/QueryBar.tsx`
- Modify: `frontend/src/features/library/SearchPage.tsx` (render `QueryBar` under the heading), `frontend/src/features/library/highlight.tsx` (`searchTerms` ignores excluded terms and quotes)
- Test: `frontend/src/features/library/queryBuilder.test.ts`, `frontend/src/features/library/queryBar.test.tsx`

**Interfaces:**
- Produces:
  - `type QueryParts = { words: string; phrases: string[]; excluded: string[] }`
  - `splitQuery(q: string): QueryParts` — the same grammar as backend `parse_query` (Task 5; a lone `-` or unbalanced quote is plain text).
  - `joinQuery(p: QueryParts): string` — words, then `"phrase"`s, then `-word` / `-"multi word"`; trims and collapses spaces.
  - `<QueryBar search nav>` — a search input (`aria-label="Search the library"`, submits `q` on Enter), a scope select ("Anywhere", "Title", "Authors", "Abstract", "Full text") writing `in`, and a "Refine" popover with three fields: "All of these words", "This exact phrase" (one phrase per line), "None of these words"; "Apply" writes `joinQuery(...)` to `q`. An empty result of `joinQuery` with only exclusions disables Apply and shows "Add at least one word to search for".

- [ ] **Step 1: Write failing tests:**

```ts
it.each([
  ['graph "neural network" -survey', { words: "graph", phrases: ["neural network"], excluded: ["survey"] }],
  ['-"related work" transformers', { words: "transformers", phrases: [], excluded: ["related work"] }],
  ['state-of-the-art "unclosed', { words: 'state-of-the-art "unclosed', phrases: [], excluded: [] }],
])("splits %s", (q, p) => expect(splitQuery(q)).toEqual(p));
it("joins back", () => expect(joinQuery({ words: "graph", phrases: ["neural network"], excluded: ["related work", "x"] }))
  .toBe('graph "neural network" -"related work" -x'));
// QueryBar: choose "Title" -> URL in=title; Refine with only "None of these words" -> Apply disabled + message
```

- [ ] **Step 2: Run** — Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `pnpm exec tsc --noEmit && pnpm exec vitest run` — Expected: PASS.
- [ ] **Step 5: Commit** `feat(web): query builder with phrase, exclude and field scope`

### Task 14: The query sentence

**Files:**
- Create: `frontend/src/features/library/querySentence.ts`, `frontend/src/features/library/QuerySentence.tsx`, `frontend/src/features/library/ClauseEditor.tsx`
- Delete: `frontend/src/features/library/ActiveFilters.tsx`
- Modify: `frontend/src/features/library/SearchPage.tsx`, `frontend/src/styles/library-search.css`
- Test: `frontend/src/features/library/querySentence.test.ts`, `frontend/src/features/library/querySentenceUi.test.tsx`

**Interfaces:**
- Consumes: Tasks 10–13 controls (the editor popover hosts them).
- Produces:
  - `type Clause = { id: string; text: string; edit: EditTarget; remove: Partial<LibrarySearch> }` (`remove` is the `patch` that deletes this clause: keys set to `undefined`).
  - `type EditTarget = FacetKey | "q" | "year" | "added" | "pages" | "votes" | "cited" | "cites" | "owner" | "has" | "sort"`.
  - `type Names = { collection(id: string): string; label(id: string): string; doc(id: string): string | undefined }`.
  - `buildSentence(s: LibrarySearch, names: Names): Clause[]` and `sentenceText(c: Clause[]): string` = `"Papers " + texts.join(" · ")`, or `"All papers you can see"` when there are no clauses.
  - Clause order and exact copy (lists of 3+ use commas then the joiner: "A, B or C"; include and exclude of one facet are separate clauses):
    | key | include | exclude |
    |---|---|---|
    | q | `matching “q”`; scoped: `with “q” in the title` / `in the authors` / `in the abstract` / `in the full text` | — |
    | author | `by A or B` (`and` when `author_op=and`) | `not by A or B` |
    | year range | `published 2018–2024`, `published 2018 or later`, `published 2024 or earlier`, `published in 2020` | — |
    | venue | `in NeurIPS or ICML` | `not in NeurIPS` |
    | tags | `tagged ml or cv` (`and`) | `not tagged ml` |
    | cited_min | `cited by ≥3 library papers` (`≥1 library paper`) | — |
    | cites | `citing “Title”` (`citing an unavailable paper` when `doc()` is undefined) | — |
    | votes_min | `with ≥2 net votes` (`≥1 net vote`) | — |
    | pages | `20–40 pages`, `at least 20 pages`, `at most 40 pages` | — |
    | has_doi / has_abstract / has_refs | `with a DOI` / `with an abstract` / `with references` | `without a DOI` / `without an abstract` / `without references` |
    | collection | `in collection “A” or “B”` | `not in collection “A”` |
    | owner | `uploaded by me` / `shared with me` | — |
    | org_unit | `from /research` | `not from /research` |
    | file_type | `in PDF format` (value upper-cased; it is a format name) | `not in PDF format` |
    | label | `labelled General` | `not labelled Restricted` |
    | added | `added 2025-01-01 – 2025-06-30`, `added since 2025-01-01`, `added on or before 2025-06-30` | — |
    | sort (only when set in the URL) | `sorted by relevance` / `recently added` / `newest published` / `oldest published` / `most votes` / `most cited` / `title A–Z` / `first author A–Z` / `venue A–Z` / `page count` | — |
  - `<QuerySentence search nav names>` — `<p className="qsentence" aria-label="Current search">` in `--f-read` serif; "Papers" plain text, each clause a `.qchip` with two buttons: the clause text (`aria-haspopup="dialog"`, opens `<ClauseEditor edit={clause.edit}>` in a Popover that focuses its first control; Escape closes and focus returns to the chip) and `×` (`aria-label="Remove: <text>"`). Delete or Backspace on a focused chip removes it and moves focus to the next chip (or the search input when none). A "Clear all" text button when ≥ 2 clauses. Chip enter/leave transitions only under `prefers-reduced-motion: no-preference`. Chips wrap at narrow widths (no horizontal scroll).

- [ ] **Step 1: Write failing tests:**

```ts
const names = { collection: (id) => ({ c1: "Reading" })[id] ?? id, label: (id) => ({ L9: "Restricted" })[id] ?? id, doc: () => "Attention" };
it("reads the example sentence", () => {
  const s = { author: ["Smith", "Lee"], year_min: 2018, year_max: 2024, cited_min: 3, label_not: ["L9"], sort: "cited" } as LibrarySearch;
  expect(sentenceText(buildSentence(s, names)))
    .toBe("Papers by Smith or Lee · published 2018–2024 · cited by ≥3 library papers · not labelled Restricted · sorted by most cited");
});
it("empty search", () => expect(sentenceText(buildSentence({}, names))).toBe("All papers you can see"));
it("three values and and-op", () => expect(buildSentence({ tags: ["a", "b", "c"], tags_op: "and" }, names)[0].text).toBe("tagged a, b and c"));
it("remove patch deletes only its clause", () => {
  const c = buildSentence({ tags: ["a"], tags_not: ["b"] }, names);
  expect(c.map((x) => x.remove)).toEqual([{ tags: undefined }, { tags_not: undefined }]);
});
it("no all-caps copy", () => { /* every clause text for a search with every key set has no /\b[A-Z]{2,}\b/ except file types and DOI */ });
```

UI tests: clicking "Remove: published 2018–2024" removes `year_min`/`year_max` from the URL; Tab to a chip + Delete removes it and focus lands on the next chip; Enter on a chip opens its editor and Escape returns focus to the chip.
- [ ] **Step 2: Run** `pnpm exec vitest run src/features/library/querySentence*.test.ts*` — Expected: FAIL.
- [ ] **Step 3: Implement**; replace `ActiveFilters` in `SearchPage`.
- [ ] **Step 4: Run** `pnpm exec tsc --noEmit && pnpm exec vitest run` — Expected: PASS.
- [ ] **Step 5: Commit** `feat(web): editable query sentence`

### Task 15: List, compact and table views

**Files:**
- Create: `frontend/src/features/library/ViewSwitch.tsx`, `frontend/src/features/library/CompactRow.tsx`, `frontend/src/features/library/ResultTable.tsx`, `frontend/src/features/library/ColumnPicker.tsx`
- Modify: `frontend/src/features/library/SearchPage.tsx`, `frontend/src/styles/library-search.css`
- Test: `frontend/src/features/library/views.test.tsx`

**Interfaces:**
- Produces:
  - `<ViewSwitch value onChange>` — `role="radiogroup" aria-label="View"`, options "List", "Compact", "Table"; writes `view` via `nav.patch` (keeps page).
  - `<CompactRow hit onOpen>` — one line: serif title button, first author + "et al.", year, venue, cited-by and votes; `li.result.compact`.
  - `<ResultTable hits columns sort onSort onOpen>` — `<table>` inside `.rtable-wrap` (`overflow-x: auto`, title column `position: sticky; left: 0`). Column → sort: title→`title`, authors→`first_author`, year→`published_desc` (a second click → `published_asc`), venue→`venue`, cited→`cited`, votes→`votes`, added→`newest`. Each sortable `<th>` holds a button and carries `aria-sort` (`descending`/`ascending`/`none`) for the active sort. Title cells open the reader like `ResultCard`.
  - `<ColumnPicker columns onChange>` — popover with a checkbox per column ("Title" disabled and always on); writes `cols`.
  - Views share paging and the query sentence; `cols` only affects the table.

- [ ] **Step 1: Write failing tests:** `?view=table&cols=title,year` renders exactly the "Title" and "Year" headers; clicking "Year" sets `sort=published_desc`, a second click `published_asc`, and the header has `aria-sort="ascending"`; unticking "Venue" in the picker removes `venue` from `cols`; `?view=compact` renders `li.result.compact` rows; switching view keeps `page`.
- [ ] **Step 2: Run** `pnpm exec vitest run src/features/library/views.test.tsx` — Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `pnpm exec tsc --noEmit && pnpm exec vitest run` — Expected: PASS.
- [ ] **Step 5: Commit** `feat(web): compact and table views with column picker`

### Task 16: Saved searches UI

**Files:**
- Create: `frontend/src/features/library/savedSearches.ts`, `frontend/src/features/library/SavedSearchesMenu.tsx`
- Modify: `frontend/src/features/library/SearchPage.tsx` (menu in `.sortrow`), `frontend/src/api/types.ts` (`SavedSearch`), `frontend/src/mocks/handlers.ts` (+ a small `mocks/savedSearches.ts`)
- Test: `frontend/src/features/library/savedSearches.test.tsx`

**Interfaces:**
- Consumes: Task 9 API, Task 10 `stringifySearch`.
- Produces:
  - `interface SavedSearch { id: string; name: string; query: string; created_at: string; updated_at: string }`
  - `useSavedSearches()`, `useSaveSearch()` (POST), `useRenameSavedSearch()` (PATCH), `useDeleteSavedSearch()` (DELETE with an Undo toast that re-POSTs) — query key `["saved-searches"]`.
  - `<SavedSearchesMenu search>` — button "Saved searches" opening a popover: "Save this search" (name input prefilled with the sentence text truncated to 100 chars; saves `stringifySearch(search)` without `?`, `page` stripped), then the list (open = navigate to `/library?<query>`; row menu "Rename", "Delete"). 409 shows "You already have a saved search with this name."

- [ ] **Step 1: Write failing tests:** saving posts `{name, query}` with no `page` and no leading `?`; opening a saved search navigates and the sentence shows its clauses; a duplicate name shows the 409 copy; delete then Undo restores it.
- [ ] **Step 2: Run** — Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `pnpm exec tsc --noEmit && pnpm exec vitest run` — Expected: PASS.
- [ ] **Step 5: Commit** `feat(web): saved searches menu`

### Task 17: Journal (ISSN) facet and has-identifier filter

**Dependency:** run after feat/ref-identifiers merges (it adds `version.meta["ids"]` with keys `doi, arxiv, pmid, pmcid, isbn, acl, openreview, issn, eissn`). The code reads `meta["ids"]` defensively, so it may also land first; its tests seed `meta["ids"]` directly. Run the Task 3 reindex after ref-identifiers' own metadata backfill so older documents get journals.

**Files:**
- Create: `backend/src/roracle/search_ids.py`
- Modify: `backend/src/roracle/search_fields.py` (`SEARCH_PROPS`, `doc_fields`), `backend/src/roracle/search_params.py` (`journal`, `journal_not`, `has_id`, `has_id_not`), `backend/src/roracle/search_filters.py`, `backend/src/roracle/search_facets.py`, `backend/src/roracle/api/routes/search.py` (find endpoint accepts `journal`)
- Modify (frontend): `frontend/src/lib/searchParams.ts`, `frontend/src/features/library/FacetRail.tsx`, `frontend/src/features/library/SignalFilters.tsx`, `frontend/src/features/library/querySentence.ts`, `frontend/src/api/types.ts`, `frontend/src/mocks/search.ts`
- Test: `backend/tests/test_search_ids.py` (unit), extend `backend/tests/test_search_facets.py`, `backend/tests/test_search_browse_es.py` (integration), `frontend/src/features/library/journal.test.tsx`

**Interfaces:**
- Consumes: Tasks 1, 4, 6, 7, 10, 11, 12, 14.
- Produces:
  - `ID_KINDS = ("doi", "arxiv", "pmid", "pmcid", "isbn", "acl", "openreview", "issn")`
  - `ids_of(meta: dict | None) -> dict[str, str]` — `meta.get("ids")` if it is a dict else `{}`; keeps non-empty string values only; adds `doi` from legacy `meta["doi"]` when `ids` has none.
  - `issns(meta) -> list[str]` — sorted unique canonical `issn` + `eissn` matching `^\d{4}-\d{3}[\dX]$` (upper-cased `x` accepted and normalised).
  - `id_kinds(meta) -> list[str]` — sorted kinds present (`eissn` counts as `issn`).
  - `SEARCH_PROPS` adds `issn` keyword and `ids_present` keyword; `doc_fields` adds `"issn": issns(meta)`, `"ids_present": id_kinds(meta)`; `has_doi` becomes `"doi" in id_kinds(meta)`.
  - `SearchParams`: `journal`, `journal_not: list[str]` (values must match the ISSN regex, others dropped, exclusion wins); `has_id`, `has_id_not: list[Literal[*ID_KINDS]]`. `FACETS` adds `"journal"` (field `issn`). `has_id` = any-of (`terms ids_present`), `has_id_not` = none-of (`must_not terms ids_present`); both are plain (non-facet) clauses. `uses_new_params` is True for all four.
  - `facet_aggs`: `f_journal` = terms on `issn` (`size=FACET_MAX`) with sub-agg `name` = terms on `venue` `size=1`; `read_facets` journal entries are `{"value": issn, "count": n, "label": top venue or issn}`.
  - Find `name="journal"`: filter `bool.should` of `regexp venue {value: ".*" + escaped + ".*", case_insensitive: true}` and `prefix issn <find>` (`minimum_should_match=1`), then the same `f_journal` terms/sub-agg; same label rule.
  - Frontend: `FacetKey` adds `"journal"` (`FACET_META.journal = { label: "Journal", icon: Newspaper }`, rendered after venue, with a find box; rows show the label, `title` attribute shows the ISSN). `LibrarySearch` adds `has_id?: IdKind[]`, `has_id_not?: IdKind[]` (validated against `ID_KINDS`). `SignalFilters` adds a "Has identifier" group: one tri-state select per kind ("any / has / lacks") writing `has_id` / `has_id_not`. Names map: doi "DOI", arxiv "arXiv ID", pmid "PMID", pmcid "PMCID", isbn "ISBN", acl "ACL Anthology ID", openreview "OpenReview ID", issn "ISSN".
  - Sentence clauses (inserted after venue): journal `in journal “Nature”` / `in journal “Nature” or “Cell”`, exclude `not in journal “Nature”` (`Names` gains `journal(issn: string): string`, fed from `facets.journal` labels, else the ISSN); has_id `with a PMID or arXiv ID` (`with` + `a`/`an` from the first name), has_id_not `without a PMID`.

- [ ] **Step 1: Write failing unit tests** (`test_search_ids.py`):

```python
@pytest.mark.parametrize("meta,want", [
    ({}, []), ({"ids": None}, []), ({"ids": "junk"}, []),
    ({"ids": {"issn": "0028-0836", "eissn": "1476-4687"}}, ["0028-0836", "1476-4687"]),
    ({"ids": {"issn": "1234-567x", "eissn": "1234-567X"}}, ["1234-567X"]),
    ({"ids": {"issn": "12345678"}}, []),
])
def test_issns(meta, want): assert issns(meta) == want

def test_id_kinds_legacy_doi_and_eissn():
    assert id_kinds({"doi": "10.1/x", "ids": {"eissn": "1476-4687", "arxiv": "", "pmid": "123"}}) == ["doi", "issn", "pmid"]

def test_journal_values_validated_and_exclusion_wins():
    f = SearchParams(journal=["0028-0836", "nope"], journal_not=["0028-0836"])
    assert f.journal == [] and f.journal_not == ["0028-0836"]

def test_has_id_clauses(principal):
    flt, nn = plain_clauses(SearchParams(has_id=["arxiv", "pmid"], has_id_not=["doi"]), principal, DocScope(None, [], {}))
    assert {"terms": {"ids_present": ["arxiv", "pmid"]}} in flt and {"terms": {"ids_present": ["doi"]}} in nn
```

Extend `test_search_facets.py`: `read_facets` journal entry label is the sub-agg's top venue, and the ISSN when the sub-agg is empty.
- [ ] **Step 2: Write the failing integration test** (`test_search_browse_es.py`): three visible docs — two with `ids.issn="0028-0836"` and venues "Nature", "Nature", one with `ids.eissn="0028-0836"` and venue "Nature (London)", plus one with no `ids` key at all → `facets.journal == [{"value": "0028-0836", "count": 3, "label": "Nature"}]`; `journal_not=0028-0836` returns only the doc without ids; `has_id=pmid` / `has_id_not=pmid` split the corpus; `/search/facets/journal?find=natu` returns the same entry; the private doc of another user with the same ISSN is never counted.
- [ ] **Step 3: Run** `uv run pytest tests/test_search_ids.py tests/test_search_facets.py -v` and the integration file under `flock` — Expected: FAIL.
- [ ] **Step 4: Implement the backend** as specified.
- [ ] **Step 5: Write failing frontend tests** (`journal.test.tsx`): the Journal group shows "Nature" with count 3 and excluding it writes `journal_not=0028-0836`; the "PMID" select set to "lacks" writes `has_id_not=pmid`; `sentenceText(buildSentence({ journal: ["0028-0836"], has_id: ["pmid", "arxiv"] }, names))` with the journal label "Nature" is `Papers in journal “Nature” · with a PMID or arXiv ID`; `validateLibrarySearch` drops `journal=nope` and `has_id=foo`.
- [ ] **Step 6: Implement the frontend**; run `pnpm exec tsc --noEmit && pnpm exec vitest run` and the backend unit suite — Expected: PASS.
- [ ] **Step 7: Commit** `feat(search): journal facet by ISSN and has-identifier filter`

### Task 18: Page composition, upgrade notice, perf and release

**Files:**
- Modify: `frontend/src/features/library/SearchPage.tsx` (header count copy, `total_capped`, `upgrading` notice, Pager max pages), `frontend/src/styles/library-search.css`, `docs/perf/budgets.yaml` (only if a new scenario is added), `docs/enterprise/operations-runbook.md` (API notes), `VERSION`
- Test: extend `frontend/src/features/library/library.test.tsx`

**Interfaces:**
- Produces:
  - Count line: "Showing 1–20 of 1,234 papers" (`Intl.NumberFormat`); capped: "Showing 1–20 of 10,000+ papers" (browse) / "200+" (query and cited window).
  - Pager: `pages = min(ceil(total / PAGE_SIZE), BROWSE_WINDOW / PAGE_SIZE)`; at the last reachable page of a capped result show "Refine the search to see more papers."
  - `upgrading: true` → an inline note above the results: "Some filters will be available when the search index upgrade finishes." and the controls for new-only parameters are disabled (a 503 shows the same note instead of the error alert).

- [ ] **Step 1: Write failing tests:** count copy for capped/uncapped; the upgrade note and disabled exclude buttons when the mock returns `upgrading: true`; at 360 px the page has no horizontal scroll (`document.documentElement.scrollWidth <= 360`) in each view.
- [ ] **Step 2: Run** — Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run the full CI locally:** backend `cd backend && ruff check . && ruff format --check . && uv run pytest -q -m "not integration and not e2e"`; `flock /tmp/roracle-integ.lock uv run pytest -m integration -q`; frontend `cd frontend && pnpm exec tsc --noEmit && pnpm exec vitest run && pnpm build && pnpm check:size`. Expected: all green.
- [ ] **Step 5: Run the perf suite:** `cd backend && uv run roracle perf suite --profile ci` — Expected: `search` p95 ≤ 400 ms and `search_filtered` p95 ≤ 500 ms. If `search_filtered` regresses, add `"request_cache": True` to the browse aggregation request first (ES shard request cache; the ACL filter is per principal) and re-run before anything else.
- [ ] **Step 6: Bump** `VERSION` to `0.2.0`; add the `/search` parameter list, `/search/facets/{name}` and `/me/saved-searches` to `docs/enterprise/agent-integration.md` (API consumers).
- [ ] **Step 7: Commit** `feat(web): compose advanced library search; release 0.2.0`
