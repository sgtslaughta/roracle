import { http, HttpResponse } from "msw";
import { API_BASE } from "@/api/client";
import { EVIDENCE_MAX_BYTES, NOTE_BODY_MAX, STANCES, type CommunityNote, type Evidence, type EvidenceMeta, type Note, type NoteIn, type NotePatch } from "@/api/notes";
import type { SearchResponse } from "@/api/types";
import { anchorBbox } from "@/features/notes/anchor";
import { DOCS } from "./docs";
import { docById, docling } from "./pdf";
import { ownerOf } from "./search";
import { state } from "./state";

const url = (p: string) => `${API_BASE}${p}`;
const err = (status: number, detail: string) => HttpResponse.json({ detail }, { status });
const ME = { id: "u-me", username: "researcher" };
const NO_VOTES = { up: 0, down: 0, mine: 0 as const, score: 0.5 };
const NO_EV = { supports: 0, refutes: 0, inconclusive: 0 };
let seq = 100;

function base(id: string, docIdx: number, author: Note["author"], kind: Note["kind"], body: string, extra: Partial<Note> = {}): Note {
  const d = DOCS[docIdx], v = state.versions[d.id];
  return {
    id, document_id: d.id, version_id: `${d.id}-v${v}`, version: v, parent_id: null, author, kind, body, anchor: null, relation: null,
    visibility: "everyone", shares: [], status: "visible", agent_assisted: false, may_not_apply_to_version: null,
    votes: NO_VOTES, evidence: NO_EV, created_at: "2026-10-02T09:00:00Z", updated_at: "2026-10-02T09:00:00Z", ...extra,
  };
}

/** Anchor on the mock PDF's real chunk box, so the highlight lines up with the drawn paragraph. */
function anchorOn(docIdx: number, page: number, para: number) {
  const d = DOCS[docIdx];
  const c = docling(d).chunks.find((x) => x.chunk_id.endsWith(`:${page - 1}:${para}`) && x.page_no === page)!;
  return { quote: d.pages[page - 1][para].slice(0, 1000), prefix: "", suffix: "", page, bbox: anchorBbox(c.bbox) };
}

const RUIZ = { id: "u-jruiz", username: "J. Ruiz" }, LIND = { id: "u-plind", username: "P. Lindqvist" }, ITO = { id: "u-owner", username: "k.ito" };

function seed(): Note[] {
  const d0 = DOCS[0].id;
  const replies = [
    [ME, "For n up to 10⁵ we saw it shift the gap by under 2%."],
    [RUIZ, "Same here with d = 4; the constant hides most of it."],
    [ITO, "Section 4 of my 2023 paper has a sharper estimate."],
    [LIND, "Thanks, that settles it for my use case."],
  ] as const;
  return [
    base("n-finding", 0, RUIZ, "finding",
      "Checked the conjectured hypergraph bound numerically for k = 3, d = 4 up to n = 10⁵. The observed gap sits **6% above** the (d−1)(k−1) prediction.",
      { anchor: anchorOn(0, 3, 1), agent_assisted: true, votes: { up: 14, down: 0, mine: 0, score: 0.78 }, evidence: { supports: 2, refutes: 0, inconclusive: 1 } }),
    base("n-question", 0, LIND, "question", "Does the polylog factor matter in practice for n below 10⁶?",
      { anchor: anchorOn(0, 2, 1), votes: { up: 4, down: 0, mine: 0, score: 0.51 } }),
    ...replies.map(([a, body], i) => base(`n-reply-${i + 1}`, 0, a, "comment", body, { parent_id: "n-question", created_at: `2026-10-0${3 + i}T09:00:00Z` })),
    base("n-mine", 0, ME, "comment", "Worth reading next to the [Friedman memoir](https://example.org/friedman).", { visibility: "private" }),
    base("n-stale", 0, LIND, "correction", "The constant in the tangle-free radius is off by a factor of two in this version.",
      { version_id: `${d0}-v1`, version: 1, may_not_apply_to_version: 2, anchor: { ...anchorOn(0, 2, 0), quote: "We prove it holds with probability 1 - O(n^-1/3)." } }),
    base("n-relation", 1, ITO, "relation", "Confirms the hypergraph analogue conjectured here, with a loss of a factor (k−1).",
      { relation: { type: "extends", target_document_id: d0 }, votes: { up: 9, down: 1, mine: 0, score: 0.6 } }),
  ];
}

const notes = () => (state.notes ??= seed());
const find = (id: string) => notes().find((n) => n.id === id);
/** Readable to the mock user: not removed; hidden only for its author, the document owner or an admin. */
const readable = (n: Note) => n.status !== "removed" && (n.status === "visible" || n.author.id === ME.id || state.admin || ownerOf(docById(n.document_id)!) === ME.id);
const out = (n: Note): Note => ({ ...n, shares: n.author.id === ME.id ? n.shares : [], evidence: evidenceCounts(n.id) });
const bodyOk = (b: unknown) => typeof b === "string" && b.length >= 1 && b.length <= NOTE_BODY_MAX;
const now = () => new Date().toISOString();

