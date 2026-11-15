import { useRef, useState } from "react";
import { describe, expect, it } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import type { Citation } from "@/api/chat";
import { AnswerText } from "./AnswerText";
import { Sidenotes } from "./Sidenotes";
import { citeNumbers } from "./citeMarkers";
import { getReaderState } from "@/features/reader/useReaderStore";

const ok = (n: number, extra: object = {}): Citation => ({
  n, doc_id: `d${n}`, version_id: `v${n}`, version: 2, chunk_id: `c${n}`, title: `Doc ${n}`, page: 4, bbox: null, quote: "q ".repeat(120), ...extra,
});
const cites = [ok(1), ok(2), ok(3)];
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
    expect(container.querySelector(".caret")).toBeTruthy();
  });
  it("click opens the doc", () => {
    r("x [1]");
    fireEvent.click(screen.getByRole("button", { name: "Source 1: Doc 1, page 4" }));
    const t = getReaderState().tabs.find((x) => x.docId === "d1");
    expect(t).toMatchObject({ page: 4, chunkId: "c1", version: 2 });
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
      <AnswerText text="a [1] b [2]" citations={citations} hot={hot} onHot={setHot} />
      <Sidenotes citations={citations} order={citeNumbers("a [1] b [2]")} answerRef={ref} hot={hot} onHot={setHot} />
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
  it("marker hover marks the matching note hot", () => {
    const { container } = render(<Harness citations={cites} />);
    fireEvent.mouseEnter(container.querySelector("button.c[data-n='2']")!);
    expect(container.querySelector(".note[data-n='2']")!.classList.contains("hot")).toBe(true);
    expect(container.querySelector(".note[data-n='1']")!.classList.contains("hot")).toBe(false);
  });
});

describe("citeNumbers", () => {
  it("is ordered and unique", () => {
    expect(citeNumbers("[3] x [1,3] [2][1]")).toEqual([3, 1, 2]);
  });
});
