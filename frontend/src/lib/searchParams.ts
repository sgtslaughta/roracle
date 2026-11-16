/** URL <-> library search state. Multi-value facets use repeated keys: ?tags=a&tags=b */
export const ARRAY_KEYS = ["tags", "year", "file_type", "org_unit", "collection", "author", "label"] as const;
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

/** `extra.size` is an API-only parameter: it is sent to the backend but never shown in the browser URL. */
export function stringifySearch(s: Record<string, unknown>, extra: { size?: number; notes?: boolean } = {}): string {
  const sp = new URLSearchParams();
  for (const k of ORDER) {
    const v = s[k];
    if (Array.isArray(v)) v.forEach((x) => x !== "" && sp.append(k, String(x)));
    else if (k === "page") {
      if (typeof v === "number" && v > 1) sp.set(k, String(v));
    } else if (v != null && v !== "") sp.set(k, String(v));
  }
  if (extra.size) sp.set("size", String(extra.size));
  if (extra.notes !== undefined) sp.set("notes", String(extra.notes));
  const str = sp.toString();
  return str ? `?${str}` : "";
}

/** Route validation: keep only well-formed values so a hand-edited URL can't produce odd state. */
export function validateLibrarySearch(s: Record<string, unknown>): LibrarySearch {
  const out: LibrarySearch = {};
  if (typeof s.q === "string" && s.q) out.q = s.q;
  if (SORTS.includes(s.sort as Sort)) out.sort = s.sort as Sort;
  if (Number.isInteger(s.page) && (s.page as number) > 1) out.page = s.page as number;
  for (const k of ARRAY_KEYS) {
    const v = s[k];
    if (Array.isArray(v)) { const a = v.filter((x): x is string => typeof x === "string" && x !== ""); if (a.length) out[k] = a; }
  }
  return out;
}
