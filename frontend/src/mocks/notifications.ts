import { http, HttpResponse } from "msw";
import { API_BASE } from "@/api/client";
import type { AppNotification } from "@/features/notifications/describe";
import { DOCS } from "./docs";
import { state } from "./state";

const url = (p: string) => `${API_BASE}${p}`;
const READ = "2026-11-14T12:00:00Z";

/** Mixed kinds, some read; ops.* rows are only served while the mock user is an admin. */
function seed(): AppNotification[] {
  const n = (id: number, kind: string, payload: Record<string, unknown>, read = false): AppNotification =>
    ({ id, kind, payload, created_at: new Date(Date.UTC(2026, 10, 15, 9, 0) - (20 - id) * 3_600_000).toISOString(), read_at: read ? READ : null });
  return [
    n(1, "upload.searchable", { document_id: DOCS[2].id, version_id: `${DOCS[2].id}-v1`, filename: "trace-method.pdf", title: DOCS[2].title }, true),
    n(2, "upload.failed", { document_id: DOCS[3].id, version_id: "v-x", filename: "scan-0412.pdf", error: "the file is password protected" }, true),
    n(3, "ops.job_failed", { job_id: 812, kind: "reembed", error: "processing failed" }),
    n(4, "upload.near_dup", { document_id: DOCS[4].id, version_id: "v-y", filename: "spectral-gap-v3.pdf", match_document_id: DOCS[0].id, match_title: DOCS[0].title }),
    n(5, "ops.connector_run", { connector_id: "c-1", name: "Group share", run_id: 31, status: "partial", stats: { added: 4, failed: 1 } }),
    n(6, "upload.searchable", { document_id: DOCS[1].id, version_id: `${DOCS[1].id}-v1`, filename: "hypergraphs.pdf", title: DOCS[1].title }),
  ];
}

const all = () => (state.notifications ??= seed());
const mine = () => all().filter((n) => state.admin || !n.kind.startsWith("ops.")).sort((a, b) => b.id - a.id);

export const notificationHandlers = [
  http.get(url("/me/notifications"), ({ request }) => {
    const sp = new URL(request.url).searchParams;
    const limit = Math.min(200, Math.max(1, Number(sp.get("limit") ?? 50)));
    const before = sp.get("before_id");
    const rows = mine().filter((n) => (sp.get("unread_only") !== "true" || !n.read_at) && (before == null || n.id < Number(before)));
    const page = rows.slice(0, limit);
    return HttpResponse.json({
      items: page, next_before_id: rows.length > limit ? page[page.length - 1].id : null, unread: mine().filter((n) => !n.read_at).length,
    });
  }),
  http.post(url("/me/notifications/read"), async ({ request }) => {
    const b = (await request.json()) as { ids?: number[]; all?: boolean };
    const hit = mine().filter((n) => !n.read_at && (b.all || b.ids?.includes(n.id)));
    hit.forEach((n) => { n.read_at = new Date().toISOString(); });
    return HttpResponse.json({ updated: hit.length });
  }),
];
