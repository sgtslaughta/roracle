import { useNavigate, useSearch } from "@tanstack/react-router";
import type { FacetKey, LibrarySearch, NotKey } from "@/lib/searchParams";

/** Library search state lives in the URL; every change except paging resets the page. */
export function useLibraryNav() {
  const search = useSearch({ strict: false }) as LibrarySearch;
  const navigate = useNavigate();
  const go = (fn: (s: LibrarySearch) => LibrarySearch, keepPage = false) =>
    navigate({ to: "/library", search: (prev: Record<string, unknown>) => ({ ...fn(prev as LibrarySearch), ...(keepPage ? {} : { page: undefined }) }) });
  const toggle = (k: FacetKey, v: string, mode: "include" | "exclude" = "include") =>
    go((s) => {
      const [on, off] = mode === "include" ? [k, `${k}_not` as NotKey] : [`${k}_not` as NotKey, k];
      const has = (s[on] ?? []).includes(v);
      const next = has ? (s[on] ?? []).filter((x) => x !== v) : [...(s[on] ?? []), v];
      const other = (s[off] ?? []).filter((x) => x !== v);
      return { ...s, [on]: next.length ? next : undefined, [off]: other.length ? other : undefined };
    });
  return {
    search,
    toggle,
    patch: (p: Partial<LibrarySearch>) => go((s) => ({ ...s, ...p })),
    clear: () => go((s) => ({ q: s.q, in: s.in, sort: s.sort, view: s.view, cols: s.cols })),
    setSort: (sort: LibrarySearch["sort"]) => go((s) => ({ ...s, sort })),
    setPage: (page: number) => go((s) => ({ ...s, page }), true),
  };
}
