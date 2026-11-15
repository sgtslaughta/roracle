// Mock search: term matching, facets from the matched set, sorts and the Wilson vote boost (ported from the prototype).
import type { FacetName, FacetValue, SearchHit, SearchResponse } from "@/api/types";
import { DOCS, type MockDoc } from "./docs";
import { state } from "./state";

const FACETS: FacetName[] = ["tags", "year", "file_type", "org_unit", "collection", "author"];
const values: Record<FacetName, (d: MockDoc) => string[]> = {
  tags: (d) => tagsOf(d),
  year: (d) => [String(d.year)],
  file_type: (d) => [d.type],
  org_unit: (d) => [d.org],
  collection: (d) => d.collections,
  author: (d) => d.authors,
};

export const tagsOf = (d: MockDoc) => [...d.tags, ...(state.extraTags[d.id] ?? [])];
export const votesOf = (d: MockDoc) => {
  const m = state.votes[d.id] ?? 0;
  return { up: d.up + (m > 0 ? 1 : 0), down: d.down + (m < 0 ? 1 : 0), mine: m };
};
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
  const active = Object.fromEntries(FACETS.map((f) => [f, sp.getAll(f).flatMap((v) => v.split(","))])) as Record<FacetName, string[]>;
  const passes = (d: MockDoc, skip?: FacetName) =>
    FACETS.every((f) => f === skip || !active[f].length || values[f](d).some((v) => active[f].includes(v)));

  const matched = DOCS.map((d) => ({ d, ...match(d, ts) })).filter((r) => r.score > 0);
  const kept = matched.filter((r) => passes(r.d));

  const sort = sp.get("sort") ?? "relevance";
  const key: Record<string, (r: (typeof kept)[number]) => number | string> = {
    relevance: (r) => -r.score * boost(r.d),
    newest: (r) => -r.d.year,
    votes: (r) => -(votesOf(r.d).up - votesOf(r.d).down),
    title: (r) => r.d.title,
  };
  const k = key[sort] ?? key.relevance;
  kept.sort((a, b) => (k(a) < k(b) ? -1 : k(a) > k(b) ? 1 : 0));

  // facets: counted over the matched set filtered by every OTHER facet, so a filter never hides its own siblings
  const facets = {} as Record<FacetName, FacetValue[]>;
  for (const f of FACETS) {
    const c: Record<string, number> = {};
    matched.filter((r) => passes(r.d, f)).forEach((r) => values[f](r.d).forEach((v) => (c[v] = (c[v] ?? 0) + 1)));
    facets[f] = Object.entries(c)
      .map(([value, count]) => ({ value, count }))
      .sort((a, b) => (f === "year" ? Number(b.value) - Number(a.value) : b.count - a.count || a.value.localeCompare(b.value)));
  }

  const size = Math.min(50, Number(sp.get("size")) || 20);
  const page = Math.max(1, Number(sp.get("page")) || 1);
  const results: SearchHit[] = kept.slice((page - 1) * size, page * size).map(({ d, hits, score }) => ({
    doc_id: d.id, title: d.title, authors: d.authors, year: d.year, score: score * boost(d),
    passages: (hits.length ? hits : [{ p: 0, c: 0 }]).slice(0, 2).map((h) => ({ page: h.p + 1, chunk_id: chunkId(d, h.p, h.c), text: d.pages[h.p][h.c] })),
    tags: tagsOf(d), votes: votesOf(d), file_type: d.type, version: state.versions[d.id],
    venue: d.venue, org_unit: d.org, flag: d.flag ?? null, cited_by_count: citedBy(d.id).length,
  }));
  return { total: kept.length, page, results, facets };
}
