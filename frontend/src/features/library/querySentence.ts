import type { FacetKey, LibrarySearch, Sort } from "@/lib/searchParams";

/** What the clause editor shows for a clause. */
export type EditTarget = FacetKey | "q" | "year" | "added" | "pages" | "votes" | "cited" | "cites" | "owner" | "has" | "sort";
/** `remove` is the nav patch that deletes this clause (its keys set to undefined). */
export type Clause = { id: string; text: string; edit: EditTarget; remove: Partial<LibrarySearch> };
export type Names = { collection(id: string): string; label(id: string): string; doc(id: string): string | undefined };

const SCOPE_TEXT = { title: "in the title", authors: "in the authors", abstract: "in the abstract", fulltext: "in the full text" } as const;
export const SORT_TEXT: Record<Sort, string> = {
  relevance: "relevance", newest: "recently added", published_desc: "newest published", published_asc: "oldest published", votes: "most votes",
  cited: "most cited", title: "title A–Z", first_author: "first author A–Z", venue: "venue A–Z", pages: "page count",
};
const HAS = [["has_doi", "a DOI"], ["has_abstract", "an abstract"], ["has_refs", "references"]] as const;

/** "A", "A or B", "A, B or C". */
export const list = (xs: string[], joiner = "or") => (xs.length < 2 ? xs.join("") : `${xs.slice(0, -1).join(", ")} ${joiner} ${xs[xs.length - 1]}`);
const plural = (n: number, one: string, many: string) => `≥${n} ${n === 1 ? one : many}`;
const quote = (x: string) => `“${x}”`;

/** Include and exclude clauses of one facet; `inc`/`exc` turn the joined values into clause text. */
function facet(s: LibrarySearch, k: FacetKey, inc: (v: string) => string, exc: (v: string) => string, show: (v: string) => string = (v) => v): Clause[] {
  const op = k === "author" ? "author_op" : k === "tags" ? "tags_op" : undefined;
  const out: Clause[] = [];
  const yes = s[k], no = s[`${k}_not`];
  if (yes?.length) out.push({ id: k, edit: k, text: inc(list(yes.map(show), op && s[op] === "and" ? "and" : "or")), remove: op ? { [k]: undefined, [op]: undefined } : { [k]: undefined } });
  if (no?.length) out.push({ id: `${k}_not`, edit: k, text: exc(list(no.map(show))), remove: { [`${k}_not`]: undefined } });
  return out;
}

function range<T extends number | string>(lo: T | undefined, hi: T | undefined, both: (a: T, b: T) => string, from: (a: T) => string, to: (b: T) => string) {
  if (lo !== undefined && hi !== undefined) return both(lo, hi);
  if (lo !== undefined) return from(lo);
  if (hi !== undefined) return to(hi);
  return undefined;
}

/** The current search as ordered sentence clauses. */
export function buildSentence(s: LibrarySearch, names: Names): Clause[] {
  const c: Clause[] = [];
  const add = (id: string, edit: EditTarget, text: string | undefined, remove: Partial<LibrarySearch>) => { if (text) c.push({ id, edit, text, remove }); };
  if (s.q) add("q", "q", s.in && s.in !== "anywhere" ? `with ${quote(s.q)} ${SCOPE_TEXT[s.in]}` : `matching ${quote(s.q)}`, { q: undefined, in: undefined });
  c.push(...facet(s, "author", (v) => `by ${v}`, (v) => `not by ${v}`));
  add("year", "year", range(s.year_min, s.year_max, (a, b) => (a === b ? `published in ${a}` : `published ${a}–${b}`), (a) => `published ${a} or later`, (b) => `published ${b} or earlier`), { year_min: undefined, year_max: undefined });
  c.push(...facet(s, "venue", (v) => `in ${v}`, (v) => `not in ${v}`));
  c.push(...facet(s, "tags", (v) => `tagged ${v}`, (v) => `not tagged ${v}`));
  if (s.cited_min !== undefined) add("cited", "cited", `cited by ${plural(s.cited_min, "library paper", "library papers")}`, { cited_min: undefined });
  if (s.cites) { const t = names.doc(s.cites); add("cites", "cites", t === undefined ? "citing an unavailable paper" : `citing ${quote(t)}`, { cites: undefined }); }
  if (s.votes_min !== undefined) add("votes", "votes", `with ${plural(s.votes_min, "net vote", "net votes")}`, { votes_min: undefined });
  add("pages", "pages", range(s.pages_min, s.pages_max, (a, b) => `${a}–${b} pages`, (a) => `at least ${a} pages`, (b) => `at most ${b} pages`), { pages_min: undefined, pages_max: undefined });
  for (const [k, what] of HAS) if (s[k]) add(k, "has", `${s[k] === "yes" ? "with" : "without"} ${what}`, { [k]: undefined });
  c.push(...facet(s, "collection", (v) => `in collection ${v}`, (v) => `not in collection ${v}`, (id) => quote(names.collection(id))));
  if (s.owner) add("owner", "owner", s.owner === "mine" ? "uploaded by me" : "shared with me", { owner: undefined });
  c.push(...facet(s, "org_unit", (v) => `from ${v}`, (v) => `not from ${v}`));
  c.push(...facet(s, "file_type", (v) => `in ${v} format`, (v) => `not in ${v} format`, (v) => v.toUpperCase()));
  c.push(...facet(s, "label", (v) => `labelled ${v}`, (v) => `not labelled ${v}`, names.label));
  add("added", "added", range(s.added_min, s.added_max, (a, b) => `added ${a} – ${b}`, (a) => `added since ${a}`, (b) => `added on or before ${b}`), { added_min: undefined, added_max: undefined });
  if (s.sort) add("sort", "sort", `sorted by ${SORT_TEXT[s.sort]}`, { sort: undefined });
  return c;
}

export const sentenceText = (c: Clause[]) => (c.length ? `Papers ${c.map((x) => x.text).join(" · ")}` : "All papers you can see");
