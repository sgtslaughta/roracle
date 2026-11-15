import { createHash } from "node:crypto";
import { execFileSync } from "node:child_process";
import { mkdtempSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const sha = (p: string) => createHash("sha256").update(readFileSync(p)).digest("hex");
const run = () => {
  const dir = mkdtempSync(join(tmpdir(), "contours-"));
  execFileSync("node", ["scripts/gen-contours.mjs", "--out", dir]);
  return dir;
};

describe("gen-contours", () => {
  it("is deterministic: same seed gives the same SHA-256, matching the committed textures", () => {
    const a = run(), b = run();
    for (const f of ["contour-main.svg", "contour-side.svg"]) {
      expect(sha(join(a, f))).toBe(sha(join(b, f)));
      expect(sha(join(a, f))).toBe(sha(join("public", f)));
    }
  });
});
