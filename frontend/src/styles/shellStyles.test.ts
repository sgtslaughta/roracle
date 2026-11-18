import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const shell = readFileSync("src/styles/shell.css", "utf8");
const narrow = shell.slice(shell.indexOf("@media (max-width: 1100px)"), shell.indexOf("@media (max-width: 640px)"));

describe("shell styles", () => {
  it("at tablet widths only an open reader takes the main column", () => {
    // `data-focus` is always set (default "split"), so a rule on it alone collapsed main on every page
    const rules = [...narrow.matchAll(/([^{}]+)\{[^}]*grid-template-columns:\s*[^;]*\b0 minmax\(0, 1fr\)/g)].map((m) => m[1].trim());
    expect(rules.length).toBeGreaterThan(0);
    for (const sel of rules) for (const part of sel.split(",")) expect(part).toMatch(/\.app\.reading/);
  });
});

describe("ask styles", () => {
  it("the citation margin stacks by the view's width, so an open reader cannot squeeze the answer", () => {
    const ask = readFileSync("src/styles/ask.css", "utf8");
    expect(ask).toMatch(/@container \(max-width:\s*\d+px\)\s*\{[^@]*\.answer\{grid-template-columns:minmax\(0,1fr\)/);
    expect(ask).not.toMatch(/@media \(max-width:1100px\)\{\s*\.answer/);
  });
});

describe("draft preview styles", () => {
  it("preview headings are styled (preflight would make them body text)", () => {
    const css = readFileSync("src/styles/drafts.css", "utf8");
    expect(css).toMatch(/\.dpreview h2\s*\{[^}]*font-size/);
  });
});