function getNote(id: string): Note | Response {
  const n = find(id);
  return n && readable(n) ? n : err(404, "not found");
}

export const noteHandlers = [
  ...evidenceHandlers(),
  http.get(url("/documents/:id/annotations"), ({ params }) => {
    if (!docById(String(params.id))) return err(404, "not found");
    return HttpResponse.json({ items: notes().filter((n) => n.document_id === params.id && readable(n)).map(out) });
  }),
  http.post(url("/documents/:id/annotations"), async ({ params, request }) => {
    const d = docById(String(params.id));
    if (!d) return err(404, "not found");
    const b = (await request.json()) as NoteIn;
    if (!bodyOk(b.body)) return err(422, "body must be 1-10000 characters");
    if ((b.kind === "relation") !== !!b.relation) return err(422, "relation is required for, and only for, kind 'relation'");
    if (b.relation?.target_document_id === d.id) return err(422, "relation target must differ from the document");
    const vis = b.visibility ?? "everyone";
    const n = base(`n-${++seq}`, DOCS.indexOf(d), ME, b.kind, b.body, {
      anchor: b.anchor ? { prefix: "", suffix: "", page: null, bbox: null, ...b.anchor } : null, relation: b.relation ?? null,
      visibility: vis, shares: vis === "shared" ? b.shares ?? [] : [], created_at: now(), updated_at: now(),
    });
    notes().push(n);
    return HttpResponse.json(out(n), { status: 201 });
  }),
  http.get(url("/annotations/:id"), ({ params }) => {
    const n = getNote(String(params.id));
    return n instanceof Response ? n : HttpResponse.json(out(n));
  }),
  http.patch(url("/annotations/:id"), async ({ params, request }) => {
    const n = getNote(String(params.id));
    if (n instanceof Response) return n;
    if (n.author.id !== ME.id) return err(403, "only the author may change this note");
    const p = (await request.json()) as NotePatch;
    if (p.body !== undefined && !bodyOk(p.body)) return err(422, "body must be 1-10000 characters");
    if (n.parent_id && (p.visibility || p.shares)) return err(422, "replies follow their thread's visibility");
    if (p.body !== undefined && p.body !== n.body) Object.assign(n, { body: p.body, votes: NO_VOTES });
    if (p.visibility) n.visibility = p.visibility;
    if (n.visibility !== "shared") n.shares = [];
    else if (p.shares) n.shares = p.shares;
    n.updated_at = now();
    return HttpResponse.json(out(n));
  }),
  http.delete(url("/annotations/:id"), ({ params }) => {
    const n = getNote(String(params.id));
    if (n instanceof Response) return n;
    if (n.author.id !== ME.id) return err(403, "only the author may change this note");
    state.notes = notes().filter((x) => x.id !== n.id && x.parent_id !== n.id);
    return new HttpResponse(null, { status: 204 });
  }),
  http.post(url("/annotations/:id/replies"), async ({ params, request }) => {
    const p = getNote(String(params.id));
    if (p instanceof Response) return p;
    const { body } = (await request.json()) as { body: string };
    if (!bodyOk(body)) return err(422, "body must be 1-10000 characters");
    const root = p.parent_id ? find(p.parent_id)! : p;
    const n = base(`n-${++seq}`, DOCS.findIndex((d) => d.id === root.document_id), ME, "comment", body, {
      parent_id: root.id, version_id: root.version_id, version: root.version, visibility: root.visibility, created_at: now(), updated_at: now(),
    });
    notes().push(n);
    return HttpResponse.json(out(n), { status: 201 });
  }),
  http.put(url("/annotations/:id/vote"), async ({ params, request }) => {
    const n = getNote(String(params.id));
    if (n instanceof Response) return n;
    if (n.author.id === ME.id) return err(422, "cannot vote on your own note");
    const { value } = (await request.json()) as { value: 1 | -1 | 0 };
    const v = n.votes, up = v.up - (v.mine > 0 ? 1 : 0) + (value > 0 ? 1 : 0), down = v.down - (v.mine < 0 ? 1 : 0) + (value < 0 ? 1 : 0);
    n.votes = { up, down, mine: value, score: (up + 1) / (up + down + 2) };
    return HttpResponse.json(n.votes);
  }),
  http.post(url("/annotations/:id/report"), ({ params }) => {
    const n = getNote(String(params.id));
    return n instanceof Response ? n : new HttpResponse(null, { status: 204 });
  }),
  http.post(url("/annotations/:id/hide"), async ({ params, request }) => {
    const n = getNote(String(params.id));
    if (n instanceof Response) return n;
    if (!(state.admin || ownerOf(docById(n.document_id)!) === ME.id)) return err(403, "only the document owner or an admin may hide notes");
    n.status = ((await request.json()) as { hidden: boolean }).hidden ? "hidden" : "visible";
    return HttpResponse.json(out(n));
  }),
  http.delete(url("/admin/annotations/:id"), ({ params }) => {
    if (!state.admin) return err(403, "admin only");
    const n = getNote(String(params.id));
    if (n instanceof Response) return n;
    n.status = "removed";
    return new HttpResponse(null, { status: 204 });
  }),
];

