import { http, HttpResponse } from "msw";
import { API_BASE } from "@/api/client";
import type { CitedByItem, Collection, DocumentDetail, NearDupAction, ReferenceItem, VoteRequest } from "@/api/types";
import { DOCS } from "./docs";
import { buildPdf, docById, docling } from "./pdf";
import { citedBy, COLLECTION_NAMES, ownerOf, runSearch, tagsOf, votesOf } from "./search";
import { state } from "./state";
import { adminHandlers } from "./admin";
import { chatHandlers } from "./chat";
import { agentHandlers } from "./agents";
import { noteHandlers, withCommunityNotes } from "./notes";
import { advance, newUpload } from "./uploads";
import { proposalHandlers } from "./proposals";
import { notificationHandlers } from "./notifications";

const url = (p: string) => `${API_BASE}${p}`;
const notFound = () => HttpResponse.json({ detail: "not found" }, { status: 404 });
const MAX_BYTES = 500 * 1024 * 1024;

function detail(id: string): DocumentDetail | null {
  const d = docById(id);
  if (!d) return null;
  const ver = state.versions[id];
  return {
    id, title: d.title, authors: d.authors, year: d.year, venue: d.venue, org_unit: d.org, visibility: "everyone",
    owner: { id: ownerOf(d), username: ownerOf(d) === "u-me" ? "researcher" : "k.ito" }, summary: d.pages[0][0], stage: "searchable", tags: tagsOf(d),
    votes: votesOf(d), file_type: d.type, page_count: d.pages.length, flag: d.flag ?? null,
    versions: Array.from({ length: ver }, (_, i) => ({ id: `${id}-v${ver - i}`, version: ver - i, filename: `${id}-v${ver - i}.pdf`, uploaded_at: "2026-10-01T10:00:00Z", is_latest: i === 0 })),
  };
}

export const handlers = [
  http.get(url("/me"), () => HttpResponse.json({ id: "u-me", username: "researcher", groups: ["/Mathematics/Spectral Theory"], is_admin: state.admin })),
  http.get(url("/search"), ({ request }) => {
    const sp = new URL(request.url).searchParams;
    return HttpResponse.json(withCommunityNotes(runSearch(sp), sp.get("notes") !== "false"));
  }),
  http.get(url("/collections"), () => {
    return HttpResponse.json(COLLECTION_NAMES.map((name, i): Collection => ({ id: `c${i + 1}`, name, count: DOCS.filter((d) => d.collections.includes(name)).length })));
  }),
  http.get(url("/documents/:id"), ({ params }) => {
    const d = detail(String(params.id));
    return d ? HttpResponse.json(d) : notFound();
  }),
  http.put(url("/documents/:id/vote"), async ({ params, request }) => {
    const d = docById(String(params.id));
    if (!d) return notFound();
    state.votes[d.id] = ((await request.json()) as VoteRequest).value;
    return HttpResponse.json(votesOf(d));
  }),
  http.post(url("/documents/:id/tags"), async ({ params, request }) => {
    const d = docById(String(params.id));
    if (!d) return notFound();
    const tag = String(((await request.json()) as { tag: string }).tag).toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "").slice(0, 40);
    if (tag && !tagsOf(d).includes(tag)) (state.extraTags[d.id] ??= []).push(tag);
    return HttpResponse.json({ tag, tags: tagsOf(d) });
  }),
  http.delete(url("/documents/:id/tags/:tag"), ({ params }) => {
    const d = docById(String(params.id));
    if (!d) return notFound();
    if (ownerOf(d) !== "u-me") return HttpResponse.json({ detail: "forbidden" }, { status: 403 });
    (state.removedTags[d.id] ??= []).push(String(params.tag));
    return HttpResponse.json({ tags: tagsOf(d) });
  }),
  http.get(url("/documents/:id/references"), ({ params }) => {
    const d = docById(String(params.id));
    if (!d) return notFound();
    return HttpResponse.json(d.refs.map((r, i): ReferenceItem => {
      const t = r.id ? docById(r.id) : undefined;
      return { ref_no: i + 1, raw: t ? `${t.authors.join(", ")}. ${t.title}. ${t.venue}, ${t.year}.` : (r.t ?? ""), target: t ? { doc_id: t.id, title: t.title } : null };
    }));
  }),
  http.get(url("/documents/:id/cited-by"), ({ params }) =>
    HttpResponse.json(citedBy(String(params.id)).map((d): CitedByItem => ({ doc_id: d.id, title: d.title, authors: d.authors, year: d.year, venue: d.venue })))),
  http.get(url("/documents/:id/versions/:n/file"), ({ params }) => {
    const d = docById(String(params.id));
    if (!d) return notFound();
    return new HttpResponse(buildPdf(d) as BodyInit, { headers: { "Content-Type": "application/pdf", "Content-Disposition": `attachment; filename="${d.id}.pdf"` } });
  }),
  http.get(url("/documents/:id/versions/:n/docling"), ({ params }) => {
    const d = docById(String(params.id));
    return d ? HttpResponse.json(docling(d)) : notFound();
  }),
  // ---- uploads ----
  http.get(url("/me/uploads"), () => {
    if (Date.now() - state.lastAdvance >= 3000) { advance(state.uploads); state.lastAdvance = Date.now(); }
    return HttpResponse.json({ items: state.uploads, total: state.uploads.length });
  }),
  http.post(url("/documents"), async ({ request }) => {
    const form = await request.formData();
    const file = form.get("file") as File;
    if (!file || !file.size) return HttpResponse.json({ detail: "empty file" }, { status: 400 });
    if (file.size > MAX_BYTES) return HttpResponse.json({ detail: "file exceeds 500 MB limit" }, { status: 413 });
    if (file.name === "spectral-gap-v3.pdf" && form.get("force") !== "true")
      return HttpResponse.json({ detail: { code: "duplicate", document_id: "00000000-0000-4000-8000-000000000001" } }, { status: 409 });
    const u = newUpload(file, (form.get("visibility") as "everyone") ?? "everyone");
    state.uploads.unshift(u);
    return HttpResponse.json({ document_id: u.document_id, version_id: u.version_id, sha256: "0".repeat(64) }, { status: 201 });
  }),
  http.post(url("/documents/:id/versions/:vid/near-dup"), async ({ params, request }) => {
    const u = state.uploads.find((x) => x.version_id === params.vid);
    if (!u) return notFound();
    if (u.stage !== "review") return HttpResponse.json({ detail: "not awaiting a decision" }, { status: 409 });
    const { action } = (await request.json()) as { action: NearDupAction };
    const target = u.near_dup_of?.document_id;
    if (action === "cancel") { u.stage = "cancelled"; u.status = "done"; }
    else {
      if (action === "new_version" && target) state.versions[target]++;
      u.stage = "deduped"; u.status = "running"; u.near_dup_of = null;
    }
    return HttpResponse.json({ version_id: u.version_id, document_id: u.document_id, stage: u.stage });
  }),
  ...adminHandlers,
  ...chatHandlers,
  ...agentHandlers,
  ...noteHandlers,
  ...proposalHandlers,
  ...notificationHandlers,
];
