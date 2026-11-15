import { http, HttpResponse } from "msw";
import { API_BASE } from "@/api/client";
import type { Chat, ChatDetail, ChatMessage, Citation, SendMessageBody, Viewer } from "@/api/chat";
import { DOCS } from "./docs";
import { passageOf, seedChats } from "./chatSeed";
import { state } from "./state";

const url = (p: string) => `${API_BASE}${p}`;
const MODELS = ["frontier-large", "open-70b", "open-8b-fast"];
const NONE = "None of the documents you can see mention that. Try broader terms, or search all documents you can see.";
const notFound = () => HttpResponse.json({ detail: "not found" }, { status: 404 });
const now = () => new Date().toISOString();
const tick = () => new Promise<void>((r) => setTimeout(r, import.meta.env.MODE === "test" ? 0 : 30));
const plain = (c: ChatDetail): Chat => ({ id: c.id, title: c.title, scope: c.scope, model: c.model, pins: c.pins, excludes: c.excludes, created_at: c.created_at, updated_at: c.updated_at });
const chats = () => (state.chats ??= seedChats());
const unprocessable = (detail: string) => HttpResponse.json({ detail }, { status: 422 });
const MAX_CHARS = 4000;
const PAGE = 50;
/** Opaque cursor = position in the (updated_at desc, id) order. */
const cursorOf = (c: Chat) => btoa(`${c.updated_at}|${c.id}`);
const afterCursor = (cur: string): ((c: Chat) => boolean) | null => {
  try {
    const [t, id] = atob(cur).split("|");
    return t && id ? (c) => c.updated_at < t || (c.updated_at === t && c.id < id) : null;
  } catch { return null; }
};
const find = (id: unknown) => chats().find((c) => c.id === id);

/** Pick up to 3 paragraphs sharing words with the question (viewer tabs preferred) and write a cited answer. */
export function buildAnswer(question: string, viewer: Viewer): { text: string; citations: Citation[] } {
  const terms = [...new Set(question.toLowerCase().match(/[a-z0-9]{4,}/g) ?? [])];
  const open = new Map(viewer.tabs.map((t) => [t.doc_id, t.page]));
  const scored: { di: number; p: number; c: number; s: number }[] = [];
  DOCS.forEach((d, di) => {
    if (viewer.ignored.includes(d.id)) return;
    d.pages.forEach((pg, p) => pg.forEach((t, c) => {
      const low = t.toLowerCase();
      let s = terms.filter((w) => low.includes(w)).length;
      if (s > 0 && open.has(d.id)) s += 1.5 + (open.get(d.id) === p + 1 ? 1 : 0);
      if (s > 0) scored.push({ di, p, c, s });
    }));
  });
  const top = scored.sort((a, b) => b.s - a.s).slice(0, 3);
  if (!top.length) return { text: NONE, citations: [] };
  const picks = top.map((x, i) => passageOf(DOCS[x.di], x.p, x.c, i + 1));
  const text = "From the documents you can see: " + picks.map((x, i) => `${x.retrieved.text} [${i + 1}]`).join(" ");
  return { text, citations: picks.map((x) => x.cite) };
}

function exportMd(c: ChatDetail): string {
  const refs: string[] = [];
  const body = c.messages.map((m) => {
    if (m.role === "user") return `**Q:** ${m.content}`;
    m.citations.forEach((x) => {
      if (x.available === false) return;
      const d = DOCS.find((y) => y.id === x.doc_id);
      refs.push(`[${x.n}] ${d?.authors.join(", ") ?? ""}, ${x.title}, ${d?.year ?? ""}, p. ${x.page}`);
    });
    return `**A:** ${m.content}`;
  });
  return `# ${c.title}\n\n${body.join("\n\n")}\n\n## References\n\n${refs.join("\n")}\n`;
}

function stream(chat: ChatDetail, body: SendMessageBody, signal: AbortSignal): Response {
  const enc = new TextEncoder();
  const ev = (e: string, d: unknown) => enc.encode(`event: ${e}\ndata: ${JSON.stringify(d)}\n\n`);
  const model = body.model ?? chat.model ?? MODELS[0];
  const { text, citations } = buildAnswer(body.content, body.viewer);
  const stamp = now();
  const base = chat.messages.length;
  chat.messages.push({ id: `${chat.id}-m${base + 1}`, role: "user", content: body.content, viewer: body.viewer, citations: [], retrieved: [], status: "complete", model: null, created_at: stamp });
  const aid = `${chat.id}-m${base + 2}`;
  let sent = "";
  let saved = false;
  const save = (status: ChatMessage["status"]) => {
    if (saved) return;
    saved = true;
    const done = status === "complete";
    const retrieved = citations.flatMap((c) => (c.available === false ? [] : [{ n: c.n, doc_id: c.doc_id, version_id: c.version_id, version: c.version, chunk_id: c.chunk_id, title: c.title, page: c.page, bbox: c.bbox, score: 0.9 - c.n * 0.1, text: c.quote, available: true as const }]));
    chat.messages.push({ id: aid, role: "assistant", content: done ? text : sent, citations: done ? citations : [], retrieved: done ? retrieved : [], status, model, created_at: now(), rating: null });
    chat.updated_at = now();
  };
  const body$ = new ReadableStream<Uint8Array>({
    async start(ctrl) {
      for (let i = 0; i < text.length; i += 12) {
        await tick();
        if (signal.aborted || saved) { save("interrupted"); return; }
        sent = text.slice(0, i + 12);
        ctrl.enqueue(ev("token", { text: text.slice(i, i + 12) }));
      }
      if (signal.aborted || saved) { save("interrupted"); return; }
      // saved complete before citations/done are sent: an abort after this point still leaves a complete message
      save("complete");
      if (citations.length) ctrl.enqueue(ev("citations", { items: citations }));
      ctrl.enqueue(ev("done", { message_id: aid }));
      ctrl.close();
    },
    cancel() { save("interrupted"); },
  });
  return new Response(body$, { headers: { "Content-Type": "text/event-stream", "Cache-Control": "no-cache" } });
}

