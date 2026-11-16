import { render, waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { SafeMarkdown } from "./SafeMarkdown";

const r = async (text: string) => {
  const { container } = render(<SafeMarkdown text={text} />);
  await waitFor(() => expect(container.querySelector("[data-md-fallback]")).toBeNull());
  return container;
};

describe("SafeMarkdown", () => {
  it("never renders images (inline, reference or raw HTML), only alt text", async () => {
    const c = await r("![a cat](https://evil/x.png)\n\n![ref][r]\n\n[r]: https://evil/y\n\n<img src=x onerror=alert(1)>");
    expect(c.querySelector("img")).toBeNull();
    expect(c.textContent).toContain("a cat");
  });
  it("drops javascript:, data: and protocol-relative links but keeps their text", async () => {
    const c = await r("[x](javascript:alert(1)) [y](data:text/html,hi) [z](//evil.com)");
    expect(c.querySelector("a")).toBeNull();
    expect(c.textContent).toContain("x");
  });
  it("keeps safe links: external in a new tab with nofollow, same-site plain", async () => {
    const [ext, local] = [...(await r("[a](https://example.org/p) [b](/d/abc)")).querySelectorAll("a")];
    expect(ext.getAttribute("href")).toBe("https://example.org/p");
    expect(ext.getAttribute("target")).toBe("_blank");
    expect(ext.getAttribute("rel")).toBe("noopener noreferrer nofollow");
    expect(local.getAttribute("href")).toBe(`${location.origin}/d/abc`);
    expect(local.getAttribute("target")).toBeNull();
  });
  it("renders GFM (tables wrapped) but not raw HTML or math", async () => {
    const c = await r("| a | b |\n|---|---|\n| 1 | 2 |\n\n<script>alert(1)</script><b>bold</b> $x^2$");
    expect(c.querySelector(".tbl table")).not.toBeNull();
    expect(c.querySelector("script,b,.katex")).toBeNull();
  });
});

describe("SafeMarkdown lazy loading", () => {
  it("shows the text immediately, then the rendered markdown", async () => {
    const { screen: s } = await import("@testing-library/react");
    render(<SafeMarkdown text="hello **bold**" />);
    expect(s.getByText(/hello/)).toBeInTheDocument();
    expect(await s.findByText("bold", { selector: "strong" })).toBeInTheDocument();
  });
});
