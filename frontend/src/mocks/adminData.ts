import { http, HttpResponse } from "msw";
import { API_BASE } from "@/api/client";
import type { AdminUser, AuditEntry, SettingsMap, SettingValue, TagCount } from "@/api/admin";
import { state } from "./state";

const url = (p: string) => `${API_BASE}${p}`;
const forbidden = () => HttpResponse.json({ detail: "admin only" }, { status: 403 });
const MiB = 1024 * 1024;

export const ENV: Record<string, SettingValue> = {
  chat_models: ["qwen2.5:14b", "llama3.1:8b"], chat_default_model: "qwen2.5:14b", chat_passages: 8, chat_note_min_upvotes: 2, chat_note_min_ratio: 0.7,
  rerank_depth: 40, near_dup_threshold: 0.92, near_dup_summary_threshold: 0.85, max_upload_bytes: 500 * MiB, upload_quota_bytes: 0, rate_limit_per_minute: 60, pats_enabled: true,
};
const RANGE: Record<string, [number, number]> = { chat_passages: [4, 12], rerank_depth: [10, 500], near_dup_threshold: [0.5, 1], near_dup_summary_threshold: [0.5, 1], chat_note_min_ratio: [0, 1], max_upload_bytes: [MiB, 5 * 1024 * MiB] };

export interface DataState {
  overrides: Record<string, SettingValue>; tags: TagCount[]; audit: AuditEntry[]; users: AdminUser[]; revoked: string[];
}
const seed = (): DataState => ({
  overrides: {},
  tags: [{ tag: "spectral-theory", documents: 14 }, { tag: "spectral theory", documents: 3 }, { tag: "pde", documents: 9 }, { tag: "operators", documents: 6 }, { tag: "survey", documents: 2 }],
  audit: Array.from({ length: 60 }, (_, i): AuditEntry => ({
    id: 200 - i, at: `2026-11-15T${String(10 - (i % 10)).padStart(2, "0")}:00:00Z`, user_id: "u-admin", username: "admin", client: null,
    action: i % 3 ? "admin.settings_update" : "admin.tags_merge", target: i % 3 ? "/admin/settings" : "pde", detail: { changes: { rerank_depth: { old: 40, new: 50 } }, note: "<b>x</b>".repeat(i % 7 ? 1 : 40) },
  })),
  users: [
    { id: "u-me", username: "researcher", email: "r@example.org", groups: ["/Mathematics/Spectral Theory", "/Mathematics", "/Physics", "/Staff"], is_admin: false, created_at: "2026-01-01T00:00:00Z", last_login_at: "2026-11-14T09:00:00Z", documents: 12, bytes: 40 * MiB },
    { id: "u-admin", username: "admin", email: "a@example.org", groups: ["/Staff"], is_admin: true, created_at: "2026-01-01T00:00:00Z", last_login_at: null, documents: 2, bytes: 3 * MiB },
    ...Array.from({ length: 25 }, (_, i): AdminUser => ({ id: `u-${i}`, username: `student${String(i).padStart(2, "0")}`, email: null, groups: ["/Mathematics"], is_admin: false, created_at: "2026-02-01T00:00:00Z", last_login_at: null, documents: i, bytes: i * MiB })),
  ],
  revoked: [],
});
export const dataOf = () => (state.adminData ??= seed());

const view = (d: DataState): SettingsMap =>
  Object.fromEntries(Object.entries(ENV).map(([k, v]) => [k, k in d.overrides ? { value: d.overrides[k], source: "override" as const } : { value: v, source: "env" as const }]));

