// Mock admin eval API: sets, drafts/approval, runs (oldest first), run detail, R5 refusal.
import { http, HttpResponse } from "msw";
import { API_BASE } from "@/api/client";
import type { EvalQuery, EvalRunItem, EvalSet } from "@/features/admin/evalApi";
import { state } from "./state";

const url = (p: string) => `${API_BASE}${p}`;
const err = (status: number, detail: string) => HttpResponse.json({ detail }, { status });
export const R5 = "evaluate as at least one non-admin principal";
export const LONG = "What is the relationship between the spectral gap of the Laplacian on a compact Riemannian manifold and its Cheeger constant, and how does that change for graphs?";

export interface EvalMock { sets: EvalSet[]; queries: EvalQuery[]; runs: EvalRunItem[]; started: { set_id: string; path: string; judge: boolean }[]; generated: { id: string; n: number; principal: string; kind?: string }[] }
const q = (id: string, set_id: string, text: string, status: "draft" | "active", principal_names: string[]): EvalQuery => ({ id, set_id, key: id, text, kind: "fact", principals: principal_names.map((p) => `u-${p}`), principal_names, as_pat: false, relevant: [{ doc: "d1" }], status });
const run = (n: number, ndcg: number, rec: number): EvalRunItem => ({ id: `r${n}`, set_id: "s1", set_name: "scifact", started_at: `2026-11-0${n}T10:00:00Z`, trigger: "admin", label: "", path: "chat", status: "done", metrics: { overall: { "p_ndcg@10": ndcg, "p_recall@10": rec, "p_recall@100": 0.9, p_mrr: 0.5, pairs: 10 } } });
const seed = (): EvalMock => ({
  sets: [{ id: "s1", name: "scifact", description: "", is_reference: true, queries: 2, drafts: 1 }, { id: "s2", name: "admins", description: "", is_reference: false, queries: 1, drafts: 0 }],
  queries: [q("q1", "s1", LONG, "active", ["ana"]), q("q2", "s1", "short one", "active", ["ana", "ben", "k.ito", "dee", "eve"]), q("q3", "s1", "drafted question", "draft", ["ana"]), q("q4", "s2", "adm", "active", ["root"])],
  runs: [run(1, 0.41, 0.52), run(2, 0.45, 0.55), run(3, 0.5, 0.6)],
  started: [], generated: [],
});
export const evalOf = () => (state.evalData ??= seed());
const admin = () => (state.admin ? null : err(403, "admin only"));
const setJson = (s: EvalSet) => { const qs = evalOf().queries.filter((x) => x.set_id === s.id); return { ...s, queries: qs.filter((x) => x.status === "active").length, drafts: qs.filter((x) => x.status === "draft").length }; };

export const evalHandlers = [
  http.get(url("/admin/eval/sets"), () => admin() ?? HttpResponse.json({ items: evalOf().sets.map(setJson) })),
  http.post(url("/admin/eval/sets"), async ({ request }) => {
    const d = admin(); if (d) return d;
    const b = (await request.json()) as { name: string };
    const s: EvalSet = { id: `s${evalOf().sets.length + 1}`, name: b.name, description: "", is_reference: false, queries: 0, drafts: 0 };
    evalOf().sets.push(s);
    return HttpResponse.json(s, { status: 201 });
  }),
  http.patch(url("/admin/eval/sets/:id"), async ({ params, request }) => {
    const d = admin(); if (d) return d;
    const b = (await request.json()) as { is_reference?: boolean };
    const s = evalOf().sets.find((x) => x.id === params.id);
    if (!s) return err(404, "not found");
    if (b.is_reference) evalOf().sets.forEach((x) => (x.is_reference = x === s));
    return HttpResponse.json(setJson(s));
  }),
  http.get(url("/admin/eval/sets/:id/queries"), ({ params }) => admin() ?? HttpResponse.json({ items: evalOf().queries.filter((x) => x.set_id === params.id) })),
  http.post(url("/admin/eval/sets/:id/generate"), async ({ params, request }) => {
    const d = admin(); if (d) return d;
    const b = (await request.json()) as { n: number; principal: string; kind?: string };
    evalOf().generated.push({ id: String(params.id), ...b });
    return HttpResponse.json({ job_id: 1 }, { status: 202 });
  }),
  http.patch(url("/admin/eval/queries/:id"), async ({ params, request }) => {
    const d = admin(); if (d) return d;
    const row = evalOf().queries.find((x) => x.id === params.id);
    if (!row) return err(404, "not found");
    Object.assign(row, await request.json());
    return HttpResponse.json(row);
  }),
  http.delete(url("/admin/eval/queries/:id"), ({ params }) => {
    const d = admin(); if (d) return d;
    evalOf().queries = evalOf().queries.filter((x) => x.id !== params.id);
    return new HttpResponse(null, { status: 204 });
  }),
  http.get(url("/admin/eval/runs"), ({ request }) => {
    const d = admin(); if (d) return d;
    const sid = new URL(request.url).searchParams.get("set_id");
    return HttpResponse.json({ items: evalOf().runs.filter((r) => !sid || r.set_id === sid) });
  }),
  http.post(url("/admin/eval/runs"), async ({ request }) => {
    const d = admin(); if (d) return d;
    const b = (await request.json()) as { set_id: string; path: string; judge: boolean };
    if (b.set_id === "s2") return err(422, R5);
    evalOf().started.push(b);
    return HttpResponse.json({ job_id: 2 }, { status: 202 });
  }),
  http.get(url("/admin/eval/runs/:id"), ({ params }) => {
    const d = admin(); if (d) return d;
    const r = evalOf().runs.find((x) => x.id === params.id);
    if (!r) return err(404, "not found");
    const { metrics, ...rest } = r;
    return HttpResponse.json({ ...rest, error: null, by_kind: { fact: metrics!.overall }, by_principal: { ana: metrics!.overall }, worst: [{ query_key: "q1", text: LONG, kind: "fact", principal: "ana", "p_recall@10": 0, top_docs: ["Spectral gaps", "Cheeger"] }] });
  }),
];
