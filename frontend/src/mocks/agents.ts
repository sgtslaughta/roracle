import { http, HttpResponse } from "msw";
import { API_BASE } from "@/api/client";
import type { AccessToken, CreateHandoffBody, CreateTokenBody, HandoffManifest, HandoffRow, HandoffShare, PatchHandoffBody } from "@/api/agents";
import { DOCS } from "./docs";
import { state } from "./state";
import { docLabel } from "./labels";

/** Handoff kept by the mock: the manifest plus creator-only fields the API never returns. */
export interface MockHandoff { m: HandoffManifest; revoked_at: string | null; shares: HandoffShare[] }

const url = (p: string) => `${API_BASE}${p}`;
const err = (status: number, detail: string) => HttpResponse.json({ detail }, { status });
const DAY = 86_400_000;
const iso = (ms = 0) => new Date(Date.now() + ms).toISOString();
const ALPHABET = "23456789ABCDEFGHJKMNPQRSTUVWXYZ";
const rand = (n: number, a = ALPHABET) => Array.from({ length: n }, () => a[Math.floor(Math.random() * a.length)]).join("");
const display = (c: string) => `${c.slice(0, 4)}-${c.slice(4)}`;
/** Any accepted form (lowercase, hyphen, /h/ URL, roracle://) -> canonical code. */
const normalize = (s: string) => s.split(/[?#]/)[0].replace(/\/+$/, "").split("/").pop()!.replace(/[\s-]/g, "").toUpperCase();

const seedTokens = (): AccessToken[] => [{
  id: "tok-1", name: "Laptop agent", prefix: "ror_k3Pq", scopes: ["read"], expires_at: iso(20 * DAY),
  revoked_at: null, last_used_at: iso(-2 * 3600_000), created_at: iso(-10 * DAY),
}];

export const tokens = () => (state.tokens ??= seedTokens());

function row(h: MockHandoff): HandoffRow {
  const { code, display, kind, title, audience, expires_at, created_at, created_via } = h.m;
  return { code, display, kind, title, audience, expires_at, revoked_at: h.revoked_at, created_at, created_via };
}

function find(raw: string): MockHandoff | Response {
  const h = state.handoffs[normalize(raw)];
  if (!h) return err(404, "not found");
  return h;
}

export const agentHandlers = [
  http.get(url("/me/tokens"), () => HttpResponse.json(tokens())),
  http.post(url("/me/tokens"), async ({ request }) => {
    const b = (await request.json()) as CreateTokenBody;
    const name = String(b.name ?? "").trim();
    if (!name || name.length > 100) return err(422, "name must be 1-100 characters");
    if (!(b.expires_in_days >= 1 && b.expires_in_days <= 365)) return err(422, "expires_in_days must be 1-365");
    const prefix = `ror_${rand(4, "abcdefghijkmnpqrstuvwxyz23456789")}`;
    const t: AccessToken = {
      id: `tok-${Date.now()}-${rand(3)}`, name, prefix, scopes: (b.scopes as string[] | undefined)?.includes("write") ? ["read", "write"] : ["read"],
      expires_at: iso(b.expires_in_days * DAY), revoked_at: null, last_used_at: null, created_at: iso(),
    };
    tokens().unshift(t);
    return HttpResponse.json({ ...t, token: `${prefix}_${rand(32, "abcdefghijkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789")}` }, { status: 201 });
  }),
  http.delete(url("/me/tokens/:id"), ({ params }) => {
    const t = tokens().find((x) => x.id === params.id);
    if (!t) return err(404, "not found");
    t.revoked_at ??= iso();
    return new HttpResponse(null, { status: 204 });
  }),

  http.post(url("/handoffs"), async ({ request }) => {
    const b = (await request.json()) as CreateHandoffBody;
    if (b.audience === "shared" && !b.shares?.length) return err(422, "shared audience needs at least one share");
    if (!(b.expires_in_days >= 1 && b.expires_in_days <= 90)) return err(422, "expires_in_days must be 1-90");
    const code = rand(8);
    const ids = [...new Set([...(b.kind === "document" && b.source_id ? [b.source_id] : []), ...b.doc_ids])].filter((id) => DOCS.some((d) => d.id === id));
    const m: HandoffManifest = {
      code, display: display(code), url: `/h/${display(code)}`, uri: `roracle://handoff/${code}`, kind: b.kind,
      title: b.title, goal: b.goal, brief: b.brief, source: b.source_id ? { id: b.source_id } : {}, doc_ids: ids, documents: ids.map((id) => ({ id, title: DOCS.find((d) => d.id === id)!.title, label: docLabel(id), text_withheld: false })), passages: [], notes: [],
      withheld: 0, parts: { transcript: 0, notes: 0 }, audience: b.audience, expires_at: iso(b.expires_in_days * DAY), created_at: iso(),
      created_by: "researcher", created_via: "ui",
    };
    state.handoffs[code] = { m, revoked_at: null, shares: b.audience === "shared" ? b.shares ?? [] : [] };
    return HttpResponse.json(m, { status: 201 });
  }),
  http.get(url("/handoffs"), () =>
    HttpResponse.json(Object.values(state.handoffs).map(row).sort((a, b) => b.created_at.localeCompare(a.created_at)))),
  http.get(url("/handoffs/:code"), ({ params }) => {
    const h = find(String(params.code));
    if (h instanceof Response) return h;
    if (h.revoked_at) return err(410, "handoff revoked");
    if (h.m.expires_at <= iso()) return err(410, "handoff expired");
    return HttpResponse.json(h.m);
  }),
  http.patch(url("/handoffs/:code"), async ({ params, request }) => {
    const h = find(String(params.code));
    if (h instanceof Response) return h;
    if (h.revoked_at) return err(410, "handoff revoked");
    const p = (await request.json()) as PatchHandoffBody;
    const audience = p.audience ?? h.m.audience;
    const shares = p.shares ?? h.shares;
    if (audience === "shared" && !shares.length) return err(422, "shared audience needs at least one share");
    if (p.expires_in_days !== undefined && !(p.expires_in_days >= 1 && p.expires_in_days <= 90)) return err(422, "expires_in_days must be 1-90");
    Object.assign(h.m, {
      ...(p.title !== undefined && { title: p.title }), ...(p.goal !== undefined && { goal: p.goal }), ...(p.brief !== undefined && { brief: p.brief }),
      audience, ...(p.expires_in_days !== undefined && { expires_at: iso(p.expires_in_days * DAY) }),
    });
    h.shares = audience === "shared" ? shares : [];
    return HttpResponse.json(h.m);
  }),
  http.delete(url("/handoffs/:code"), ({ params }) => {
    const h = find(String(params.code));
    if (h instanceof Response) return h;
    h.revoked_at ??= iso();
    return new HttpResponse(null, { status: 204 });
  }),
];
