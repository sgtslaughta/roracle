import { describe, expect, it } from "vitest";
import type { LibrarySearch } from "@/lib/searchParams";
import { buildSentence, sentenceText, type Names } from "./querySentence";

const names: Names = { collection: (id) => ({ c1: "Reading" } as Record<string, string>)[id] ?? id, label: (id) => ({ L9: "Restricted" } as Record<string, string>)[id] ?? id, doc: () => "Attention", journal: (i) => ({ "0028-0836": "Nature" } as Record<string, string>)[i] ?? i };

describe("query sentence", () => {
  it("reads the example sentence", () => {
    const s = { author: ["Smith", "Lee"], year_min: 2018, year_max: 2024, cited_min: 3, label_not: ["L9"], sort: "cited" } as LibrarySearch;
    expect(sentenceText(buildSentence(s, names)))
      .toBe("Papers by Smith or Lee · published 2018–2024 · cited by ≥3 library papers · not labelled Restricted · sorted by most cited");
  });
  it("empty search", () => expect(sentenceText(buildSentence({}, names))).toBe("All papers you can see"));
  it("three values and and-op", () => expect(buildSentence({ tags: ["a", "b", "c"], tags_op: "and" }, names)[0].text).toBe("tagged a, b and c"));
  it("remove patch deletes only its clause", () => {
    const c = buildSentence({ tags: ["a"], tags_not: ["b"] }, names);
    expect(c.map((x) => x.remove)).toEqual([{ tags: undefined }, { tags_not: undefined }]);
  });
  it("no all-caps copy", () => {
    const s: LibrarySearch = {
      q: "graph", in: "fulltext", author: ["smith"], author_not: ["lee"], year_min: 2018, year_max: 2024, venue: ["neurips"], venue_not: ["icml"],
      tags: ["ml"], tags_not: ["cv"], cited_min: 1, cites: "00000000-0000-0000-0000-000000000001", votes_min: 2, pages_min: 20, pages_max: 40,
      has_doi: "yes", has_abstract: "no", has_refs: "yes", collection: ["c1"], collection_not: ["c2"], owner: "mine", org_unit: ["/research"],
      org_unit_not: ["/ops"], file_type: ["pdf"], file_type_not: ["docx"], label: ["L1"], label_not: ["L9"], added_min: "2025-01-01", added_max: "2025-06-30", sort: "first_author",
    };
    const c = buildSentence(s, names);
    expect(c).toHaveLength(26);
    for (const x of c) {
      const t = x.edit === "file_type" ? x.text.replace(/\b(PDF|DOCX)\b/g, "") : x.text;
      expect(t.replace(/\bDOI\b/g, "")).not.toMatch(/\b[A-Z]{2,}\b/);
    }
    expect(c.find((x) => x.edit === "file_type")?.text).toBe("in PDF format");
  });
  it("covers the remaining copy", () => {
    const t = (s: LibrarySearch, n: Names = names) => buildSentence(s, n).map((c) => c.text);
    expect(t({ q: "x", in: "title" })).toEqual(["with “x” in the title"]);
    expect(t({ q: "x" })).toEqual(["matching “x”"]);
    expect(t({ year_min: 2018 })).toEqual(["published 2018 or later"]);
    expect(t({ year_max: 2024 })).toEqual(["published 2024 or earlier"]);
    expect(t({ year_min: 2020, year_max: 2020 })).toEqual(["published in 2020"]);
    expect(t({ cited_min: 1, votes_min: 1 })).toEqual(["cited by ≥1 library paper", "with ≥1 net vote"]);
    expect(t({ cites: "x" }, { ...names, doc: () => undefined })).toEqual(["citing an unavailable paper"]);
    expect(t({ cites: "x" })).toEqual(["citing “Attention”"]);
    expect(t({ pages_min: 20 })).toEqual(["at least 20 pages"]);
    expect(t({ pages_max: 40 })).toEqual(["at most 40 pages"]);
    expect(t({ pages_min: 20, pages_max: 40 })).toEqual(["20–40 pages"]);
    expect(t({ collection: ["c1", "c2"], collection_not: ["c1"] })).toEqual(["in collection “Reading” or “c2”", "not in collection “Reading”"]);
    expect(t({ owner: "shared" })).toEqual(["shared with me"]);
    expect(t({ has_doi: "no" })).toEqual(["without a DOI"]);
    expect(t({ added_min: "2025-01-01", added_max: "2025-06-30" })).toEqual(["added 2025-01-01 – 2025-06-30"]);
    expect(t({ added_min: "2025-01-01" })).toEqual(["added since 2025-01-01"]);
    expect(t({ added_max: "2025-06-30" })).toEqual(["added on or before 2025-06-30"]);
    expect(t({ sort: "title" })).toEqual(["sorted by title A–Z"]);
    expect(t({ author: ["A", "B"], author_op: "and", author_not: ["C", "D"] })).toEqual(["by A and B", "not by C or D"]);
  });
});
