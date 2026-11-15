import { beforeEach, describe, expect, it, vi } from "vitest";
import { cycleTheme, getTheme, setTheme } from "./theme";

beforeEach(() => {
  localStorage.clear();
  document.documentElement.removeAttribute("data-theme");
});

describe("theme", () => {
  it("defaults to system", () => expect(getTheme()).toBe("system"));

  it("cycles system -> light -> dark -> system and persists", () => {
    expect(cycleTheme()).toBe("light");
    expect(document.documentElement.getAttribute("data-theme")).toBe("light");
    expect(localStorage.getItem("roracle-theme")).toBe("light");
    expect(cycleTheme()).toBe("dark");
    expect(cycleTheme()).toBe("system");
    expect(document.documentElement.hasAttribute("data-theme")).toBe(false);
  });

  it("falls back to system when storage throws", () => {
    const get = vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => { throw new Error("blocked"); });
    const set = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new Error("blocked"); });
    expect(getTheme()).toBe("system");
    expect(() => setTheme("dark")).not.toThrow();
    expect(document.documentElement.getAttribute("data-theme")).toBe("dark");
    get.mockRestore(); set.mockRestore();
  });

  it("ignores garbage values", () => {
    localStorage.setItem("roracle-theme", "purple");
    expect(getTheme()).toBe("system");
  });
});

describe("theme store", () => {
  it("notifies subscribers so the top bar follows palette changes", async () => {
    const { subscribeTheme } = await import("./theme");
    const seen: string[] = [];
    const off = subscribeTheme(() => seen.push(getTheme()));
    cycleTheme(); cycleTheme();
    off(); cycleTheme();
    expect(seen).toEqual(["light", "dark"]);
  });
});