/* ---- evidence: seeded on the agent finding (clean file, infected file, link only) ---- */
type MockEvidence = NonNullable<typeof state.evidence>[number];
const H = (c: string) => c.repeat(64).slice(0, 64);

/** Same RO-Crate shape as the backend's notes/evidence.py crate(). */
function crate(m: EvidenceMeta) {
  const items = (xs: EvidenceMeta["inputs"]) => xs.map((i) => ({ "@type": "File", name: i.name, ...(i.uri ? { url: i.uri } : {}), ...(i.sha256 ? { sha256: i.sha256 } : {}) }));
  return { "@context": "https://w3id.org/ro/crate/1.1/context", "@graph": [
    { "@id": "./", "@type": "Dataset", name: "rOracle evidence", description: m.description, ...(m.uri ? { url: m.uri, sha256: m.uri_sha256 } : {}) },
    { "@id": "#run", "@type": "CreateAction", startTime: m.started_at, endTime: m.ended_at, object: items(m.inputs), result: items(m.outputs),
      environment: { "@type": "PropertyValue", name: "image_digest", value: m.environment.image_digest }, parameters: m.parameters, seed: m.seed },
    { "@id": "#code", "@type": "SoftwareSourceCode", codeRepository: m.code.repo, version: m.code.commit },
    { "@id": "#agent", "@type": "SoftwareApplication", name: m.agent.client, version: m.agent.model },
  ] };
}

const META: EvidenceMeta = {
  stance: "supports", code: { repo: "https://git.lab/spectral/sweeps", commit: "4e1c9a2" }, environment: { image_digest: `sha256:${H("9f2c")}` },
  inputs: [{ name: "graphs_k3_d4.npz", sha256: H("a1") }], outputs: Array.from({ length: 6 }, (_, i) => ({ name: `gap_sweep_${i + 1}.parquet` })),
  parameters: { k: 3, d: 4, n_max: 100000, trials: 200 }, seed: 1729, agent: { client: "claude-code", model: "opus" },
  started_at: "2026-10-01T08:00:00Z", ended_at: "2026-10-01T09:42:00Z", description: "Spectral gap sweep.\nSee sweep.ipynb.", uri: null, uri_sha256: null,
};
function ev(id: string, m: EvidenceMeta, by: string, extra: Partial<MockEvidence>): MockEvidence {
  return { id, annotation_id: "n-finding", stance: m.stance, crate: crate(m), file: null, uri: m.uri, uri_sha256: m.uri_sha256,
    reproductions: { reproduced: 0, failed: 0, mine: null }, created_by: by, created_at: "2026-10-02T10:00:00Z", ...extra };
}
const seedEvidence = (): MockEvidence[] => [
  ev("ev-clean", META, RUIZ.id, { file: { filename: "gap_sweep.tar", size: 48_213, sha256: H("c3"), scan_status: "clean" }, content: "mock evidence archive\n", reproductions: { reproduced: 2, failed: 0, mine: null } }),
  ev("ev-infected", META, RUIZ.id, { file: { filename: "sweep.ipynb", size: 9_120, sha256: H("d4"), scan_status: "infected" } }),
  ev("ev-uri", { ...META, stance: "inconclusive", uri: "s3://lab-runs/sweep-2026-10.tar", uri_sha256: H("e5"), description: "" }, ME.id, { reproductions: { reproduced: 0, failed: 1, mine: null } }),
];
const evidence = () => (state.evidence ??= seedEvidence());
function evidenceCounts(noteId: string): Note["evidence"] {
  const c = { supports: 0, refutes: 0, inconclusive: 0 };
  for (const e of evidence()) if (e.annotation_id === noteId) c[e.stance]++;
  return c;
}
/** A pending scan turns clean a few seconds after upload (a file named *eicar* turns infected). */
function evOut({ content: _c, ready, ...e }: MockEvidence): Evidence {
  if (e.file?.scan_status === "pending" && ready && Date.now() >= ready) e.file = { ...e.file, scan_status: /eicar/i.test(e.file.filename ?? "") ? "infected" : "clean" };
  return e;
}
function getEvidence(id: string): MockEvidence | Response {
  const e = evidence().find((x) => x.id === id);
  if (!e) return err(404, "not found");
  const n = getNote(e.annotation_id);
  if (n instanceof Response) return n;
  if (e.file?.scan_status === "pending" && e.ready && Date.now() >= e.ready) Object.assign(e, evOut(e));
  return e;
}

