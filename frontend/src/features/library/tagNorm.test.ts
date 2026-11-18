import { describe, expect, it } from "vitest";
import { normTag, tagError } from "./api";

describe("tag normalisation mirrors the server's normalize_tag", () => {
  it("keeps non-Latin letters and accents", () => {
    expect(normTag("日本語")).toBe("日本語");
    expect(tagError("日本語")).toBeNull();
    expect(normTag("Café")).toBe("café");
    expect(normTag("ｆｕｌｌ　width")).toBe("full-width"); // NFKC
    expect(normTag("  Machine_Learning!! ")).toBe("machine-learning");
  });

  it("rejects instead of truncating", () => {
    expect(normTag("x".repeat(45))).toHaveLength(45);
    expect(tagError("x".repeat(45))).toMatch(/at most 40/);
    expect(tagError("x".repeat(40))).toBeNull();
    expect(tagError(" -- ")).toMatch(/letter or digit/);
  });
});
