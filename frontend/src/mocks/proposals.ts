import { http, HttpResponse } from "msw";
import { API_BASE } from "@/api/client";
import { NOTE_BODY_MAX, type NoteIn } from "@/api/notes";
import type { ApproveBody, Proposal, ProposalPayload } from "@/features/proposals/api";
import { DOCS } from "./docs";
import { addNote, anchorOn } from "./notes";
import { state } from "./state";

const url = (p: string) => `${API_BASE}${p}`;
const err = (status: number, detail: string) => HttpResponse.json({ detail }, { status });
const CLIENT = "claude-code";

/** Three pending proposals from one agent session: an anchored finding, a relation and a comment. */
function seed(): Proposal[] {
  const mk = (id: string, kind: Proposal["kind"], doc: number, payload: Omit<ProposalPayload, "document_id">, at: string): Proposal => ({
    id, kind, client: CLIENT, status: "pending", annotation_id: null, created_at: at, decided_at: null,
    payload: { document_id: DOCS[doc].id, visibility: "private", ...payload },
    document: { id: DOCS[doc].id, title: DOCS[doc].title },
  });
  const anchor = anchorOn(0, 3, 1);
  return [
    mk("p-finding", "note", 0, { kind: "finding", body: "Re-ran the k = 3 sweep to n = 2·10⁵: the gap stays ~6% above the bound.\n<b>Raw</b> numbers are in the run log.", anchor }, "2026-11-15T09:40:00Z"),
    mk("p-relation", "relation", 2, { kind: "relation", body: "Uses the same trace-method estimate as the spectral gap paper.", relation: { type: "extends", target_document_id: DOCS[0].id } }, "2026-11-15T09:39:00Z"),
    mk("p-comment", "note", 1, { kind: "comment", body: "Section 4 restates Lemma 2 without the polylog factor." }, "2026-11-15T09:38:00Z"),
  ];
}

const all = () => (state.proposals ??= seed());

function decide(id: string, edits: ApproveBody | null): Proposal | Response {
  const p = all().find((x) => x.id === id);
  if (!p) return err(404, "not found");
  if (p.status !== "pending") return err(409, `proposal already ${p.status}`);
  if (edits?.body !== undefined && !(edits.body.length >= 1 && edits.body.length <= NOTE_BODY_MAX)) return err(422, "body: must be 1-10000 characters");
  const { document_id, parent_id: _p, ...fields } = p.payload;
  const n = addNote(document_id, { ...fields, kind: fields.kind ?? "comment", ...edits } as NoteIn, { agent_assisted: true });
  Object.assign(p, { status: "approved", annotation_id: n.id, decided_at: new Date().toISOString() });
  return p;
}

export const proposalHandlers = [
  http.get(url("/me/proposals"), ({ request }) => {
    const s = new URL(request.url).searchParams.get("status");
    const rows = all().filter((p) => !s || p.status === s);
    return HttpResponse.json({ total: rows.length, items: rows.slice(0, 200) });
  }),
  http.post(url("/proposals/bulk-approve"), async ({ request }) => {
    const { ids } = (await request.json()) as { ids: string[] };
    if (ids.length > 100) return err(422, "ids: at most 100 items");
    const out = { approved: [] as { proposal_id: string; annotation_id: string }[], failed: [] as { proposal_id: string; detail: string }[] };
    for (const id of ids) {
      const r = decide(id, null);
      if (r instanceof Response) out.failed.push({ proposal_id: id, detail: ((await r.json()) as { detail: string }).detail });
      else out.approved.push({ proposal_id: id, annotation_id: r.annotation_id! });
    }
    return HttpResponse.json(out);
  }),
  http.post(url("/proposals/:id/approve"), async ({ params, request }) => {
    const text = await request.text();
    const r = decide(String(params.id), text ? (JSON.parse(text) as ApproveBody) : null);
    if (r instanceof Response) return r;
    const note = state.notes!.find((n) => n.id === r.annotation_id)!;
    return HttpResponse.json(note);
  }),
  http.post(url("/proposals/:id/reject"), ({ params }) => {
    const p = all().find((x) => x.id === params.id);
    if (!p) return err(404, "not found");
    if (p.status !== "pending") return err(409, `proposal already ${p.status}`);
    Object.assign(p, { status: "rejected", decided_at: new Date().toISOString() });
    return new HttpResponse(null, { status: 204 });
  }),
];