export const adminDataHandlers = [
  http.get(url("/admin/settings"), () => (state.admin ? HttpResponse.json(view(dataOf())) : forbidden())),
  http.put(url("/admin/settings"), async ({ request }) => {
    if (!state.admin) return forbidden();
    const d = dataOf();
    const body = (await request.json()) as Record<string, SettingValue | null>;
    for (const [k, v] of Object.entries(body)) {
      if (!(k in ENV)) return HttpResponse.json({ detail: [{ loc: ["body", k], msg: "Extra inputs are not permitted" }] }, { status: 422 });
      const r = RANGE[k];
      if (v !== null && r && (typeof v !== "number" || v < r[0] || v > r[1])) return HttpResponse.json({ detail: [{ loc: ["body", k], msg: `Input should be between ${r[0]} and ${r[1]}` }] }, { status: 422 });
    }
    const next = { ...d.overrides };
    for (const [k, v] of Object.entries(body)) { if (v === null) delete next[k]; else next[k] = v; }
    const models = (next.chat_models ?? ENV.chat_models) as string[];
    const def = next.chat_default_model;
    if (def !== undefined && !models.includes(def as string)) return HttpResponse.json({ detail: "chat_default_model must be one of chat_models" }, { status: 422 });
    d.overrides = next;
    return HttpResponse.json(view(d));
  }),
  http.get(url("/admin/tags"), ({ request }) => {
    if (!state.admin) return forbidden();
    const sp = new URL(request.url).searchParams;
    const all = dataOf().tags.filter((t) => t.tag.toLowerCase().includes((sp.get("q") ?? "").toLowerCase()));
    return HttpResponse.json({ items: all.slice(0, Number(sp.get("limit") ?? 50)), total: all.length });
  }),
  http.post(url("/admin/tags/merge"), async ({ request }) => {
    if (!state.admin) return forbidden();
    const d = dataOf();
    const { sources, target } = (await request.json()) as { sources: string[]; target: string };
    const src = sources.filter((s) => s !== target);
    const moved = d.tags.filter((t) => src.includes(t.tag));
    const documents = moved.reduce((n, t) => n + t.documents, 0);
    d.tags = d.tags.filter((t) => !src.includes(t.tag));
    const hit = d.tags.find((t) => t.tag === target);
    if (hit) hit.documents += documents; else if (documents) d.tags.push({ tag: target, documents });
    return HttpResponse.json({ documents });
  }),
  http.get(url("/admin/audit"), ({ request }) => {
    if (!state.admin) return forbidden();
    const sp = new URL(request.url).searchParams;
    const limit = Math.min(200, Number(sp.get("limit") ?? 50));
    const before = sp.get("before_id");
    const left = dataOf().audit.filter((a) => (before == null || a.id < Number(before)) && a.action.startsWith(sp.get("action") ?? "") && (!sp.get("user_id") || a.user_id === sp.get("user_id")));
    const items = left.slice(0, limit);
    return HttpResponse.json({ items, next_before_id: left.length > limit ? items[items.length - 1].id : null });
  }),
  http.get(url("/admin/users"), ({ request }) => {
    if (!state.admin) return forbidden();
    const sp = new URL(request.url).searchParams;
    const q = (sp.get("q") ?? "").toLowerCase(), page = Math.max(1, Number(sp.get("page") ?? 1)), limit = Number(sp.get("limit") ?? 50);
    const all = dataOf().users.filter((u) => !q || u.username.toLowerCase().includes(q) || (u.email ?? "").toLowerCase().includes(q));
    return HttpResponse.json({ items: all.slice((page - 1) * limit, page * limit), total: all.length });
  }),
  http.delete(url("/admin/users/:id/pats"), ({ params }) => {
    if (!state.admin) return forbidden();
    dataOf().revoked.push(String(params.id));
    return new HttpResponse(null, { status: 204 });
  }),
  http.get(url("/admin/groups"), () => (state.admin ? HttpResponse.json({ items: [{ group: "/Mathematics", members: 27 }, { group: "/Mathematics/Spectral Theory", members: 1 }, { group: "/Staff", members: 2 }], total: 3 }) : forbidden())),
  http.get(url("/admin/stats"), () => state.admin ? HttpResponse.json({
    documents: { live: 41, deleted: 3 }, versions: { searchable: 41, review: 2, needs_title: 1, rejected: 1 }, blobs: { count: 47, bytes: 912 * MiB },
    users: dataOf().users.length, chats: 18, connectors: 3,
    top_uploaders: [{ user_id: "u-me", username: "researcher", documents: 12, bytes: 40 * MiB }, { user_id: "u-admin", username: "admin", documents: 2, bytes: 3 * MiB }],
  }) : forbidden()),
  http.get(url("/admin/health"), () => state.admin ? HttpResponse.json({
    status: "ok", checks: { db: "ok", elastic: "ok", storage: "ok" }, queue: { queued: 1, running: 1, failed: 2, oldest_queued_s: 12 }, scheduler: { tick_queued: false },
  }) : forbidden()),
];
