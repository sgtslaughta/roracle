// Mock sensitivity labels: GET /labels, document/note label PUTs and admin CRUD, with the backend's rules and error strings.
import { http, HttpResponse } from "msw";
import { API_BASE } from "@/api/client";
import type { AdminLabel, Label } from "@/api/types";
import { DOCS } from "./docs";
import { state } from "./state";

const url = (p: string) => `${API_BASE}${p}`;
const err = (status: number, detail: string) => HttpResponse.json({ detail }, { status });
const GATES = ["exclude_llm", "block_agents", "block_export", "reason_on_lower"] as const;
const G = (o: Partial<Label["gates"]> = {}) => ({ exclude_llm: false, block_agents: false, block_export: false, reason_on_lower: false, ...o });

export const SEED_LABELS: AdminLabel[] = [
  { id: "l-general", name: "General", colour: "#496580", rank: 0, description: "Everyday material.", is_default: true, gates: G() },
  { id: "l-internal", name: "Internal", colour: "#8a5a00", rank: 1, description: "Not for outside the organisation.", is_default: false, gates: G({ reason_on_lower: true }) },
  { id: "l-confidential", name: "Confidential", colour: "#a02a2a", rank: 2, description: "Restricted handling.", is_default: false, gates: G({ exclude_llm: true, block_agents: true, block_export: true, reason_on_lower: true }) },
];
export const toLabel = ({ id, name, colour, rank, gates }: AdminLabel): Label => ({ id, name, colour, rank, gates: { ...gates } });

export const labels = (): AdminLabel[] => (state.labels ??= SEED_LABELS.map((l) => ({ ...l, gates: { ...l.gates } }))).sort((a, b) => a.rank - b.rank);
export const defaultLabel = () => labels().find((l) => l.is_default) ?? labels()[0];
const byId = (id: string | undefined | null) => labels().find((l) => l.id === id);
export const labelJson = (id: string): Label => toLabel(byId(id) ?? defaultLabel());
/** Seeded: the first document is Internal, the second Confidential; the rest take the default. */
export const docLabel = (docId: string): Label => {
  const own = state.docLabels[docId];
  const seeded = docId === DOCS[0].id ? "l-internal" : docId === DOCS[1].id ? "l-confidential" : undefined;
  return labelJson(own ?? seeded ?? defaultLabel().id);
};
export const noteOwnLabel = (noteId: string): Label | null => (state.noteLabels[noteId] ? labelJson(state.noteLabels[noteId]) : null);

/** Gates must not decrease with rank (the backend's 422). */
function monotonic(list: AdminLabel[]): boolean {
  const s = [...list].sort((a, b) => a.rank - b.rank);
  return GATES.every((g) => s.every((l, i) => !l.gates[g] || s.slice(i).every((h) => h.gates[g])));
}
const NOT_MONOTONIC = "gates must not decrease with rank";
const COLOUR = /^#[0-9a-fA-F]{6}$/;

function check(next: AdminLabel[]): Response | null {
  if (new Set(next.map((l) => l.name.toLowerCase())).size !== next.length) return err(422, "label names must be unique");
  if (new Set(next.map((l) => l.rank)).size !== next.length) return err(422, "label ranks must be unique");
  return monotonic(next) ? null : err(422, NOT_MONOTONIC);
}
const admin = () => (state.admin ? null : err(403, "admin only"));

export const labelHandlers = [
  http.get(url("/labels"), () => HttpResponse.json({ items: labels() })),
  http.post(url("/admin/labels"), async ({ request }) => {
    const d = admin(); if (d) return d;
    const b = (await request.json()) as Partial<AdminLabel>;
    if (!b.name?.trim() || !COLOUR.test(b.colour ?? "")) return err(422, "invalid label");
    const row: AdminLabel = { id: `l-${labels().length + 1}-${Date.now() % 1000}`, name: b.name.trim(), colour: b.colour!, rank: b.rank ?? 0, description: b.description ?? "", is_default: false, gates: G(b.gates) };
    const next = [...labels(), row];
    const bad = check(next); if (bad) return bad;
    if (b.is_default) next.forEach((l) => (l.is_default = l === row));
    state.labels = next;
    return HttpResponse.json(row, { status: 201 });
  }),
  http.put(url("/admin/labels/order"), async ({ request }) => {
    const d = admin(); if (d) return d;
    const { ids } = (await request.json()) as { ids: string[] };
    if (ids.length !== labels().length || ids.some((i) => !byId(i))) return err(422, "ids must list every label");
    const old = labels().map((l) => l.rank).sort((a, b) => a - b);
    const next = ids.map((id, i) => ({ ...byId(id)!, rank: old[i] }));
    const bad = check(next); if (bad) return bad;
    state.labels = next;
    return HttpResponse.json({ items: labels() });
  }),
  http.patch(url("/admin/labels/:id"), async ({ params, request }) => {
    const d = admin(); if (d) return d;
    const cur = byId(String(params.id));
    if (!cur) return err(404, "not found");
    const b = (await request.json()) as Partial<AdminLabel>;
    if (b.colour && !COLOUR.test(b.colour)) return err(422, "invalid colour");
    const row: AdminLabel = { ...cur, ...b, gates: { ...cur.gates, ...b.gates }, is_default: b.is_default ?? cur.is_default };
    const next = labels().map((l) => (l.id === cur.id ? row : l));
    const bad = check(next); if (bad) return bad;
    if (b.is_default) next.forEach((l) => (l.is_default = l.id === row.id));
    state.labels = next;
    return HttpResponse.json(row);
  }),
  http.delete(url("/admin/labels/:id"), ({ params }) => {
    const d = admin(); if (d) return d;
    const cur = byId(String(params.id));
    if (!cur) return err(404, "not found");
    const used = DOCS.some((x) => docLabel(x.id).id === cur.id) || Object.values(state.noteLabels).includes(cur.id);
    if (cur.is_default || used) return err(409, "label in use");
    state.labels = labels().filter((l) => l.id !== cur.id);
    return new HttpResponse(null, { status: 204 });
  }),
  http.put(url("/documents/:id/label"), async ({ params, request }) => {
    const doc = DOCS.find((x) => x.id === params.id);
    if (!doc) return err(404, "not found");
    const mine = (state.owners[doc.id] ?? (doc === DOCS[0] ? "u-me" : "u-owner")) === "u-me";
    if (!mine && !state.admin) return err(403, "only the document owner or an admin can do this");
    const b = (await request.json()) as { label_id: string; reason?: string };
    const to = byId(b.label_id), from = docLabel(doc.id);
    if (!to) return err(422, "unknown label");
    if (to.rank < from.rank) {
      if (!state.admin) return err(403, "only admins can lower a label");
      if (from.gates.reason_on_lower && (b.reason ?? "").trim().length < 3) return err(422, "a reason is required to lower this label");
    }
    state.docLabels[doc.id] = to.id;
    return HttpResponse.json({ label: toLabel(to) });
  }),
];
