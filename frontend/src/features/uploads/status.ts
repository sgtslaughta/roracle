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
export const VIS_ORDER: Visibility[] = ["everyone", "org_unit", "private", "shared"];
export const VIS_LABEL: Record<Visibility, string> = { everyone: "Everyone", org_unit: "My org unit", private: "Only me", shared: "Shared" };
export const MAX_UPLOAD_BYTES = 500 * 1024 * 1024;


/** Known ingest failure reasons, in plain words; anything else gets a capital and a full stop. */
const FAILURES: [RegExp, string][] = [
  [/^no text extracted$/i, "No text could be read from this file. For a scan or photo, try a sharper copy."],
  [/^document could not be converted/i, "This file couldn't be read. It may be damaged, or its type isn't supported."],
  [/^invalid (json|tsv)$/i, "This file isn't valid $1."],
  [/^archive (was not unpacked|could not be read)/i, "This archive couldn't be unpacked. Check that it isn't damaged."],
  [/^(file exceeds the malware scanner|converted PDF exceeds)/i, "This file is too large to process."],
  [/^ingest crashed repeatedly$/i, "Processing kept failing. Try uploading the file again."],
];
export function failureText(raw: string | null | undefined): string {
  const t = (raw ?? "").trim();
  if (!t) return "Processing failed.";
  for (const [re, text] of FAILURES) {
    const m = t.match(re);
    if (m) return text.replace("$1", (m[1] ?? "").toUpperCase());
  }
  const s = t[0].toUpperCase() + t.slice(1);
  return /[.!?]$/.test(s) ? s : `${s}.`;
}
