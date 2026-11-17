import { SearchX } from "lucide-react";
import { openDoc } from "@/features/reader/useReaderStore";
import { ARRAY_KEYS, DEFAULT_COLUMNS, type LibrarySearch } from "@/lib/searchParams";
import { ContinueInAgent } from "@/features/agents/ContinueInAgent";
import type { HandoffSource } from "@/features/agents/HandoffDialog";
import { FacetRail } from "./FacetRail";
import { FiltersButton } from "./FiltersButton";
import { QueryBar } from "./QueryBar";
import { QuerySentence, useSentenceNames } from "./QuerySentence";
import { SavedSearchesMenu } from "./SavedSearchesMenu";
import { SortSelect } from "./SortSelect";
import { PAGE_SIZE, useCollectionName, useSearch } from "./api";
import { searchTerms } from "./highlight";
import { ColumnPicker } from "./ColumnPicker";
import { CompactRow } from "./CompactRow";
import { Pager } from "./Pager";
import { ResultTable } from "./ResultTable";
import { ViewSwitch } from "./ViewSwitch";
import { ResultCard } from "./ResultCard";
import { useLibraryNav } from "./useLibraryNav";

/** Exactly one collection filter hands off the collection; otherwise the search with this page's results. */
function searchHandoff(search: LibrarySearch, docIds: string[], total: number, collName: (id: string) => string): HandoffSource {
  const q = search.q ?? "";
  const coll = search.collection?.length === 1 ? search.collection[0] : null;
  if (coll) {
    const name = collName(coll);
    return {
      kind: "collection", sourceId: coll, docIds: [], title: name.slice(0, 200),
      goal: `Work through the “${name}” collection and summarise what's settled and what's open.`,
      brief: `Goal: work through the “${name}” collection and summarise what's settled and what's open.${q ? `\nFocus: “${q}”.` : ""}`,
      summary: "Hands this collection to an agent: its documents as of now, and the notes on them.",
    };
  }
  const filters = ARRAY_KEYS.flatMap((k) => (search[k] ?? []).map((v) => (k === "collection" ? collName(v) : v))).join(", ");
  return {
    kind: "search", docIds, title: (q ? `Search: ${q}` : "Library search").slice(0, 200),
    goal: q ? `Review the documents matching “${q}” and pull out the main results.` : "Review these documents and pull out the main results.",
    brief: `Query: ${q ? `“${q}”` : "none"}\nFilters: ${filters || "none"}\n${total} matching documents; the ${docIds.length} on this page are included.`,
    summary: "Hands this search to an agent, with its query, filters and the results on this page.",
  };
}

export function SearchPage() {
  const nav = useLibraryNav();
  const { search } = nav;
  const { data, isError, error } = useSearch(search);
  const terms = searchTerms(search.q ?? "");
  const page = data?.page ?? search.page ?? 1;
  const total = data?.total ?? 0;
  const capped = data?.total_capped ?? false;
  const first = (page - 1) * PAGE_SIZE + 1;
  const others = (data?.results ?? []).map((r) => ({ id: r.doc_id, title: r.title }));
  const collName = useCollectionName();
  const view = search.view ?? "list";
  const names = useSentenceNames(search.cites, data?.facets);

  return (
    <section className="view" aria-label="Library search">
      <div className="shead">
        <div>
          <h1>{search.q ? `Results for “${search.q}”` : "Library"}</h1>
          <p className="sub" aria-live="polite">
            {!data ? "Loading…" : total ? `Showing ${first}–${first + data.results.length - 1} of ${total}${capped ? "+" : ""} documents` : "No documents match"}
          </p>
        </div>
        <div className="sortrow">
          <ContinueInAgent source={() => searchHandoff(search, others.map((o) => o.id), total, collName)} />
          <FiltersButton facets={data?.facets} search={search} nav={nav} />
          <ViewSwitch value={view} onChange={(v) => nav.patch({ view: v }, true)} />
          {view === "table" && <ColumnPicker columns={search.cols ?? DEFAULT_COLUMNS} onChange={(cols) => nav.patch({ cols })} />}
          <SavedSearchesMenu search={search} />
          <SortSelect value={search.sort} onChange={nav.setSort} />
        </div>
      </div>
      <QueryBar search={search} nav={nav} />
      <QuerySentence search={search} nav={nav} names={names} facets={data?.facets} />
      <div className="sbody">
        <div className="facet-rail"><FacetRail facets={data?.facets} search={search} nav={nav} /></div>
        <div>
          {isError && <div className="empty" role="alert"><p>Search failed: {error instanceof Error ? error.message : "unknown error"}.</p></div>}
          {view === "table" && data && !!data.results.length && (
            <ResultTable hits={data.results} columns={search.cols ?? DEFAULT_COLUMNS} sort={search.sort ?? "relevance"} onSort={nav.setSort} onOpen={openDoc} />
          )}
          <ol className={view === "compact" ? "results compact" : "results"} hidden={view === "table" && !!data?.results.length}>
            {view === "compact" && data?.results.map((hit) => <CompactRow key={hit.doc_id} hit={hit} onOpen={openDoc} />)}
            {view === "list" && data?.results.map((hit) => (
              <ResultCard key={hit.doc_id} hit={hit} terms={terms} others={others} onOpen={openDoc} onTag={(t) => nav.patch({ tags: [...new Set([...(search.tags ?? []), t])], tags_not: undefined })} collectionId={search.collection?.length === 1 ? search.collection[0] : undefined} />
            ))}
            {data && !data.results.length && (
              <li className="empty">
                <SearchX aria-hidden />
                <p>Nothing you can see matches “{search.q ?? ""}”. Try fewer words, or clear the filters.</p>
              </li>
            )}
          </ol>
          <Pager page={page} pages={Math.max(1, Math.ceil(total / PAGE_SIZE))} onPage={nav.setPage} />
        </div>
      </div>
    </section>
  );
}
