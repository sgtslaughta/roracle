import type { Stage, UploadItem, Visibility } from "@/api/types";

export interface MockUpload extends Omit<UploadItem, "status"> { status: UploadItem["status"] }

const SEQ: Stage[] = ["uploaded", "scanned", "parsed", "chunked", "embedded", "searchable"];

export const seedUploads = (): MockUpload[] => [
  { document_id: "u-1", version_id: "uv-1", title: "spectral-gap-v3", filename: "spectral-gap-v3.pdf", size: 2_516_582, visibility: "everyone", status: "done", error: null, uploaded_at: "2026-11-15T08:12:00Z", stage: "searchable", page_count: 14 },
  { document_id: "u-2", version_id: "uv-2", title: "hypergraph-cheeger-draft", filename: "hypergraph-cheeger-draft.docx", size: 1_153_434, visibility: "org_unit", status: "running", error: null, uploaded_at: "2026-11-15T09:30:00Z", stage: "chunked" },
  { document_id: "u-3", version_id: "uv-3", title: "scan_1998_lecture-notes", filename: "scan_1998_lecture-notes.pdf", size: 192_937_984, visibility: "private", status: "failed", error: "Text recognition timed out on page 212 of 340. Retry processes the remaining pages only.", uploaded_at: "2026-11-14T16:45:00Z", stage: "scanned" },
  { document_id: "u-4", version_id: "uv-4", title: "chen-aldana-preprint", filename: "chen-aldana-preprint.pdf", size: 2_306_867, visibility: "everyone", status: "done", error: null, uploaded_at: "2026-11-15T09:02:00Z", stage: "review", near_dup_of: "dv-1", near_dup: { document_id: "d1", title: "Lower bounds on the spectral gap of random regular graphs", version: 2, similarity: 0.96 } },
];

/** One poll = one pipeline step for every running item (keeps the demo moving and tests deterministic). */
export function advance(items: MockUpload[]) {
  for (const u of items) {
    if (u.status !== "running" && u.status !== "queued") continue;
    const i = SEQ.indexOf(u.stage);
    u.stage = SEQ[Math.min(SEQ.length - 1, i + 1)];
    u.status = u.stage === "searchable" ? "done" : "running";
  }
}

export const newUpload = (file: { name: string; size: number }, visibility: Visibility): MockUpload => {
  const id = Math.random().toString(36).slice(2, 8);
  return { document_id: `u-${id}`, version_id: `uv-${id}`, title: file.name.replace(/\.[^.]+$/, ""), filename: file.name, size: file.size, visibility, status: "running", error: null, uploaded_at: new Date().toISOString(), stage: "uploaded" };
};
