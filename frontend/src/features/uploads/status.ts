import type { Stage, UploadItem, Visibility } from "@/api/types";

export const STAGE_LABELS = ["Scanned", "Parsed", "Chunked", "Embedded", "Searchable"] as const;
export type ViewStatus = "running" | "review" | "title" | "failed" | "done";
export type FilterKey = ViewStatus | "all" | "needs";

/** Number of completed user-facing steps. summarized/deduped report as Parsed. */
export function pipelineDone(stage: Stage): number {
  switch (stage) {
    case "uploaded": case "cancelled": return 0;
    case "scanned": case "rejected": return 1;
    case "parsed": case "summarized": case "deduped": case "review": case "needs_title": return 2;
    case "chunked": return 3;
    case "embedded": return 4;
    case "searchable": return 5;
  }
}

export function viewStatus(u: Pick<UploadItem, "stage" | "status">): ViewStatus {
  if (u.stage === "review") return "review";
  if (u.stage === "needs_title") return "title";
  if (u.stage === "rejected" || u.stage === "cancelled" || u.status === "failed") return "failed";
  if (u.stage === "searchable" || u.status === "done") return "done";
  return "running";
}

export const PILL: Record<ViewStatus, string> = { done: "Searchable", running: "Processing", failed: "Failed", review: "Needs your decision", title: "Needs a title" };
export const FILTERS: [FilterKey, string][] = [["all", "All"], ["running", "Processing"], ["needs", "Needs you"], ["failed", "Failed"], ["done", "Searchable"]];
/** "Needs you" covers both a near-duplicate decision and a missing title. */
export const inFilter = (u: Pick<UploadItem, "stage" | "status">, k: FilterKey) => {
  const v = viewStatus(u);
  return k === "all" || (k === "needs" ? v === "review" || v === "title" : v === k);
};
export const VIS_LABEL: Record<Visibility, string> = { everyone: "Everyone", org_unit: "My org unit", private: "Only me", shared: "Shared" };
export const MAX_UPLOAD_BYTES = 500 * 1024 * 1024;

