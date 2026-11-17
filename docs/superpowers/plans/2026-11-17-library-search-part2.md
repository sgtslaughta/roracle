# Library search plan, part 2 (Tasks 10–18)

Part 1 (goal, global constraints, review focus, rulings, file structure, Tasks 1–9): [2026-11-17-library-search.md](2026-11-17-library-search.md).

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
