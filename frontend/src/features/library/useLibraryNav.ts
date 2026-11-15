import { useNavigate, useSearch } from "@tanstack/react-router";
import type { FacetKey, LibrarySearch } from "@/lib/searchParams";

/** Library search state lives in the URL; every change resets paging. */
export function useLibraryNav() {
  const search = useSearch({ strict: false }) as LibrarySearch;
  const navigate = useNavigate();
  const go = (fn: (s: LibrarySearch) => LibrarySearch, keepPage = false) =>
    navigate({ to: "/library", search: (prev: Record<string, unknown>) => ({ ...fn(prev as LibrarySearch), ...(keepPage ? {} : { page: undefined }) }) });
  return {
    search,
    toggle: (k: FacetKey, v: string) => go((s) => ({ ...s, [k]: (s[k] ?? []).includes(v) ? (s[k] ?? []).filter((x) => x !== v) : [...(s[k] ?? []), v] })),
    add: (k: FacetKey, v: string) => go((s) => ({ ...s, [k]: [...new Set([...(s[k] ?? []), v])] })),
    remove: (k: FacetKey, v: string) => go((s) => ({ ...s, [k]: (s[k] ?? []).filter((x) => x !== v) })),
    clear: () => go((s) => ({ q: s.q, sort: s.sort })),
    setSort: (sort: LibrarySearch["sort"]) => go((s) => ({ ...s, sort })),
    setPage: (page: number) => go((s) => ({ ...s, page }), true),
  };
}
