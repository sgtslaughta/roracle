import { describe, expect, it } from "vitest";
import { parseTopic, slugify } from "./parse";

describe("slugify", () => {
  it("matches python-markdown toc", () => {
    expect(slugify("401 Unauthorized")).toBe("401-unauthorized");
    expect(slugify("What's new?  Café - tips")).toBe("whats-new-cafe-tips");
  });
});

describe("parseTopic", () => {
  it("extracts title and lede, rest is segments", () => {
    const t = parseTopic("# Chat\n\nAsk things.\nTwo lines.\n\nBody para.\n");
    expect(t.title).toBe("Chat");
    expect(t.lede).toBe("Ask things. Two lines.");
    expect(t.segments).toEqual([{ kind: "md", text: "Body para.\n" }]);
  });
  it("parses callouts with default titles", () => {
    const t = parseTopic('# T\n\nL\n\n!!! warning\n    Careful here.\n\n!!! tip "Custom"\n    Nice.\n');
    expect(t.segments).toEqual([
      { kind: "callout", tone: "warning", title: "Heads up", text: "Careful here." },
      { kind: "callout", tone: "tip", title: "Custom", text: "Nice." },
    ]);
  });
  it("parses definition lists", () => {
    const t = parseTopic("# T\n\nL\n\nOne\n:   first thing\n\nTwo\n:   second thing\n");
    expect(t.segments).toEqual([{ kind: "dl", items: [{ term: "One", def: "first thing" }, { term: "Two", def: "second thing" }] }]);
  });
  it("splits tabs on ' | '", () => {
    const t = parseTopic('# T\n\nL\n\n<!-- tabs -->\n\n```bash title="Claude Code | Terminal"\nrun it\n```\n\n```text title="Other"\nx\n```\n\n<!-- /tabs -->\n');
    expect(t.segments).toEqual([{ kind: "tabs", tabs: [
      { name: "Claude Code", caption: "Terminal", lang: "bash", code: "run it" },
      { name: "Other", caption: "Other", lang: "text", code: "x" },
    ] }]);
  });
  it("parses steps with ids and nested segments", () => {
    const t = parseTopic("# T\n\nL\n\n<!-- steps -->\n\n### First one\n\nDo A.\n\n### Second\n\n!!! tip\n    Hi.\n\n<!-- /steps -->\n");
    const s = t.segments[0];
    if (s.kind !== "steps") throw new Error("steps");
    expect(s.steps.map((x) => x.id)).toEqual(["first-one", "second"]);
    expect(s.steps[0].body).toEqual([{ kind: "md", text: "Do A.\n" }]);
    expect(s.steps[1].body[0].kind).toBe("callout");
  });
  it("parses try attributes", () => {
    const t = parseTopic(`# T\n\nL\n\n<!-- try:search label="Run it" hint="Opens search." q='"a b" -c' -->\n`);
    expect(t.segments).toEqual([{ kind: "try", action: "search", label: "Run it", hint: "Opens search.", q: '"a b" -c' }]);
  });
  it("ignores markers inside fences", () => {
    const t = parseTopic("# T\n\nL\n\n```md\n!!! tip\n    x\n<!-- steps -->\n## no\n```\n");
    expect(t.segments).toHaveLength(1);
    expect(t.segments[0].kind).toBe("md");
    expect(t.sections).toEqual([]);
  });
  it("lists h2/h3 sections with slugs", () => {
    const t = parseTopic("# T\n\nL\n\n## Errors\n\n### 401 Unauthorized\n\ntext\n");
    expect(t.sections).toEqual([{ id: "errors", title: "Errors", depth: 2 }, { id: "401-unauthorized", title: "401 Unauthorized", depth: 3 }]);
  });
});
