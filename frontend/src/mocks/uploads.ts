import type { Stage, UploadItem, Visibility } from "@/api/types";

export type MockUpload = UploadItem;

const SEQ: Stage[] = ["uploaded", "scanned", "parsed", "chunked", "embedded", "searchable"];

const LABEL: Record<Stage, string> = {
  uploaded: "Uploaded", scanned: "Scanned", parsed: "Parsed", summarized: "Parsed", deduped: "Parsed", chunked: "Chunked",
  embedded: "Embedded", searchable: "Searchable", review: "Needs your decision", needs_title: "Needs a title", rejected: "Rejected", cancelled: "Cancelled",
};
export const labelOf = (s: Stage) => LABEL[s];

const base = { size: 1_000_000, visibility: "everyone" as Visibility, status: "done" as const, error: null, page_count: null, near_dup_of: null };
const mk = (o: Partial<MockUpload> & Pick<MockUpload, "document_id" | "version_id" | "filename" | "stage" | "uploaded_at">): MockUpload =>
  ({ ...base, title: null, stage_label: LABEL[o.stage], ...o });

export const seedUploads = (): MockUpload[] => [
  mk({ document_id: "u-1", version_id: "uv-1", title: "spectral-gap-v3", filename: "spectral-gap-v3.pdf", size: 2_516_582, uploaded_at: "2026-11-15T08:12:00Z", stage: "searchable", page_count: 14 }),
  mk({ document_id: "u-2", version_id: "uv-2", title: "hypergraph-cheeger-draft", filename: "hypergraph-cheeger-draft.docx", size: 1_153_434, visibility: "org_unit", status: "running", uploaded_at: "2026-11-15T09:30:00Z", stage: "chunked" }),
  mk({ document_id: "u-3", version_id: "uv-3", title: "scan_1998_lecture-notes", filename: "scan_1998_lecture-notes.pdf", size: 192_937_984, visibility: "private", status: "failed", error: "Text recognition timed out on page 212 of 340. Retry processes the remaining pages only.", uploaded_at: "2026-11-14T16:45:00Z", stage: "scanned" }),
  mk({ document_id: "u-4", version_id: "uv-4", title: "chen-aldana-preprint", filename: "chen-aldana-preprint.pdf", size: 2_306_867, uploaded_at: "2026-11-15T09:02:00Z", stage: "review", near_dup_of: { document_id: "00000000-0000-4000-8000-000000000001", title: "Lower bounds on the spectral gap of random regular graphs" } }),
  mk({ document_id: "u-5", version_id: "uv-5", filename: "untitled-scan-0412.pdf", size: 880_000, status: "running", uploaded_at: "2026-11-15T09:45:00Z", stage: "needs_title" }),
];

/** One poll = one pipeline step for every running item (keeps the demo moving and tests deterministic). */
export function advance(items: MockUpload[]) {
  for (const u of items) {
    if ((u.status !== "running" && u.status !== "queued") || u.stage === "needs_title") continue;
    const i = SEQ.indexOf(u.stage);
    u.stage = SEQ[Math.min(SEQ.length - 1, i + 1)];
    u.status = u.stage === "searchable" ? "done" : "running";
    u.stage_label = LABEL[u.stage];
  }
}

export const newUpload = (file: { name: string; size: number }, visibility: Visibility): MockUpload => {
  const id = Math.random().toString(36).slice(2, 8);
  return mk({ document_id: `u-${id}`, version_id: `uv-${id}`, title: file.name.replace(/\.[^.]+$/, ""), filename: file.name, size: file.size, visibility, status: "running", uploaded_at: new Date().toISOString(), stage: "uploaded" });
};
