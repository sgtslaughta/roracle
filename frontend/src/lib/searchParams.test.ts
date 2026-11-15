import { describe, expect, it } from "vitest";
import { parseSearch, stringifySearch } from "./searchParams";

describe("search params", () => {
  it("round-trips repeated keys and numbers", () => {
    const s = { q: "spectral gap", tags: ["a", "b"], year: ["2021"], sort: "newest", page: 2 };
    const url = stringifySearch(s);
    expect(url).toBe("?q=spectral+gap&tags=a&tags=b&year=2021&sort=newest&page=2");
    expect(parseSearch(url)).toEqual(s);
  });
  it("omits empty values and page 1", () => {
    expect(stringifySearch({ q: "", tags: [], page: 1 })).toBe("");
  });
  it("parses single array values as arrays", () => {
    expect(parseSearch("?collection=Thesis")).toEqual({ collection: ["Thesis"] });
  });
});
