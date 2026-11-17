/** URL <-> library search state. Multi-value facets use repeated keys: ?tags=a&tags=b */
export const FACET_KEYS = ["tags", "author", "venue", "journal", "file_type", "org_unit", "collection", "label"] as const;
export type FacetKey = (typeof FACET_KEYS)[number];
export type NotKey = `${FacetKey}_not`;
export const NOT_KEYS = FACET_KEYS.map((k) => `${k}_not`) as NotKey[];
export const ARRAY_KEYS: readonly (FacetKey | NotKey)[] = [...FACET_KEYS, ...NOT_KEYS];

export const SORTS = ["relevance", "newest", "votes", "title", "published_desc", "published_asc", "cited", "pages", "venue", "first_author"] as const;
export type Sort = (typeof SORTS)[number];
export const SCOPES = ["anywhere", "title", "authors", "abstract", "fulltext"] as const;
export type Scope = (typeof SCOPES)[number];
export const VIEWS = ["list", "compact", "table"] as const;
export type View = (typeof VIEWS)[number];
export const COLUMNS = ["title", "authors", "year", "venue", "cited", "votes", "added"] as const;
export type Column = (typeof COLUMNS)[number];
export const DEFAULT_COLUMNS: readonly Column[] = COLUMNS;

const NUM_KEYS = ["year_min", "year_max", "pages_min", "pages_max", "votes_min", "cited_min"] as const;
const DATE_KEYS = ["added_min", "added_max"] as const;
const YESNO_KEYS = ["has_doi", "has_abstract", "has_refs"] as const;
const OP_KEYS = ["tags_op", "author_op"] as const;
export const ID_KINDS = ["doi", "arxiv", "pmid", "pmcid", "isbn", "acl", "openreview", "issn"] as const;
export type IdKind = (typeof ID_KINDS)[number];
const ISSN_RE = /^\d{4}-\d{3}[\dX]$/;
type Yn = "yes" | "no";

export type LibrarySearch = {
  q?: string; in?: Scope; sort?: Sort; page?: number;
  tags_op?: "and"; author_op?: "and";
  year_min?: number; year_max?: number; pages_min?: number; pages_max?: number; votes_min?: number; cited_min?: number;
  added_min?: string; added_max?: string;
  has_doi?: Yn; has_abstract?: Yn; has_refs?: Yn;
  has_id?: IdKind[]; has_id_not?: IdKind[];
  owner?: "mine" | "shared"; cites?: string; view?: View; cols?: Column[];
} & { [K in FacetKey | NotKey]?: string[] };

const ORDER = ["q", "in", ...ARRAY_KEYS, ...OP_KEYS, ...NUM_KEYS, ...DATE_KEYS, ...YESNO_KEYS, "has_id", "has_id_not", "owner", "cites", "sort", "page", "view", "cols"] as const;
/** [min, max] per numeric key, matching backend/src/roracle/search_params.py. */
const NUM_RANGE: Record<(typeof NUM_KEYS)[number], [number, number]> = {
  year_min: [0, 3000], year_max: [0, 3000], pages_min: [0, Number.MAX_SAFE_INTEGER], pages_max: [0, Number.MAX_SAFE_INTEGER],
  votes_min: [Number.MIN_SAFE_INTEGER, Number.MAX_SAFE_INTEGER], cited_min: [1, Number.MAX_SAFE_INTEGER],
};
const DATE_RE = /^\d{4}-\d{2}-\d{2}$/;
const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const isArr = (k: string) => (ARRAY_KEYS as readonly string[]).includes(k) || k === "has_id" || k === "has_id_not";
const validDate = (v: string) => DATE_RE.test(v) && !Number.isNaN(Date.parse(v)) && new Date(v).toISOString().startsWith(v);

export function parseSearch(search: string): Record<string, unknown> {
  const sp = new URLSearchParams(search);
  const out: Record<string, unknown> = {};
  for (const k of [...ORDER, "year"]) {
    const all = sp.getAll(k).filter(Boolean);
    if (!all.length) continue;
    if (isArr(k) || k === "year") out[k] = all;
    else if (k === "cols") out[k] = all[0].split(",").filter(Boolean);
    else if ((NUM_KEYS as readonly string[]).includes(k) || k === "page") out[k] = Number(all[0]);
    else out[k] = all[0];
  }
  return out;
}

type Extra = { size?: number; notes?: boolean; mode?: "quick" };

