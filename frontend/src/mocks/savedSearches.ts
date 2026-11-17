import { http, HttpResponse } from "msw";
import { API_BASE } from "@/api/client";
import type { SavedSearch } from "@/api/types";
import { state } from "./state";

const url = (p: string) => `${API_BASE}/me/saved-searches${p}`;
const dup = () => HttpResponse.json({ detail: "a saved search with this name exists" }, { status: 409 });
const list = () => (state.savedSearches ??= []);
let seq = 1;

export const savedSearchHandlers = [
  http.get(url(""), () => HttpResponse.json([...list()].sort((a, b) => b.updated_at.localeCompare(a.updated_at)))),
  http.post(url(""), async ({ request }) => {
    const { name, query } = (await request.json()) as { name: string; query: string };
    if (list().some((s) => s.name === name.trim())) return dup();
    if (list().length >= 200) return HttpResponse.json({ detail: "at most 200 saved searches" }, { status: 422 });
    const now = new Date(Date.UTC(2026, 10, 17, 9, 0, seq)).toISOString();
    const s: SavedSearch = { id: `ss-${seq++}`, name: name.trim(), query, created_at: now, updated_at: now };
    list().push(s);
    return HttpResponse.json(s, { status: 201 });
  }),
  http.patch(url("/:id"), async ({ params, request }) => {
    const s = list().find((x) => x.id === params.id);
    if (!s) return HttpResponse.json({ detail: "not found" }, { status: 404 });
    const b = (await request.json()) as { name?: string; query?: string };
    if (b.name !== undefined && list().some((x) => x.id !== s.id && x.name === b.name!.trim())) return dup();
    Object.assign(s, { ...(b.name !== undefined && { name: b.name.trim() }), ...(b.query !== undefined && { query: b.query }), updated_at: new Date().toISOString() });
    return HttpResponse.json(s);
  }),
  http.delete(url("/:id"), ({ params }) => {
    const i = list().findIndex((x) => x.id === params.id);
    if (i < 0) return HttpResponse.json({ detail: "not found" }, { status: 404 });
    list().splice(i, 1);
    return new HttpResponse(null, { status: 204 });
  }),
];
