import { describe, expect, it } from "vitest";
import { fmtDate, fmtDateTime } from "./dates";

describe("dates", () => {
  it("formats consistently and handles empty", () => {
    expect(fmtDate("2026-11-15T09:30:00Z")).toMatch(/2026/);
    expect(fmtDate("2026-11-15T09:30:00Z")).not.toMatch(/:/);
    expect(fmtDateTime("2026-11-15T09:30:00Z")).toMatch(/:/);
    expect(fmtDate(null)).toBe("Never");
    expect(fmtDate(undefined, "")).toBe("");
    expect(fmtDateTime(null, "-")).toBe("-");
  });
});