function evidenceHandlers() {
  return [
    http.get(url("/annotations/:id/evidence"), ({ params }) => {
      const n = getNote(String(params.id));
      return n instanceof Response ? n : HttpResponse.json({ items: evidence().filter((e) => e.annotation_id === n.id).map(evOut) });
    }),
    http.post(url("/annotations/:id/evidence"), async ({ params, request }) => {
      const n = getNote(String(params.id));
      if (n instanceof Response) return n;
      if (n.kind !== "finding") return err(422, "evidence requires a finding note");
      const form = await request.formData();
      let m: EvidenceMeta;
      try { m = JSON.parse(String(form.get("meta"))) as EvidenceMeta; } catch { return err(422, "meta must be JSON"); }
      if (!STANCES.includes(m.stance) || !m.code?.repo || !m.started_at || m.started_at > m.ended_at) return err(422, "invalid evidence metadata");
      if ((m.uri == null) !== (m.uri_sha256 == null)) return err(422, "uri and uri_sha256 must be given together");
      const f = form.get("file");
      const file = f instanceof File ? f : null;
      if (file && m.uri) return err(422, "provide either a file or a uri, not both");
      if (file && !file.size) return err(400, "empty file");
      if (file && file.size > EVIDENCE_MAX_BYTES) return err(413, "file exceeds the evidence size limit");
      const e = ev(`ev-${++seq}`, m, ME.id, { annotation_id: n.id, created_at: now(),
        ...(file ? { file: { filename: file.name, size: file.size, sha256: H("f6"), scan_status: "pending" as const }, content: await file.text(), ready: Date.now() + 3000 } : {}) });
      evidence().push(e);
      return HttpResponse.json(evOut(e), { status: 201 });
    }),
    http.get(url("/evidence/:id"), ({ params }) => {
      const e = getEvidence(String(params.id));
      return e instanceof Response ? e : HttpResponse.json(evOut(e));
    }),
    http.get(url("/evidence/:id/file"), ({ params }) => {
      const e = getEvidence(String(params.id));
      if (e instanceof Response) return e;
      if (!e.file || !(e.file.scan_status === "clean" || e.file.scan_status === "unscanned")) return err(409, "not available");
      return new HttpResponse(e.content ?? "", { headers: {
        "Content-Type": "application/octet-stream", "Content-Disposition": `attachment; filename="${e.file.filename}"`, "X-Content-Type-Options": "nosniff",
      } });
    }),
    http.put(url("/evidence/:id/reproduction"), async ({ params, request }) => {
      const e = getEvidence(String(params.id));
      if (e instanceof Response) return e;
      const { outcome, note } = (await request.json()) as { outcome: "reproduced" | "failed" | null; note: string };
      if ((note ?? "").length > 2000) return err(422, "note too long");
      const r = { ...e.reproductions };
      if (r.mine) r[r.mine]--;
      if (outcome) r[outcome]++;
      e.reproductions = { ...r, mine: outcome };
      return HttpResponse.json(evOut(e));
    }),
    http.delete(url("/evidence/:id"), ({ params }) => {
      const e = getEvidence(String(params.id));
      if (e instanceof Response) return e;
      if (e.created_by !== ME.id && !state.admin) return err(403, "only the creator or an admin can delete evidence");
      state.evidence = evidence().filter((x) => x !== e);
      return new HttpResponse(null, { status: 204 });
    }),
  ];
}

/* ---- community notes on search hits (backend notes/community.py): everyone-visible top-level notes on the
   latest version with score >= 0.4; up to 2 per document and 6 per page, best first, body cut to 500 ---- */
export function withCommunityNotes(res: SearchResponse, on: boolean): SearchResponse {
  let left = 6;
  const pick = (docId: string): CommunityNote[] => {
    if (!on) return [];
    const ns = notes()
      .filter((n) => n.document_id === docId && !n.parent_id && n.status === "visible" && n.visibility === "everyone"
        && n.version === state.versions[docId] && n.may_not_apply_to_version == null && n.votes.score >= 0.4)
      .sort((a, b) => b.votes.score - a.votes.score)
      .slice(0, Math.min(2, left));
    left -= ns.length;
    return ns.map((n) => ({ id: n.id, kind: n.kind, body: n.body.slice(0, 500), author: n.author, score: n.votes.score, page: n.anchor?.page ?? null }));
  };
  return { ...res, results: res.results.map((h) => ({ ...h, notes: pick(h.doc_id) })) };
}
