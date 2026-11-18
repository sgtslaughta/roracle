import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { HelpBody } from "./HelpBody";

const md = (s: string, extra: Partial<Parameters<typeof HelpBody>[0]> = {}) => {
  const onGo = vi.fn(), onTry = vi.fn();
  const r = render(<HelpBody md={s} file="user/chat.md" onGo={onGo} onTry={onTry} {...extra} />);
  return { ...r, onGo, onTry };
};
const ready = (c: HTMLElement) => waitFor(() => expect(c.querySelector("[data-md-fallback]")).toBeNull());

describe("HelpBody", () => {
  const writeText = vi.fn().mockResolvedValue(undefined);
  beforeEach(() => { writeText.mockClear(); Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true }); });

  it("renders h1, lede, headings with ids and a screenshot figure", async () => {
    const { container } = md("# Chat\n\nAsk things.\n\n## Sources\n\n![Answer with markers](../assets/screens/ask-answer.png)\n\n![Missing](../assets/screens/nope.png)\n");
    await ready(container);
    expect(screen.getByRole("heading", { level: 1 }).textContent).toBe("Chat");
    expect(container.querySelector(".lede")?.textContent).toBe("Ask things.");
    expect(container.querySelector("h2")?.id).toBe("sources");
    const img = container.querySelector("figure.shot img") as HTMLImageElement;
    expect(img.getAttribute("src")).toBeTruthy();
    expect(container.querySelector("figure.shot figcaption")?.textContent).toBe("Answer with markers");
    expect(container.querySelectorAll("img")).toHaveLength(1);
    expect(container.textContent).toContain("Missing");
  });

  it("copies a titled snippet with the origin substituted", async () => {
    const { container } = md('# T\n\nL\n\n```text title="Link"\nhttps://roracle.example.internal/mcp\n```\n');
    await ready(container);
    expect(container.querySelector(".snip figcaption")?.textContent).toContain("Link");
    fireEvent.click(screen.getByRole("button", { name: "Copy" }));
    expect(writeText).toHaveBeenCalledWith(`${location.origin}/mcp`);
  });

  it("links to other topics via onGo and to the docs site otherwise", async () => {
    const { container, onGo } = md("# T\n\nL\n\nSee [labels](labels.md#x), [mcp](../reference/mcp.md) and [out](https://example.org).\n");
    await ready(container);
    fireEvent.click(screen.getByText("labels"));
    expect(onGo).toHaveBeenCalledWith("labels", "x");
    const a = screen.getByText("mcp") as HTMLAnchorElement;
    expect(a.getAttribute("href")).toBe("/docs/reference/mcp/");
    expect(a.getAttribute("target")).toBe("_blank");
    expect(screen.getByText("out").getAttribute("rel")).toContain("noopener");
  });

  it("resolves links against the topic's directory", async () => {
    const { container, onGo } = md("# T\n\nL\n\n[admin](../admin/labels.md)\n", { file: "user/chat.md" });
    await ready(container);
    fireEvent.click(screen.getByText("admin"));
    expect(onGo).toHaveBeenCalledWith("admin-labels", undefined);
  });

  it("renders callouts, dl, steps, tabs and try buttons", async () => {
    const { container, onTry } = md([
      "# T", "", "L", "", '!!! warning "Careful"', "    Mind **this**.", "", "Term", ":   Meaning", "",
      "<!-- steps -->", "", "### One", "", "Do it.", "", "### Two", "", "<!-- tabs -->", "",
      '```bash title="A | cap a"', "a-code", "```", "", '```bash title="B | cap b"', "b-code", "```", "", "<!-- /tabs -->", "", "<!-- /steps -->", "",
      '<!-- try:search label="Run it" q=\'"a b"\' -->', "",
    ].join("\n"));
    await ready(container);
    expect(container.querySelector(".call.warn")?.textContent).toContain("Careful");
    expect(container.querySelector(".dl dt")?.textContent).toBe("Term");
    expect(container.querySelectorAll("ol.walk > li")).toHaveLength(2);
    expect(container.querySelector("#one")).not.toBeNull();
    expect(screen.getByRole("tab", { name: "A" }).getAttribute("aria-selected")).toBe("true");
    fireEvent.click(screen.getByRole("tab", { name: "B" }));
    expect(container.querySelector(".snip pre")?.textContent).toBe("b-code");
    expect(container.querySelector(".snip figcaption")?.textContent).toContain("cap b");
    fireEvent.click(screen.getByRole("button", { name: "Run it" }));
    expect(onTry).toHaveBeenCalledWith({ kind: "try", action: "search", label: "Run it", q: '"a b"' });
  });
});
