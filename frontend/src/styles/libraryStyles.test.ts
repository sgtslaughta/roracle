import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const read = (f: string) => readFileSync(`src/styles/${f}`, "utf8");
const defined = new Set(["tokens.css", "base.css", "globals.css"].flatMap((f) => [...read(f).matchAll(/(--[\w-]+)\s*:/g)].map((m) => m[1])));

describe("library styles", () => {
  it("library search colours come from defined tokens", () => {
    const used = [...read("library-search.css").matchAll(/var\((--[\w-]+)/g)].map((m) => m[1]);
    expect(used.filter((t) => !defined.has(t))).toEqual([]);
  });

  it("a hidden results list stays hidden despite display: flex", () => {
    expect(read("library.css")).toMatch(/\.results\[hidden\]\s*\{\s*display:\s*none/);
  });
});