function build(s: Record<string, unknown>, extra: Extra, ui: boolean): string {
  const sp = new URLSearchParams();
  for (const k of ORDER) {
    const v = s[k];
    if (!ui && (k === "view" || k === "cols")) continue;
    if (k === "cols") {
      if (Array.isArray(v) && v.length && v.join() !== DEFAULT_COLUMNS.join()) sp.set(k, v.join(","));
    } else if (Array.isArray(v)) v.forEach((x) => x !== "" && sp.append(k, String(x)));
    else if (k === "page") { if (typeof v === "number" && v > 1) sp.set(k, String(v)); }
    else if ((k === "in" && v === "anywhere") || (k === "view" && v === "list")) continue;
    else if (v != null && v !== "") sp.set(k, String(v));
  }
  if (extra.size) sp.set("size", String(extra.size));
  if (extra.mode) sp.set("mode", extra.mode);
  if (extra.notes !== undefined) sp.set("notes", String(extra.notes));
  const str = sp.toString();
  return str ? `?${str}` : "";
}

/** Browser URL: includes view/cols. `extra.size` etc. are API-only and normally omitted. */
export const stringifySearch = (s: Record<string, unknown>, extra: Extra = {}) => build(s, extra, true);
/** Backend query: drops view/cols (UI-only state). */
export const toApiQuery = (s: Record<string, unknown>, extra: Extra = {}) => build(s, extra, false);

/** Route validation: keep only well-formed values so a hand-edited URL can't produce odd state. */
export function validateLibrarySearch(s: Record<string, unknown>): LibrarySearch {
  const out: Record<string, unknown> = {};
  const strs = (v: unknown) => (Array.isArray(v) ? v.filter((x): x is string => typeof x === "string" && x !== "") : []);
  if (typeof s.q === "string" && s.q) out.q = s.q;
  if (SCOPES.includes(s.in as Scope) && s.in !== "anywhere") out.in = s.in;
  if (SORTS.includes(s.sort as Sort)) out.sort = s.sort;
  if (Number.isInteger(s.page) && (s.page as number) > 1) out.page = s.page;
  for (const k of ARRAY_KEYS) { const a = strs(s[k]); if (a.length) out[k] = a; }
  for (const k of ["journal", "journal_not"] as const) { const a = (out[k] as string[] | undefined)?.filter((x) => ISSN_RE.test(x)); if (a?.length) out[k] = a; else delete out[k]; }
  for (const k of ["has_id", "has_id_not"] as const) { const a = strs(s[k]).filter((x): x is IdKind => (ID_KINDS as readonly string[]).includes(x)); if (a.length) out[k] = [...new Set(a)]; }
  for (const k of FACET_KEYS) { // exclusion wins
    const ex = out[`${k}_not`] as string[] | undefined;
    if (ex && out[k]) { const a = (out[k] as string[]).filter((x) => !ex.includes(x)); if (a.length) out[k] = a; else delete out[k]; }
  }
  for (const k of OP_KEYS) if (s[k] === "and") out[k] = "and";
  for (const k of NUM_KEYS) {
    const v = s[k];
    if (Number.isInteger(v) && (v as number) >= NUM_RANGE[k][0] && (v as number) <= NUM_RANGE[k][1]) out[k] = v;
  }
  const legacy = strs(s.year).map(Number).filter((n) => Number.isInteger(n) && n >= 0 && n <= 3000);
  if (legacy.length && out.year_min === undefined && out.year_max === undefined) { out.year_min = Math.min(...legacy); out.year_max = Math.max(...legacy); }
  for (const k of DATE_KEYS) if (typeof s[k] === "string" && validDate(s[k] as string)) out[k] = s[k];
  for (const k of YESNO_KEYS) if (s[k] === "yes" || s[k] === "no") out[k] = s[k];
  for (const r of ["year", "pages", "added"]) { // the backend 422s an inverted range: drop both ends
    const lo = out[`${r}_min`] as number | string | undefined, hi = out[`${r}_max`] as number | string | undefined;
    if (lo !== undefined && hi !== undefined && lo > hi) { delete out[`${r}_min`]; delete out[`${r}_max`]; }
  }
  if (s.owner === "mine" || s.owner === "shared") out.owner = s.owner;
  if (typeof s.cites === "string" && UUID_RE.test(s.cites)) out.cites = s.cites;
  if (VIEWS.includes(s.view as View) && s.view !== "list") out.view = s.view;
  if (Array.isArray(s.cols)) {
    const c = [...new Set(s.cols.filter((x): x is Column => COLUMNS.includes(x as Column)))];
    if (c.length && c.join() !== DEFAULT_COLUMNS.join()) out.cols = c;
  }
  return out as LibrarySearch;
}