export const chatHandlers = [
  http.get(url("/chats/models"), () => HttpResponse.json({ models: MODELS, default: MODELS[0] })),
  http.get(url("/chats"), ({ request }) => {
    const q = new URL(request.url).searchParams;
    const limit = Number(q.get("limit") ?? PAGE);
    if (!Number.isInteger(limit) || limit < 1 || limit > 100) return unprocessable("invalid limit");
    const before = q.get("before");
    const keep = before === null ? () => true : afterCursor(before);
    if (!keep) return unprocessable("invalid cursor");
    const rows = chats().filter((c) => c.is_owner).map(plain).sort((a, b) => b.updated_at.localeCompare(a.updated_at) || b.id.localeCompare(a.id)).filter(keep);
    const items = rows.slice(0, limit);
    return HttpResponse.json({ items, next: rows.length > limit ? cursorOf(items[items.length - 1]) : null });
  }),
  http.post(url("/chats"), async ({ request }) => {
    const b = ((await request.json().catch(() => ({}))) ?? {}) as Partial<Chat>;
    if (b.model && !MODELS.includes(b.model)) return unprocessable("unknown model");
    const t = now();
    const c: ChatDetail = { id: `c${++state.chatSeq}`, title: b.title ?? "New chat", scope: b.scope ?? "all", model: b.model ?? null, pins: [], excludes: [], created_at: t, updated_at: t, messages: [], is_owner: true };
    chats().push(c);
    return HttpResponse.json(plain(c), { status: 201 });
  }),
  http.get(url("/chats/:id"), ({ params }) => { const c = find(params.id); return c ? HttpResponse.json(c) : notFound(); }),
  http.patch(url("/chats/:id"), async ({ params, request }) => {
    const c = find(params.id);
    if (!c) return notFound();
    if (!c.is_owner) return HttpResponse.json({ detail: "read-only" }, { status: 403 });
    const { title, scope, pins, excludes, model } = (await request.json()) as Partial<Chat>;
    if (model && !MODELS.includes(model)) return unprocessable("unknown model");
    Object.assign(c, { ...(title !== undefined && { title }), ...(model && { model }), ...(scope && { scope }), ...(pins && { pins }), ...(excludes && { excludes }), updated_at: now() });
    return HttpResponse.json(plain(c));
  }),
  http.get(url("/chats/:id/shares"), ({ params }) => {
    const c = find(params.id);
    if (!c) return notFound();
    return c.is_owner ? HttpResponse.json(state.shares[c.id] ?? { users: [], groups: [] }) : HttpResponse.json({ detail: "read-only" }, { status: 403 });
  }),
  http.put(url("/chats/:id/shares"), async ({ params, request }) => {
    const c = find(params.id);
    if (!c) return notFound();
    if (!c.is_owner) return HttpResponse.json({ detail: "read-only" }, { status: 403 });
    const b = (await request.json()) as { users?: string[]; groups?: string[] };
    const bad = (b.groups ?? []).find((g) => !g.startsWith("/"));
    if (bad) return unprocessable(`unknown group ${bad}`);
    return HttpResponse.json((state.shares[c.id] = { users: b.users ?? [], groups: b.groups ?? [] }));
  }),
  http.get(url("/chats/:id/export.md"), ({ params }) => {
    const c = find(params.id);
    return c ? new HttpResponse(exportMd(c), { headers: { "Content-Type": "text/markdown; charset=utf-8" } }) : notFound();
  }),
  http.put(url("/messages/:id/rating"), async ({ params, request }) => {
    const m = chats().flatMap((c) => c.messages).find((x) => x.id === params.id);
    if (!m) return notFound();
    if (m.role !== "assistant") return unprocessable("only answers can be rated");
    const { value, comment } = (await request.json()) as { value: 1 | -1 | 0; comment?: string };
    const out = { value, comment: value ? (comment ?? null) : null };
    m.rating = value ? { value, comment: out.comment } : null;
    return HttpResponse.json(out);
  }),
  http.post(url("/chats/:id/messages"), async ({ params, request }) => {
    const c = find(params.id);
    if (!c) return notFound();
    if (!c.is_owner) return HttpResponse.json({ detail: "read-only" }, { status: 403 });
    const body = (await request.json()) as SendMessageBody;
    if (!body.content || body.content.length > MAX_CHARS) return unprocessable("content must be 1 to 4000 characters");
    if ((body.viewer?.selection?.text.length ?? 0) > MAX_CHARS) return unprocessable("selection too long");
    if (/trigger 503/i.test(body.content)) return HttpResponse.json({ detail: "inference unavailable" }, { status: 503 });
    if (body.model && !MODELS.includes(body.model)) return unprocessable("unknown model");
    return stream(c, body, request.signal);
  }),
];
