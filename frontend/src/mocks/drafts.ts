import { http, HttpResponse } from "msw";
import { API_BASE } from "@/api/client";
import type { Citations, DraftDetail, DraftSummary, Validation, VersionRef } from "@/api/drafts";
import { DOCS } from "./docs";
import { buildPdf } from "./pdf";
import { state } from "./state";

const url = (p: string) => `${API_BASE}${p}`;
const err = (status: number, detail: unknown) => HttpResponse.json({ detail }, { status });
const ME = { id: "u-me", username: "researcher" };
const ANA = { id: "11111111-1111-4111-8111-111111111111", username: "ana" };
const USERS = [ME, ANA, { id: "22222222-2222-4222-8222-222222222222", username: "ben" }];
export const D1 = "d0000001-0000-4000-8000-000000000001";
export const D2 = "d0000002-0000-4000-8000-000000000002";
export const D3 = "d0000003-0000-4000-8000-000000000003";

export interface MockDraft extends DraftSummary { vers: (VersionRef & { content: string })[]; reports: { validation?: Validation; citations?: Citations }; acks: string[] }

const AI = `ai_assistance: {"used": false, "tools": [], "description": ""}`;
function mk(id: string, title: string, status: DraftSummary["status"], owner: MockDraft["owner"], texts: string[], extra: Partial<MockDraft> = {}): MockDraft {
  const vers = texts.map((content, i) => ({ version: i + 1, author: owner, created_at: `2026-11-1${i}T10:00:00Z`, content }));
  return { id, title, profile: "journal-article", status, owner, coauthors: { users: [], groups: [] }, latest_version: vers.length, updated_at: "2026-11-14T10:00:00Z", review_note: null, vers, reports: {}, acks: [], ...extra };
}
const seed = (): MockDraft[] => [
  mk(D1, "Spectral gaps of random hypergraphs", "draft", ME, ["# Intro\n\nFirst pass.", "# Intro\n\nSecond pass with a claim [@spectral-gap-v3]."]),
  mk(D2, "Trace methods survey", "in_review", ME, [`---\n${AI}\n---\n# Survey`]),
  mk(D3, "Joint grant proposal", "draft", ANA, ["# Aims"], { coauthors: { users: [ME.id], groups: [] }, review_note: "Tighten the aims." }),
];
const all = () => (state.drafts ??= seed()) as MockDraft[];
const find = (id: unknown) => all().find((d) => d.id === id);
const summary = ({ vers: _v, reports: _r, acks: _a, ...s }: MockDraft): DraftSummary => s;
const detail = (d: MockDraft): DraftDetail => ({ ...summary(d), content: d.vers.at(-1)!.content, versions: d.vers.map(({ content: _c, ...v }) => v).reverse() });
const latest = (d: MockDraft) => d.vers.at(-1)!;
const hasAi = (d: MockDraft) => /^ai_assistance:/m.test(latest(d).content);
const hashOf = (s: string) => Array.from({ length: 64 }, (_, i) => "0123456789abcdef"[(s.charCodeAt(i % s.length) + i) % 16]).join("");
const isAuthor = (d: MockDraft) => d.owner.id === ME.id || d.coauthors.users.includes(ME.id);
const SENTENCES = ["Spectral gaps shrink with density.", "The bound is tight for k = 3.", "See also the unpublished notes."];

function citations(d: MockDraft): Citations {
  const items: Citations["items"] = [
    { key: "spectral-gap-v3", sentence: SENTENCES[0], sentence_hash: hashOf(SENTENCES[0]), status: "resolved", verdict: "supported", reason: "Matches section 2.", chunk_id: "c1", doc_id: DOCS[0].id, title: DOCS[0].title, acknowledged: false },
    { key: "weak-claim", sentence: SENTENCES[1], sentence_hash: hashOf(SENTENCES[1]), status: "resolved", verdict: "unsupported", reason: "The cited text says k = 2 only.", chunk_id: "c2", doc_id: DOCS[1].id, title: DOCS[1].title, acknowledged: false },
    { key: "ghost", sentence: SENTENCES[2], sentence_hash: hashOf(SENTENCES[2]), status: "unresolved", verdict: null, reason: "No such document.", chunk_id: null, doc_id: null, title: null, acknowledged: false },
  ].map((i) => ({ ...i, acknowledged: d.acks.includes(`${i.key}:${i.sentence_hash}`) })) as Citations["items"];
  return { version: d.latest_version, checked_by: ME.id, ok: items.every((i) => i.status === "resolved" && (i.verdict === "supported" || i.acknowledged)), items };
}

