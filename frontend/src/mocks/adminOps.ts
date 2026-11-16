import { http, HttpResponse } from "msw";
import { API_BASE } from "@/api/client";
import type { AdminJob, Connector, ConnectorKind, ConnectorRunDetail, ReviewItem } from "@/api/admin";
import { state } from "./state";

const url = (p: string) => `${API_BASE}${p}`;
const forbidden = () => HttpResponse.json({ detail: "admin only" }, { status: 403 });
const notFound = () => HttpResponse.json({ detail: "not found" }, { status: 404 });

export interface OpsState { connectors: Connector[]; runs: Record<string, ConnectorRunDetail[]>; jobs: AdminJob[]; seq: number }

const CID = (n: number) => `00000000-0000-4000-8000-${String(n).padStart(12, "0")}`;
const cfg: Record<ConnectorKind, Record<string, unknown>> = {
  s3: { endpoint: "https://s3.example.org", bucket: "group-papers", prefix: "preprints/", region: "us-east-1", access_key: "AKIAEXAMPLE", secret_key: "***" },
  fs: { path: "/data/shared/papers", include: ["*.pdf", "*.tex"] },
  http: { start_urls: ["https://arxiv.example.org/list/math.SP"], max_depth: 1, max_pages: 100, respect_robots: true, ingest_html: false },
};
const conn = (n: number, name: string, kind: ConnectorKind, o: Partial<Connector> = {}): Connector => ({
  id: CID(n), name, kind, config: cfg[kind], visibility: "everyone", org_unit: "", schedule_minutes: 60, enabled: true,
  next_run_at: null, last_run_at: null, created_at: "2026-10-01T09:00:00Z", last_run: null, ...o,
});
const run = (id: number, status: ConnectorRunDetail["status"], log: string): ConnectorRunDetail => ({
  id, status, started_at: `2026-11-15T0${id % 9}:00:00Z`, finished_at: status === "running" ? null : `2026-11-15T0${id % 9}:04:00Z`,
  stats: { seen: 40 + id, ingested: 12, versions: 1, unchanged: 25, duplicate: 2, too_large: 0, errors: status === "failed" ? 3 : 0 }, log,
});
const job = (id: number, kind: string, status: AdminJob["status"], o: Partial<AdminJob> = {}): AdminJob => ({
  id, kind, status, attempts: status === "failed" ? 3 : 0, last_error: null, payload: {}, created_at: "2026-11-15T08:00:00Z", updated_at: "2026-11-15T08:30:00Z", ...o,
});

function seed(): OpsState {
  const runs = {
    [CID(1)]: [run(31, "running", "start\nlisting bucket…\n"), run(30, "partial", "start\nwarn: 1 object skipped\ndone\n"), run(29, "ok", "start\ndone\n")],
    [CID(2)]: [run(20, "failed", "start\nerror: path not readable\n")],
    [CID(3)]: [],
  };
  const withLast = (c: Connector) => ({ ...c, last_run: runs[c.id]?.[0] ?? null });
  return {
    connectors: [conn(1, "Group share", "s3"), conn(2, "Server folder", "fs", { schedule_minutes: null }), conn(3, "arXiv listing", "http", { enabled: false, visibility: "org_unit", org_unit: "/Mathematics" })].map(withLast),
    runs, seq: 100,
    jobs: [
      job(812, "reembed", "failed", { last_error: "processing failed" }),
      job(811, "ingest", "failed", { last_error: "text recognition timed out", document_id: "00000000-0000-4000-8000-000000000003", filename: "scan-0412.pdf" }),
      job(810, "ingest", "running"), job(809, "ingest", "queued"), job(808, "reembed", "done"), job(807, "connector_run", "done"),
    ],
  };
}
export const ops = () => (state.ops ??= seed());

const lastOf = (o: OpsState, c: Connector) => ({ ...c, last_run: o.runs[c.id]?.[0] ? { ...o.runs[c.id][0], log: undefined } : null });
const keyset = <T extends { id: number }>(rows: T[], sp: URLSearchParams) => {
  const limit = Math.min(200, Number(sp.get("limit") ?? 50));
  const before = sp.get("before_id");
  const left = rows.filter((r) => before == null || r.id < Number(before)).sort((a, b) => b.id - a.id);
  const items = left.slice(0, limit);
  return { items, next_before_id: left.length > limit ? items[items.length - 1].id : null };
};

