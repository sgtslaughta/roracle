import { afterEach, describe, expect, it, vi } from "vitest";
import { loadHistory, pushHistory } from "./promptHistory";

afterEach(() => { localStorage.clear(); vi.restoreAllMocks(); });

describe("promptHistory", () => {
  it("keeps newest last, per user, skipping empty and consecutive duplicates", () => {
    pushHistory("u1", "a"); pushHistory("u1", "  "); pushHistory("u1", ""); pushHistory("u1", "a"); pushHistory("u1", "b"); pushHistory("u1", "a");
    pushHistory("u2", "z");
    expect(loadHistory("u1")).toEqual(["a", "b", "a"]);
    expect(loadHistory("u2")).toEqual(["z"]);
    expect(localStorage.getItem("roracle.prompts.u1")).toBe('["a","b","a"]');
  });
  it("caps at 100, dropping the oldest", () => {
    for (let i = 0; i < 105; i++) pushHistory("u", `q${i}`);
    const h = loadHistory("u");
    expect(h).toHaveLength(100);
    expect([h[0], h[99]]).toEqual(["q5", "q104"]);
  });
  it("falls back to no history when storage throws or holds junk", () => {
    localStorage.setItem("roracle.prompts.u", "{not json");
    expect(loadHistory("u")).toEqual([]);
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => { throw new Error("denied"); });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new Error("denied"); });
    expect(loadHistory("u")).toEqual([]);
    expect(() => pushHistory("u", "x")).not.toThrow();
  });
});
