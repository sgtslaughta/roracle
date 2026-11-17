// Mock search: term matching, facets from the matched set, sorts and the Wilson vote boost (ported from the prototype).
import type { FacetName, FacetValue, SearchHit, SearchResponse } from "@/api/types";
import { DOCS, type MockDoc } from "./docs";
import { state } from "./state";
import { docLabel, labelJson } from "./labels";

/** Collection ids are positional (c1, c2…): the backend filters and facets by id, with the name as label. */
export const COLLECTION_NAMES = [...new Set(DOCS.flatMap((d) => d.collections))];
export const collectionId = (name: string) => `c${COLLECTION_NAMES.indexOf(name) + 1}`;
const esc = (t: string) => t.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
/** Server-style highlight: escape first, then wrap matches in <em>. */
export function emphasize(text: string, ts: string[]): string {
  const safe = esc(text);
  if (!ts.length) return safe;
  const re = new RegExp(`(${ts.map((t) => esc(t).replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("|")})`, "gi");
  return safe.replace(re, "<em>$1</em>");
}

const FACETS: FacetName[] = ["tags", "year", "file_type", "org_unit", "collection", "author", "venue", "label"];
const values: Record<FacetName, (d: MockDoc) => string[]> = {
  tags: (d) => tagsOf(d),
  year: (d) => [String(d.year)],
  file_type: (d) => [d.type],
  org_unit: (d) => [d.org],
  collection: (d) => [...d.collections.map(collectionId).filter((c) => !(state.collRemoved[c] ?? []).includes(d.id)), ...Object.keys(state.collItems).filter((c) => state.collItems[c].includes(d.id))],
  author: (d) => d.authors,
  venue: (d) => [d.venue],
  label: (d) => [docLabel(d.id).id],
};

export const tagsOf = (d: MockDoc) => [...d.tags, ...(state.extraTags[d.id] ?? [])].filter((t) => !(state.removedTags[d.id] ?? []).includes(t));
/** The first sample document belongs to the mock user; the rest to someone else. */
export const ownerOf = (d: MockDoc) => state.owners[d.id] ?? (d === DOCS[0] ? "u-me" : "u-owner");
export const votesOf = (d: MockDoc) => {
  const m = state.votes[d.id] ?? 0;
  return { up: d.up + (m > 0 ? 1 : 0), down: d.down + (m < 0 ? 1 : 0), mine: m };
};
/** Mock "added to library" time: deterministic, later docs are newer. */
export const addedAt = (d: MockDoc) => `2026-0${1 + DOCS.indexOf(d) % 9}-01T00:00:00Z`;
export const citedBy = (id: string) => DOCS.filter((d) => d.refs.some((r) => r.id === id));

const terms = (q: string) => q.toLowerCase().split(/[^\p{L}\p{N}]+/u).filter((t) => t.length > 2);
function wilson(u: number, n: number) {
  if (!n) return 0.5;
  const z = 1.96, p = u / n;
  return (p + (z * z) / (2 * n) - z * Math.sqrt((p * (1 - p) + (z * z) / (4 * n)) / n)) / (1 + (z * z) / n);
}
const boost = (d: MockDoc) => {
  const v = votesOf(d);
  return 1 + 0.15 * Math.max(-1, Math.min(1, (wilson(v.up, v.up + v.down) - 0.5) * 2));
};

export const chunkId = (d: MockDoc, p: number, c: number) => `${d.id}:${p}:${c}`;

function match(d: MockDoc, ts: string[]) {
  if (!ts.length) return { score: 1, hits: [] as { p: number; c: number; n: number }[] };
  const hits: { p: number; c: number; n: number }[] = [];
  d.pages.forEach((pg, p) => pg.forEach((t, c) => {
    const s = t.toLowerCase();
    const n = ts.filter((x) => s.includes(x)).length;
    if (n) hits.push({ p, c, n });
  }));
  hits.sort((a, b) => b.n - a.n);
  const head = (d.title + " " + tagsOf(d).join(" ") + " " + d.authors.join(" ")).toLowerCase();
  return { score: ts.filter((t) => head.includes(t)).length * 3 + hits.reduce((a, x) => a + x.n, 0), hits };
}