export const adminOpsHandlers = [
  http.get(url("/admin/connectors"), () => (state.admin ? HttpResponse.json({ items: ops().connectors.map((c) => lastOf(ops(), c)) }) : forbidden())),
  http.post(url("/admin/connectors"), async ({ request }) => {
    if (!state.admin) return forbidden();
    const b = (await request.json()) as Partial<Connector>;
    const o = ops();
    if (o.connectors.some((c) => c.name === b.name)) return HttpResponse.json({ detail: "connector name already exists" }, { status: 409 });
    const c = { ...conn(o.seq, String(b.name), b.kind!), ...b, id: CID(o.seq++), next_run_at: null, last_run: null } as Connector;
    o.connectors.push(c); o.runs[c.id] = [];
    return HttpResponse.json(c, { status: 201 });
  }),
  http.get(url("/admin/connectors/:id"), ({ params }) => {
    if (!state.admin) return forbidden();
    const c = ops().connectors.find((x) => x.id === params.id);
    return c ? HttpResponse.json(lastOf(ops(), c)) : notFound();
  }),
  http.patch(url("/admin/connectors/:id"), async ({ params, request }) => {
    if (!state.admin) return forbidden();
    const c = ops().connectors.find((x) => x.id === params.id);
    if (!c) return notFound();
    const { config, ...rest } = (await request.json()) as Partial<Connector>;
    Object.assign(c, rest);
    if (config) {
      const next = { ...c.config };
      for (const [k, v] of Object.entries(config)) { if (v === null) delete next[k]; else if (!(k === "secret_key" && v === "***")) next[k] = v; }
      c.config = { ...next, ...(next.secret_key ? { secret_key: "***" } : {}) };
    }
    return HttpResponse.json(lastOf(ops(), c));
  }),
  http.delete(url("/admin/connectors/:id"), ({ params }) => {
    if (!state.admin) return forbidden();
    const o = ops();
    o.connectors = o.connectors.filter((c) => c.id !== params.id);
    return new HttpResponse(null, { status: 204 });
  }),
  http.post(url("/admin/connectors/:id/run"), ({ params }) => {
    if (!state.admin) return forbidden();
    const o = ops();
    if (!o.connectors.some((c) => c.id === params.id)) return notFound();
    if (o.runs[String(params.id)]?.[0]?.status === "running") return HttpResponse.json({ detail: "a run is already queued or running" }, { status: 409 });
    return HttpResponse.json({ job_id: o.seq++ }, { status: 202 });
  }),
  http.get(url("/admin/connectors/:id/runs"), ({ params, request }) => {
    if (!state.admin) return forbidden();
    const rows = (ops().runs[String(params.id)] ?? []).map(({ log: _l, ...r }) => r);
    return HttpResponse.json(keyset(rows, new URL(request.url).searchParams));
  }),
  http.get(url("/admin/connectors/:id/runs/:runId"), ({ params }) => {
    if (!state.admin) return forbidden();
    const r = ops().runs[String(params.id)]?.find((x) => x.id === Number(params.runId));
    return r ? HttpResponse.json(r) : notFound();
  }),
  http.get(url("/admin/jobs/summary"), () => {
    if (!state.admin) return forbidden();
    const jobs: Record<string, Record<string, number>> = {};
    for (const j of ops().jobs) { jobs[j.kind] ??= {}; jobs[j.kind][j.status] = (jobs[j.kind][j.status] ?? 0) + 1; }
    return HttpResponse.json({ jobs, versions: { searchable: 41, review: state.uploads.filter((u) => u.stage === "review").length, needs_title: 1 } });
  }),
  http.get(url("/admin/jobs"), ({ request }) => {
    if (!state.admin) return forbidden();
    const sp = new URL(request.url).searchParams;
    const rows = ops().jobs.filter((j) => (!sp.get("status") || j.status === sp.get("status")) && (!sp.get("kind") || j.kind === sp.get("kind")));
    return HttpResponse.json(keyset(rows, sp));
  }),
  http.post(url("/admin/jobs/retry-failed"), async ({ request }) => {
    if (!state.admin) return forbidden();
    const { kind } = ((await request.json().catch(() => ({}))) ?? {}) as { kind?: string };
    const hit = ops().jobs.filter((j) => j.status === "failed" && (!kind || j.kind === kind));
    hit.forEach((j) => { j.status = "queued"; j.attempts = 0; j.last_error = null; });
    return HttpResponse.json({ retried: hit.length });
  }),
  http.post(url("/admin/jobs/:id/retry"), ({ params }) => {
    if (!state.admin) return forbidden();
    const j = ops().jobs.find((x) => x.id === Number(params.id));
    if (!j) return notFound();
    if (j.status !== "failed") return HttpResponse.json({ detail: "only failed jobs can be retried" }, { status: 409 });
    Object.assign(j, { status: "queued", attempts: 0, last_error: null });
    return HttpResponse.json(j);
  }),
  http.get(url("/admin/review"), ({ request }) => {
    if (!state.admin) return forbidden();
    const sp = new URL(request.url).searchParams;
    const page = Math.max(1, Number(sp.get("page") ?? 1)), limit = Number(sp.get("limit") ?? 50);
    const all = state.uploads.filter((u) => u.stage === "review").map((u): ReviewItem => ({
      document_id: u.document_id, version_id: u.version_id, filename: u.filename, owner: "researcher", connector_id: null, uploaded_at: u.uploaded_at, match: u.near_dup_of && { document_id: u.near_dup_of.document_id, title: u.near_dup_of.title ?? "" },
    })).filter(() => sp.get("source") !== "connector");
    return HttpResponse.json({ items: all.slice((page - 1) * limit, page * limit), total: all.length });
  }),
];
