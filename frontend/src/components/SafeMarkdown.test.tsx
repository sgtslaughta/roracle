import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { SafeMarkdown } from "./SafeMarkdown";

const r = (text: string) => render(<SafeMarkdown text={text} />).container;

describe("SafeMarkdown", () => {
  it("never renders images (inline, reference or raw HTML), only alt text", () => {
    const c = r("![a cat](https://evil/x.png)\n\n![ref][r]\n\n[r]: https://evil/y\n\n<img src=x onerror=alert(1)>");
    expect(c.querySelector("img")).toBeNull();
    expect(c.textContent).toContain("a cat");
  });
  it("drops javascript:, data: and protocol-relative links but keeps their text", () => {
    const c = r("[x](javascript:alert(1)) [y](data:text/html,hi) [z](//evil.com)");
    expect(c.querySelector("a")).toBeNull();
    expect(c.textContent).toContain("x");
  });
  it("keeps safe links: external in a new tab with nofollow, same-site plain", () => {
    const [ext, local] = [...r("[a](https://example.org/p) [b](/d/abc)").querySelectorAll("a")];
    expect(ext.getAttribute("href")).toBe("https://example.org/p");
    expect(ext.getAttribute("target")).toBe("_blank");
    expect(ext.getAttribute("rel")).toBe("noopener noreferrer nofollow");
    expect(local.getAttribute("href")).toBe(`${location.origin}/d/abc`);
    expect(local.getAttribute("target")).toBeNull();
  });
  it("renders GFM (tables wrapped) but not raw HTML or math", () => {
    const c = r("| a | b |\n|---|---|\n| 1 | 2 |\n\n<script>alert(1)</script><b>bold</b> $x^2$");
    expect(c.querySelector(".tbl table")).not.toBeNull();
    expect(c.querySelector("script,b,.katex")).toBeNull();
  });
});
