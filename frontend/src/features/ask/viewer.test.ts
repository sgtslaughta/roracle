import { describe, expect, it } from "vitest";
import type { ReaderTab } from "@/features/reader/useReaderStore";
import { buildViewer } from "./viewer";

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