export function runSearch(sp: URLSearchParams): SearchResponse {
  const ts = terms(sp.get("q") ?? "");
  const list = (k: string) => sp.getAll(k).flatMap((v) => v.split(","));
  const active = Object.fromEntries(FACETS.map((f) => [f, list(f)])) as Record<FacetName, string[]>;
  const excluded = Object.fromEntries(FACETS.map((f) => [f, list(`${f}_not`)])) as Record<FacetName, string[]>;
  const passes = (d: MockDoc, skip?: FacetName) =>
    FACETS.every((f) => {
      if (f === skip) return true;
      const vs = values[f](d);
      if (excluded[f].some((v) => vs.includes(v))) return false;
      if (!active[f].length) return true;
      return sp.get(`${f}_op`) === "and" ? active[f].every((v) => vs.includes(v)) : vs.some((v) => active[f].includes(v));
    }) && passesRest(d);
  const num = (k: string) => (sp.has(k) ? Number(sp.get(k)) : null);
  const yn = (k: string, has: boolean) => !sp.has(k) || (sp.get(k) === "yes") === has;
  const passesRest = (d: MockDoc) => {
    const [y0, y1, p0, p1, v0, c0] = ["year_min", "year_max", "pages_min", "pages_max", "votes_min", "cited_min"].map(num);
    const net = votesOf(d).up - votesOf(d).down, added = addedAt(d).slice(0, 10);
    return (y0 == null || d.year >= y0) && (y1 == null || d.year <= y1) && (p0 == null || d.pages.length >= p0) && (p1 == null || d.pages.length <= p1)
      && (v0 == null || net >= v0) && (c0 == null || citedBy(d.id).length >= c0)
      && (!sp.has("added_min") || added >= sp.get("added_min")!) && (!sp.has("added_max") || added <= sp.get("added_max")!)
      && yn("has_doi", false) && yn("has_abstract", true) && yn("has_refs", d.refs.length > 0)
      && (!sp.has("owner") || (sp.get("owner") === "mine") === (ownerOf(d) === "u-me"))
      && (!sp.has("cites") || d.refs.some((r) => r.id === sp.get("cites")));
  };

  const matched = DOCS.filter((d) => !state.deletedDocs.includes(d.id)).map((d) => ({ d, ...match(d, ts) })).filter((r) => r.score > 0);
  const kept = matched.filter((r) => passes(r.d));

  const sort = sp.get("sort") ?? "relevance";
  const key: Record<string, (r: (typeof kept)[number]) => number | string> = {
    relevance: (r) => -r.score * boost(r.d),
    newest: (r) => addedAt(r.d),
    votes: (r) => -(votesOf(r.d).up - votesOf(r.d).down),
    title: (r) => r.d.title,
    published_desc: (r) => -r.d.year,
    published_asc: (r) => r.d.year,
    cited: (r) => -citedBy(r.d.id).length,
    pages: (r) => r.d.pages.length,
    venue: (r) => r.d.venue,
    first_author: (r) => r.d.authors[0] ?? "",
  };
  const k = key[sort] ?? key.relevance;
  kept.sort((a, b) => (k(a) < k(b) ? -1 : k(a) > k(b) ? 1 : 0));

  // facets: counted over the matched set filtered by every OTHER facet, so a filter never hides its own siblings
  const facets = {} as Record<FacetName, FacetValue[]>;
  for (const f of FACETS) {
    const c: Record<string, number> = {};
    matched.filter((r) => passes(r.d, f)).forEach((r) => values[f](r.d).forEach((v) => (c[v] = (c[v] ?? 0) + 1)));
    facets[f] = Object.entries(c)
      .map(([value, count]): FacetValue => (f === "collection" ? { value, count, label: COLLECTION_NAMES[Number(value.slice(1)) - 1] } : f === "label" ? { value, count, label: labelJson(value) } : { value, count }))
      .sort((a, b) => (f === "year" ? Number(a.value) - Number(b.value) : b.count - a.count || a.value.localeCompare(b.value)));
  }

  const size = Math.min(50, Number(sp.get("size")) || 20);
  const page = Math.max(1, Number(sp.get("page")) || 1);
  const results: SearchHit[] = kept.slice((page - 1) * size, page * size).map(({ d, hits, score }) => ({
    doc_id: d.id, title: d.title, authors: d.authors, year: d.year, score: ts.length ? score * boost(d) : 0,
    passages: hits.slice(0, 2).map((h) => ({ page: h.p + 1, chunk_id: chunkId(d, h.p, h.c), text: d.pages[h.p][h.c], highlight: emphasize(d.pages[h.p][h.c], ts) })),
    tags: tagsOf(d), votes: votesOf(d), file_type: d.type, version: state.versions[d.id], label: labelJson(docLabel(d.id).id),
    venue: d.venue, org_unit: d.org, owner_id: ownerOf(d), flag: d.flag ?? null, cited_by_count: citedBy(d.id).length, page_count: d.pages.length, added_at: addedAt(d),
  }));
  return { total: kept.length, total_capped: false, next: null, page, results, facets };
}

/** Facet find: case-insensitive substring over the matched set's values, plus one value outside the sample for accent tests. */
export function findFacet(name: string, find: string, sp: URLSearchParams): FacetValue[] {
  const f = name as FacetName;
  const c: Record<string, number> = name === "author" ? { "Émile Zola": 1 } : {};
  runSearch(sp).facets[f]?.forEach((v) => { c[v.value] = v.count; });
  return Object.entries(c).filter(([v]) => v.toLowerCase().includes(find)).map(([value, count]) => ({ value, count }));
}
