import { beforeEach, describe, expect, it } from "vitest";
import { createElement, type ReactNode } from "react";
import { renderHook } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { DocumentDetail } from "@/api/types";
import { openDoc, resetReaderStore } from "@/features/reader/useReaderStore";
import type { ReaderTab } from "@/features/reader/useReaderStore";
import { buildViewer, useViewer } from "./viewer";

const tab = (docId: string, page = 1, version?: number, seq = 1): ReaderTab => ({ docId, page, version, seq });
const ids = (d: string, v?: number) => (d === "unk" ? null : `${d}-v${v ?? 9}`);

describe("buildViewer", () => {
  it("caps at 3 tabs with the active tab first", () => {
    const tabs = ["a", "b", "c", "d"].map((d) => tab(d));
    const v = buildViewer({ tabs, active: "c" }, [], ids);
    expect(v.tabs.map((t) => t.doc_id)).toEqual(["c", "a", "b"]);
  });
  it("lists ignored docs only in ignored", () => {
    const v = buildViewer({ tabs: [tab("a"), tab("b", 4)], active: "a" }, ["b"], ids);
    expect(v.tabs.map((t) => t.doc_id)).toEqual(["a"]);
    expect(v.ignored).toEqual(["b"]);
  });
  it("drops ignores for closed tabs (no chip shown, so no hidden corpus exclusion)", () => {
    const v = buildViewer({ tabs: [tab("a")], active: "a" }, ["gone", "a"], ids);
    expect(v.ignored).toEqual(["a"]);
    expect(buildViewer({ tabs: [], active: null }, ["gone"], ids).ignored).toEqual([]);
  });
  it("maps latest, explicit and unknown versions", () => {
    const v = buildViewer({ tabs: [tab("a", 2), tab("b", 1, 3), tab("unk", 5)], active: "a" }, [], ids);
    expect(v.tabs).toEqual([
      { doc_id: "a", version_id: "a-v9", page: 2 },
      { doc_id: "b", version_id: "b-v3", page: 1 },
      { doc_id: "unk", version_id: null, page: 5 },
    ]);
  });
  it("passes the selection through, default null", () => {
    expect(buildViewer({ tabs: [], active: null }, [], ids).selection).toBeNull();
    const sel = { doc_id: "a", page: 1, text: "x" };
    expect(buildViewer({ tabs: [], active: null }, [], ids, sel).selection).toEqual(sel);
  });
});

describe("useViewer", () => {
  beforeEach(resetReaderStore);
  it("getViewer resolves version ids from a cache filled after first render", () => {
    const qc = new QueryClient();
    const wrapper = ({ children }: { children: ReactNode }) => createElement(QueryClientProvider, { client: qc }, children);
    const { result } = renderHook(() => useViewer(), { wrapper });
    openDoc("d1", { page: 2 });
    expect(result.current().tabs[0]).toEqual({ doc_id: "d1", version_id: null, page: 2 });
    qc.setQueryData(["doc", "d1"], { versions: [{ id: "v-old", version: 1, is_latest: false }, { id: "v-new", version: 2, is_latest: true }] } as unknown as DocumentDetail);
    expect(result.current().tabs[0].version_id).toBe("v-new");
  });
});
