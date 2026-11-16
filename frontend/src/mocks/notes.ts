import { http, HttpResponse } from "msw";
import { API_BASE } from "@/api/client";
import { NOTE_BODY_MAX, type Note, type NoteIn, type NotePatch } from "@/api/notes";
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
const out = (n: Note): Note => ({ ...n, shares: n.author.id === ME.id ? n.shares : [] });
const bodyOk = (b: unknown) => typeof b === "string" && b.length >= 1 && b.length <= NOTE_BODY_MAX;
const now = () => new Date().toISOString();

function getNote(id: string): Note | Response {
  const n = find(id);
  return n && readable(n) ? n : err(404, "not found");
}

export const noteHandlers = [
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
