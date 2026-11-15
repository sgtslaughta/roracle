import { useRef, useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import type { Citation } from "@/api/chat";
import { AnswerText } from "./AnswerText";
import { Sidenotes, trimQuote } from "./Sidenotes";
import { citeNumbers } from "./citeMarkers";
import { getReaderState } from "@/features/reader/useReaderStore";

const ok = (n: number, extra: object = {}): Citation => ({
  n, doc_id: `d${n}`, version_id: `v${n}`, version: 2, chunk_id: `c${n}`, title: `Doc ${n}`, page: 4, bbox: null, quote: "q ".repeat(120), ...extra,
});
const cites = [ok(1), ok(2), ok(3)];
afterEach(() => { vi.restoreAllMocks(); });
const r = (text: string, citations: Citation[] = cites, streaming = false) =>
  render(<AnswerText text={text} citations={citations} streaming={streaming} />);

describe("AnswerText", () => {
  it("renders math, tables and code", () => {
    const { container } = r("inline $x^2$\n\n$$\\int_0^1 f$$\n\n| a | b |\n|---|---|\n| 1 | 2 |\n\n```\ncode\n```");
    expect(container.querySelectorAll(".katex").length).toBeGreaterThanOrEqual(2);
    expect(container.querySelector("table")).toBeTruthy();
    expect(container.querySelector("pre code")).toBeTruthy();
  });
  it("drops raw html", () => {
    const { container } = r("<script>alert(1)</script> hi <img src=x onerror=alert(1)>");
    expect(container.querySelector("script,img")).toBeNull();
  });
  it("renders [2,3] and [2][3] as two buttons each", () => {
    const a = r("see [2,3]");
    expect(a.container.querySelectorAll("button.c")).toHaveLength(2);
    a.unmount();
    const b = r("see [2][3]");
    expect(b.container.querySelectorAll("button.c")).toHaveLength(2);
  });
  it("unknown n is plain text; code stays literal", () => {
    const { container } = r("x [9] and `[1]`");
    expect(container.textContent).toContain("[9]");
    expect(container.querySelector("button.c")).toBeNull();
    expect(container.querySelector("code")?.textContent).toBe("[1]");
  });
  it("streaming without citations shows inert markers and a caret", () => {
    const { container } = r("x [1]", [], true);
    expect(container.querySelector("sup.c.pending")).toBeTruthy();
    expect(container.querySelector(".ans-text.streaming")).toBeTruthy();
    expect(container.querySelector("sup.c.pending")!.getAttribute("aria-label")).toBe("Source 1");
  });
  it("click opens the doc", () => {
    r("x [1]");
    fireEvent.click(screen.getByRole("button", { name: "Source 1: Doc 1, page 4" }));
    const t = getReaderState().tabs.find((x) => x.docId === "d1");
    expect(t).toMatchObject({ page: 4, chunkId: "c1", version: 2 });
  });
  it("never renders images, shows alt as text", () => {
    const { container } = r("![x](https://evil/x?q=secret)");
    expect(container.querySelector("img")).toBeNull();
    expect(container.textContent).toContain("x");
  });
  it("blocks unsafe link schemes", () => {
    const { container } = r("[x](javascript:alert(1)) [y](data:text/html,hi) [z](//evil.com)");
    expect(container.querySelector("a")).toBeNull();
    expect(container.textContent).toContain("x");
  });
  it("rejects a backslash host trick and keeps external hrefs normalised", () => {
    const { container } = r("[a](/\\evil.com) [b](https://example.org/p?q=1#h)");
    const links = [...container.querySelectorAll("a")];
    const evil = links.find((a) => a.textContent === "a");
    if (evil) { // never the raw backslash form: either encoded same-site, or external with noopener
      const href = evil.getAttribute("href")!;
      expect(href).not.toContain("\\");
      const external = new URL(href, location.origin).origin !== location.origin;
      expect(external ? evil.getAttribute("rel") : "noopener noreferrer").toBe("noopener noreferrer");
    }
    expect(links.find((a) => a.textContent === "b")!.getAttribute("href")).toBe("https://example.org/p?q=1#h");
  });
  it("renders no image for reference-style images or KaTeX \\href", () => {
    const a = r("![a][r]\n\n[r]: https://evil/x");
    expect(a.container.querySelector("img")).toBeNull();
    a.unmount();
    const b = r("$\\href{https://evil}{x}$");
    expect(b.container.querySelector("a,img")).toBeNull();
  });
  it("external links are noopener, same-origin plain", () => {
    const { container } = r("[x](https://example.org) [y](/d/abc)");
    const [a, b] = [...container.querySelectorAll("a")];
    expect(a.getAttribute("rel")).toBe("noopener noreferrer");
    expect(a.getAttribute("target")).toBe("_blank");
    expect(b.getAttribute("href")).toBe("/d/abc");
    expect(b.getAttribute("target")).toBeNull();
  });
  it("preserves surrounding text; markers in links and math stay literal", () => {
    const { container } = r("a [1] b [t [2]](/x) $[3]$");
    expect(container.textContent).toContain("a 1 b");
    expect(container.querySelectorAll("button.c")).toHaveLength(1);
    expect(container.querySelector("a")!.textContent).toBe("t [2]");
  });
  it("unavailable marker is not a button", () => {
    const { container } = r("x [1]", [{ n: 1, available: false }]);
    expect(container.querySelector("button")).toBeNull();
    expect(container.querySelector("sup.c.gone")).toBeTruthy();
  });
});

function Harness({ citations }: { citations: Citation[] }) {
  const ref = useRef<HTMLElement>(null);
  const [hot, setHot] = useState<number | null>(null);
  return (
    <article ref={ref}>
      <AnswerText text="a [1] b [2] c [3]" citations={citations} hot={hot} onHot={setHot} />
      <Sidenotes citations={citations} order={citeNumbers("a [1] b [2] c [3]")} answerRef={ref} hot={hot} onHot={setHot} />
    </article>
  );
}

describe("Sidenotes", () => {
  it("shows unavailable notes", () => {
    render(<Harness citations={[ok(1), { n: 2, available: false }]} />);
    expect(screen.getByText("Source not available")).toBeTruthy();
  });
  it("note click opens doc and trims quote", () => {
    const { container } = render(<Harness citations={cites} />);
    expect(container.querySelector(".crop")!.textContent!.length).toBeLessThanOrEqual(161);
    fireEvent.click(container.querySelector(".note[data-n='2']")!);
    expect(getReaderState().active).toBe("d2");
  });
  it("caps at 12 notes with a +1 more trigger", () => {
    const many = Array.from({ length: 13 }, (_, i) => ok(i + 1));
    const { container } = render(<Harness citations={many} />);
    expect(container.querySelectorAll(".margin .note")).toHaveLength(3); // the harness text cites 3 of them
    const { container: c2 } = render(<Sidenotes citations={many} order={many.map((c) => c.n)} answerRef={{ current: null }} />);
    expect(c2.querySelectorAll(".note")).toHaveLength(12);
    expect(screen.getByText("+1 more sources")).toBeTruthy();
  });
  it("positions notes absolutely without overlap", () => {
    const gcs = vi.spyOn(globalThis, "getComputedStyle").mockReturnValue({ position: "absolute" } as CSSStyleDeclaration);
    const oh = vi.spyOn(HTMLElement.prototype, "offsetHeight", "get").mockReturnValue(50);
    vi.spyOn(Element.prototype, "getBoundingClientRect").mockImplementation(function (this: Element) {
      const n = (this as HTMLElement).dataset?.n;
      return { top: n ? 100 + Number(n) * 5 : 0 } as DOMRect;
    });
    const { container } = render(<Harness citations={cites} />);
    const tops = [...container.querySelectorAll<HTMLElement>(".note")].map((n) => parseFloat(n.style.top));
    expect(tops).toHaveLength(3);
    expect(tops[1]).toBeGreaterThanOrEqual(tops[0] + 58);
    expect(tops[2]).toBeGreaterThanOrEqual(tops[1] + 58);
    expect(parseFloat(container.querySelector<HTMLElement>(".margin")!.style.minHeight)).toBeGreaterThan(tops[2]);
    expect(gcs).toHaveBeenCalled(); expect(oh).toHaveBeenCalled();
  });
  it("marker hover marks the matching note hot", () => {
    const { container } = render(<Harness citations={cites} />);
    fireEvent.mouseEnter(container.querySelector("button.c[data-n='2']")!);
    expect(container.querySelector(".note[data-n='2']")!.classList.contains("hot")).toBe(true);
    expect(container.querySelector(".note[data-n='1']")!.classList.contains("hot")).toBe(false);
  });
});

describe("trimQuote", () => {
  it("only trims past 160 and cuts on a word boundary", () => {
    expect(trimQuote("a".repeat(160))).toBe("a".repeat(160));
    expect(trimQuote("word ".repeat(40))).toMatch(/^(word ){31}word…$/);
  });
});

describe("citeNumbers", () => {
  it("ignores code and math", () => {
    expect(citeNumbers("`[1]` ```\n[2]\n``` $[3]$ [4]")).toEqual([4]);
  });
  it("is ordered and unique", () => {
    expect(citeNumbers("[3] x [1,3] [2][1]")).toEqual([3, 1, 2]);
  });
});
