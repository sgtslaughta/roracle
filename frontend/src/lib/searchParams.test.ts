import { describe, expect, it } from "vitest";
import { COLUMNS, parseSearch, stringifySearch, toApiQuery, validateLibrarySearch, type LibrarySearch } from "./searchParams";

const U = "123e4567-e89b-42d3-a456-426614174000";

describe("search params", () => {
  it("round-trips repeated keys and numbers", () => {
    const s = { q: "spectral gap", tags: ["a", "b"], year_min: 2021, sort: "newest", page: 2 };
    const url = stringifySearch(s);
    expect(url).toBe("?q=spectral+gap&tags=a&tags=b&year_min=2021&sort=newest&page=2");
    expect(parseSearch(url)).toEqual(s);
  });
  it("omits empty values and page 1", () => {
    expect(stringifySearch({ q: "", tags: [], page: 1 })).toBe("");
  });
  it("parses single array values as arrays", () => {
    expect(parseSearch("?collection=Thesis")).toEqual({ collection: ["Thesis"] });
  });
});

describe("validateLibrarySearch", () => {
  it("drops malformed values", () => {
    expect(validateLibrarySearch({ q: "x", sort: "bogus", page: -3, tags: ["a", 5, ""], year: "2021" })).toEqual({ q: "x", tags: ["a"] });
    expect(validateLibrarySearch({ sort: "newest", page: 4 })).toEqual({ sort: "newest", page: 4 });
  });
  it("round-trips every key and omits defaults", () => {
    const s: LibrarySearch = { q: 'a "b" -c', in: "title", author: ["Smith", "Lee"], author_op: "and", tags_not: ["x"], year_min: 2018, year_max: 2024,
      added_min: "2025-01-01", has_doi: "yes", owner: "mine", cites: U, sort: "cited", view: "table", cols: ["title", "year"] };
    expect(validateLibrarySearch(parseSearch(stringifySearch(s)))).toEqual(s);
    expect(stringifySearch({ in: "anywhere", view: "list", cols: [...COLUMNS] })).toBe("");
  });
  it("drops malformed values", () => {
    expect(validateLibrarySearch(parseSearch("?year_min=abc&cites=nope&added_min=2025-13&pages_min=-1&sort=weird&foo=1"))).toEqual({});
  });
  it("converts legacy year lists", () => expect(validateLibrarySearch(parseSearch("?year=2021&year=2019"))).toEqual({ year_min: 2019, year_max: 2021 }));
  it("exclusion wins in a hand-edited URL", () => expect(validateLibrarySearch(parseSearch("?tags=ml&tags_not=ml"))).toEqual({ tags_not: ["ml"] }));
  it("api query drops view and cols", () => expect(toApiQuery({ q: "x", view: "table", cols: ["title"] })).toBe("?q=x"));
  it("drops values the backend would 422", () => {
    expect(validateLibrarySearch(parseSearch("?year_min=5000"))).toEqual({});
    expect(validateLibrarySearch(parseSearch("?cited_min=0"))).toEqual({});
  });
  it("drops both ends of an inverted range", () => {
    expect(validateLibrarySearch(parseSearch("?year_min=2024&year_max=2018&pages_min=9&pages_max=3&added_min=2025-02-01&added_max=2025-01-01"))).toEqual({});
    expect(validateLibrarySearch(parseSearch("?year_min=2018&year_max=2018"))).toEqual({ year_min: 2018, year_max: 2018 });
  });
  it("exclusion wins for has_id", () => expect(validateLibrarySearch(parseSearch("?has_id=doi&has_id=arxiv&has_id_not=doi"))).toEqual({ has_id: ["arxiv"], has_id_not: ["doi"] }));
  it("exclusion wins for has_id even when nothing is left", () => expect(validateLibrarySearch(parseSearch("?has_id=doi&has_id_not=doi"))).toEqual({ has_id_not: ["doi"] }));
  it("exclusion wins for author too", () => expect(validateLibrarySearch(parseSearch("?author=Lee&author=Kim&author_not=Lee"))).toEqual({ author: ["Kim"], author_not: ["Lee"] }));
});
