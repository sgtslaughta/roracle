import { http, HttpResponse } from "msw";
import { API_BASE } from "@/api/client";
import type { NeedsTitleItem } from "@/api/types";
import { state } from "./state";
import { labelOf } from "./uploads";

const url = (p: string) => `${API_BASE}${p}`;

export const adminHandlers = [
  http.put(url("/documents/:id/versions/:vid/title"), async ({ params, request }) => {
    const u = state.uploads.find((x) => x.version_id === params.vid);
    if (!u) return HttpResponse.json({ detail: "not found" }, { status: 404 });
    if (u.stage !== "needs_title") return HttpResponse.json({ detail: "not awaiting a title" }, { status: 409 });
    const title = String(((await request.json()) as { title?: string }).title ?? "").trim();
    if (!title) return HttpResponse.json({ detail: "title required" }, { status: 422 });
    u.title = title.slice(0, 500); u.stage = "parsed"; u.stage_label = labelOf("parsed"); u.status = "running";
    return HttpResponse.json({ version_id: u.version_id, document_id: u.document_id, stage: u.stage });
  }),
  http.get(url("/admin/needs-title"), () => {
    if (!state.admin) return HttpResponse.json({ detail: "admin only" }, { status: 403 });
    const rows = state.uploads.filter((u) => u.stage === "needs_title")
      .map((u): NeedsTitleItem => ({ document_id: u.document_id, version_id: u.version_id, filename: u.filename, owner: "researcher", uploaded_at: u.uploaded_at }))
      .sort((a, b) => a.uploaded_at.localeCompare(b.uploaded_at));
    return HttpResponse.json(rows);
  }),
];
