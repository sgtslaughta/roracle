import { describe, expect, it } from "vitest";
import { joinQuery, splitQuery } from "./queryBuilder";
import { searchTerms } from "./highlight";

describe("splitQuery (mirrors backend parse_query)", () => {
  it.each([
    ['graph "neural network" -survey', { words: "graph", phrases: ["neural network"], excluded: ["survey"] }],
    ['-"related work" transformers', { words: "transformers", phrases: [], excluded: ["related work"] }],
    ['state-of-the-art "unclosed', { words: 'state-of-the-art "unclosed', phrases: [], excluded: [] }],
    // backend-equivalent edge cases
    ["a - b", { words: "a - b", phrases: [], excluded: [] }],
    ['-foo"bar baz', { words: '-foo"bar baz', phrases: [], excluded: [] }],
    ['"a" "b" "c', { words: '"c', phrases: ["a", "b"], excluded: [] }],
    ['x "  spaced   out " -"" y', { words: "x y", phrases: ["spaced out"], excluded: [] }],
    ["-a -b c", { words: "c", phrases: [], excluded: ["a", "b"] }],
  ])("splits %s", (q, p) => expect(splitQuery(q)).toEqual(p));

  it("joins back", () =>
    expect(joinQuery({ words: "graph", phrases: ["neural network"], excluded: ["related work", "x"] })).toBe('graph "neural network" -"related work" -x'));
  it("trims and collapses spaces", () =>
    expect(joinQuery({ words: "  a   b ", phrases: [" p  q "], excluded: [""] })).toBe('a b "p q"'));
});

describe("searchTerms", () => {
  it("ignores exclusions and quotes", () => expect(searchTerms('graph "neural network" -survey')).toEqual(["graph", "neural", "network"]));
});

describe("round trip", () => {
  it.each([
    'graph "neural network" -survey', '-"related work" transformers', 'state-of-the-art "unclosed', '"a" "b" "c', 'x"y "p q" z"w',
    '"c d"e', 'a - b', '-foo"bar "baz" -"q r"', '--x y', '"" -"" z',
  ])("keeps meaning of %s", (q) => {
    const p = splitQuery(q);
    expect(splitQuery(joinQuery(p))).toEqual(p);
  });
  it("strips quotes from phrase entries", () =>
    expect(splitQuery(joinQuery({ words: "a", phrases: ['x "y" z'], excluded: [] }))).toEqual({ words: "a", phrases: ["x y z"], excluded: [] }));
});