export const draftHandlers = [
  http.get(url("/drafts"), () => HttpResponse.json({ items: all().filter((d) => d.owner.id === ME.id || isAuthor(d)).map(summary) })),
  http.post(url("/drafts"), async ({ request }) => {
    const b = (await request.json()) as { title?: string; profile?: string };
    const title = (b.title ?? "").trim();
    if (!title) return err(422, [{ loc: ["body", "title"], msg: "String should have at least 1 character", type: "string_too_short" }]);
    if (b.profile && !/^[a-z-]+$/.test(b.profile)) return err(422, "unknown profile");
    const d = mk(`d00000${10 + all().length}-0000-4000-8000-000000000000`, title, "draft", ME, ["# " + title], { profile: b.profile ?? null });
    all().unshift(d);
    return HttpResponse.json(summary(d), { status: 201 });
  }),
  http.get(url("/drafts/:id"), ({ params }) => { const d = find(params.id); return d ? HttpResponse.json(detail(d)) : err(404, "not found"); }),
  http.get(url("/drafts/:id/versions/:n"), ({ params }) => {
    const v = find(params.id)?.vers.find((x) => String(x.version) === params.n);
    return v ? HttpResponse.json(v) : err(404, "not found");
  }),
  http.put(url("/drafts/:id/content"), async ({ params, request }) => {
    const d = find(params.id);
    if (!d) return err(404, "not found");
    const b = (await request.json()) as { content: string; base_version?: number };
    if (d.status === "in_review") return err(409, "draft is in review");
    if (b.base_version !== undefined && b.base_version !== d.latest_version) return err(409, { code: "draft_changed", version: d.latest_version });
    const v = { version: d.latest_version + 1, author: ME, created_at: new Date().toISOString(), content: b.content };
    d.vers.push(v);
    d.latest_version = v.version;
    return HttpResponse.json({ version: v.version, created_at: v.created_at });
  }),
  http.put(url("/drafts/:id/coauthors"), async ({ params, request }) => {
    const d = find(params.id);
    if (!d) return err(404, "not found");
    if (d.owner.id !== ME.id) return err(403, "owner only");
    const b = (await request.json()) as { users: string[]; groups: string[] };
    if (b.users.some((u) => !USERS.some((x) => x.id === u))) return err(422, "unknown user");
    d.coauthors = { users: b.users, groups: b.groups };
    return HttpResponse.json({ coauthors: d.coauthors });
  }),
  http.delete(url("/drafts/:id"), ({ params }) => {
    const d = find(params.id);
    if (!d) return err(404, "not found");
    if (d.status === "submitted") return err(409, "draft already submitted");
    state.drafts = all().filter((x) => x !== d);
    return new HttpResponse(null, { status: 204 });
  }),
  http.post(url("/drafts/:id/validate"), ({ params }) => {
    const d = find(params.id);
    if (!d) return err(404, "not found");
    const blocking = hasAi(d) ? [] : [{ rule: "ai_disclosure", message: "Front matter needs ai_assistance.", line: null }];
    const v: Validation = { version: d.latest_version, profile: { name: d.profile ?? "journal-article", version: 0, source: "builtin" }, checked_by: ME.id, ok: !blocking.length, blocking, warnings: [{ rule: "abstract_length", message: "Abstract is short.", line: 3 }] };
    d.reports.validation = v;
    return HttpResponse.json(v);
  }),
  http.post(url("/drafts/:id/check-citations"), ({ params }) => {
    const d = find(params.id);
    if (!d) return err(404, "not found");
    if (!latest(d).content.trim()) return err(409, "draft has no content");
    return HttpResponse.json((d.reports.citations = citations(d)));
  }),
  http.post(url("/drafts/:id/citations/ack"), async ({ params, request }) => {
    const d = find(params.id);
    if (!d) return err(404, "not found");
    if (d.reports.citations?.version !== d.latest_version) return err(409, { code: "checks_required" });
    const b = (await request.json()) as { key: string; sentence_hash: string; reason: string };
    if (b.key === "ghost") return err(422, "citation is not acknowledgeable");
    d.acks.push(`${b.key}:${b.sentence_hash}`);
    return HttpResponse.json((d.reports.citations = citations(d)));
  }),
  http.delete(url("/drafts/:id/citations/ack"), async ({ params, request }) => {
    const d = find(params.id);
    if (!d) return err(404, "not found");
    const b = (await request.json()) as { key: string; sentence_hash: string };
    d.acks = d.acks.filter((a) => a !== `${b.key}:${b.sentence_hash}`);
    return HttpResponse.json((d.reports.citations = citations(d)));
  }),
  http.post(url("/drafts/:id/build"), ({ params }) => {
    const d = find(params.id);
    if (!d) return err(404, "not found");
    if (/format:\s*latex/.test(latest(d).content)) return err(501, "LaTeX builds are not enabled");
    return new HttpResponse(buildPdf(DOCS[0]) as BodyInit, { headers: { "Content-Type": "application/pdf" } });
  }),
  http.post(url("/drafts/:id/submit"), async ({ params, request }) => {
    const d = find(params.id);
    if (!d) return err(404, "not found");
    if (d.owner.id !== ME.id) return err(403, "owner only");
    await request.json();
    if (d.status === "in_review") return err(409, { code: "in_review" });
    const code = d.reports.validation?.version !== d.latest_version || d.reports.citations?.version !== d.latest_version ? "checks_required"
      : !d.reports.validation.ok ? "blocking_findings" : !hasAi(d) ? "ai_disclosure_required" : !citations(d).ok ? "citations_unresolved" : null;
    if (code) return err(409, { code });
    d.status = "submitted";
    return HttpResponse.json({ status: "submitted", version: d.latest_version, document_version: 1 });
  }),
];
