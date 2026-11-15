/** URL <-> library search state. Multi-value facets use repeated keys: ?tags=a&tags=b */
export const ARRAY_KEYS = ["tags", "year", "file_type", "org_unit", "collection", "author"] as const;
export type FacetKey = (typeof ARRAY_KEYS)[number];
export const SORTS = ["relevance", "newest", "votes", "title"] as const;
export type Sort = (typeof SORTS)[number];

export type LibrarySearch = { q?: string; sort?: Sort; page?: number } & { [K in FacetKey]?: string[] };

const ORDER = ["q", ...ARRAY_KEYS, "sort", "page"] as const;

export function parseSearch(search: string): Record<string, unknown> {
  const sp = new URLSearchParams(search);
  const out: Record<string, unknown> = {};
  for (const k of ORDER) {
    const all = sp.getAll(k).filter(Boolean);
    if (!all.length) continue;
    if ((ARRAY_KEYS as readonly string[]).includes(k)) out[k] = all;
    else if (k === "page") out[k] = Number(all[0]) || 1;
    else out[k] = all[0];
  }
  return out;
}

export function stringifySearch(s: Record<string, unknown>): string {
  const sp = new URLSearchParams();
  for (const k of ORDER) {
    const v = s[k];
    if (Array.isArray(v)) v.forEach((x) => x !== "" && sp.append(k, String(x)));
    else if (k === "page") {
      if (typeof v === "number" && v > 1) sp.set(k, String(v));
    } else if (v != null && v !== "") sp.set(k, String(v));
  }
  const str = sp.toString();
  return str ? `?${str}` : "";
}

export function validateLibrarySearch(s: Record<string, unknown>): LibrarySearch {
  return s as LibrarySearch;
}
