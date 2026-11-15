import { SearchX } from "lucide-react";
import { openDoc } from "@/features/reader/useReaderStore";
import { SORTS } from "@/lib/searchParams";
import { ActiveFilters } from "./ActiveFilters";
import { Facets } from "./Facets";
import { PAGE_SIZE, useSearch } from "./api";
import { searchTerms } from "./highlight";
import { Pager } from "./Pager";
import { ResultCard } from "./ResultCard";
import { useLibraryNav } from "./useLibraryNav";

const SORT_LABEL = { relevance: "Relevance", newest: "Newest", votes: "Most useful", title: "Title" } as const;

export function SearchPage() {
  const nav = useLibraryNav();
  const { search } = nav;
  const { data, isError, error } = useSearch(search);
  const terms = searchTerms(search.q ?? "");
  const page = data?.page ?? search.page ?? 1;
  const total = data?.total ?? 0;
  const first = (page - 1) * PAGE_SIZE + 1;
  const others = (data?.results ?? []).map((r) => ({ id: r.doc_id, title: r.title }));

  return (
    <section className="view" aria-label="Library search">
      <div className="shead">
        <div>
          <h1>{search.q ? `Results for “${search.q}”` : "Library"}</h1>
          <p className="sub" aria-live="polite">
            {!data ? "Loading…" : total ? `Showing ${first}–${first + data.results.length - 1} of ${total} documents` : "No documents match"}
          </p>
        </div>
        <div className="sortrow">
          <label className="vis" htmlFor="sort">Sort</label>
          <select id="sort" className="sel" value={search.sort ?? "relevance"} onChange={(e) => nav.setSort(e.target.value as (typeof SORTS)[number])}>
            {SORTS.map((s) => <option key={s} value={s}>{SORT_LABEL[s]}</option>)}
          </select>
        </div>
      </div>
      <ActiveFilters search={search} onRemove={nav.remove} onClear={nav.clear} />
      <div className="sbody">
        <Facets facets={data?.facets} selected={search} onToggle={nav.toggle} />
        <div>
          {isError && <div className="empty" role="alert"><p>Search failed: {error instanceof Error ? error.message : "unknown error"}.</p></div>}
          <ol className="results">
            {data?.results.map((hit) => (
              <ResultCard key={hit.doc_id} hit={hit} terms={terms} others={others} onOpen={openDoc} onTag={(t) => nav.add("tags", t)} />
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
