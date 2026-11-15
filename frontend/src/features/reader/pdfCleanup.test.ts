import { QueryClient } from "@tanstack/react-query";
import { describe, expect, it, vi } from "vitest";
import { attachPdfCleanup } from "./pdfCleanup";

describe("pdf cleanup", () => {
  it("destroys a PDF document when its query is removed, and ignores other queries", () => {
    const qc = new QueryClient();
    attachPdfCleanup(qc);
    const destroy = vi.fn();
    qc.setQueryData(["pdf", "a", 1], { numPages: 1, getPage: vi.fn(), destroy });
    qc.setQueryData(["doc", "a"], { destroy: vi.fn() });
    qc.removeQueries({ queryKey: ["doc"] });
    expect(destroy).not.toHaveBeenCalled();
    qc.removeQueries({ queryKey: ["pdf", "a", 1] });
    expect(destroy).toHaveBeenCalledTimes(1);
  });
});
