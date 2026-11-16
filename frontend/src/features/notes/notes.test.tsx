import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider } from "@tanstack/react-router";
import { http, HttpResponse } from "msw";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { API_BASE } from "@/api/client";
import type { Note } from "@/api/notes";
import { createAppRouter } from "@/app/router";
import { DOCS } from "@/mocks/docs";
import { docling } from "@/mocks/pdf";
import { server } from "@/mocks/server";
import { state } from "@/mocks/state";
import { bboxToViewport } from "@/features/reader/bbox";
import { getReaderState, resetReaderStore } from "@/features/reader/useReaderStore";
import { notesUi } from "./api";
import { threads } from "./NotesTab";

// page 3 / paragraph 2 of the first sample document: where "Add note" clicks land
const CHUNK = docling(DOCS[0]).chunks.find((c) => c.chunk_id.endsWith(":2:1"))!;
const TL = bboxToViewport(CHUNK.bbox, 792, 1);

vi.mock("@/features/reader/pdfjs", () => ({
  loadPdf: async () => ({
    numPages: 3,
    getPage: async () => ({
      getViewport: ({ scale }: { scale: number }) => ({ width: 612 * scale, height: 792 * scale, scale }),
      render: () => ({ promise: Promise.resolve() }),
      getTextContent: async () => ({ items: [
        { str: "We conjecture an analogous", transform: [11, 0, 0, 11, CHUNK.bbox.l + 8, CHUNK.bbox.t - 12] },
        { str: "bound.", transform: [11, 0, 0, 11, CHUNK.bbox.l + 8, CHUNK.bbox.t - 27] },
        { str: "Page footer", transform: [9, 0, 0, 9, 300, 40] },
      ] }),
    }),
  }),
}));

const D1 = DOCS[0].id, D2 = DOCS[1].id;

async function renderApp(path: string) {
  const router = createAppRouter([path]);
  await router.load();
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><RouterProvider router={router} /></QueryClientProvider>);
  await screen.findByRole("heading", { level: 2 });
  return router;
}
const reader = () => screen.getByRole("complementary", { name: "Reader" });
async function openNotes(path = `/d/${D1}`) {
  await renderApp(path);
  await userEvent.click(within(reader()).getByRole("tab", { name: /Notes/ }));
  return within(reader());
}
const card = (name: string | RegExp) => screen.findByRole("article", { name });

const seen: Request[] = [];
beforeEach(() => {
  resetReaderStore(); notesUi.reset(); seen.length = 0;
  server.events.on("request:start", ({ request }) => { seen.push(request.clone()); });
});
afterEach(() => { server.events.removeAllListeners(); });
async function sent(method: string, suffix: string) {
  await waitFor(() => expect(seen.some((r) => r.method === method && new URL(r.url).pathname.endsWith(suffix))).toBe(true));
  const text = await seen.find((r) => r.method === method && new URL(r.url).pathname.endsWith(suffix))!.text();
  return text ? JSON.parse(text) : null;
}

describe("threads", () => {
  it("groups replies under their note and drops replies whose thread is missing", () => {
    const n = (id: string, parent_id: string | null) => ({ id, parent_id }) as Note;
    const t = threads([n("a", null), n("r1", "a"), n("b", null), n("r2", "a"), n("orphan", "zzz")]);
    expect(t.map((x) => [x.note.id, x.replies.map((r) => r.id)])).toEqual([["a", ["r1", "r2"]], ["b", []]]);
  });
});

describe("notes tab", () => {
  it("lists notes with threads; replies collapse after 3", async () => {
    const r = await openNotes();
    expect(await card("Finding by J. Ruiz")).toBeInTheDocument();
    expect(r.getByRole("tab", { name: /Notes/ })).toHaveTextContent("4");
    const q = await card("Question by P. Lindqvist");
    expect(within(q).getAllByRole("listitem").filter((li) => li.classList.contains("nreply"))).toHaveLength(3);
    await userEvent.click(within(q).getByRole("button", { name: "Show 1 more reply" }));
    expect(within(q).getAllByRole("listitem").filter((li) => li.classList.contains("nreply"))).toHaveLength(4);
    const f = await card("Finding by J. Ruiz");
    expect(within(f).getByText("Agent-assisted")).toBeInTheDocument();
    expect(within(f).getByText(/Evidence: 2 supports · 1 inconclusive/)).toBeInTheDocument();
  });

  it("flags a note that may not apply to the viewed version", async () => {
    await openNotes();
    expect(within(await card("Correction by P. Lindqvist")).getByText("May not apply to version 2")).toBeInTheDocument();
  });

  it("filters by kind", async () => {
    const r = await openNotes();
    await card("Finding by J. Ruiz");
    await userEvent.selectOptions(r.getByRole("combobox", { name: "Filter by kind" }), "question");
    expect(screen.queryByRole("article", { name: "Finding by J. Ruiz" })).toBeNull();
    expect(screen.getByRole("article", { name: "Question by P. Lindqvist" })).toBeInTheDocument();
  });

  it("renders note bodies as safe markdown: no images, no javascript: links", async () => {
    const note: Note = {
      id: "x", document_id: D1, version_id: "v", version: 2, parent_id: null, author: { id: "u-jruiz", username: "J. Ruiz" }, kind: "finding",
      body: "![tracker](https://evil.test/p.png) [bad](javascript:alert(1)) [ok](https://example.org) <b>raw</b>", anchor: null, relation: null,
      visibility: "everyone", shares: [], status: "visible", agent_assisted: false, may_not_apply_to_version: null,
      votes: { up: 0, down: 0, mine: 0, score: 0.5 }, evidence: { supports: 0, refutes: 0, inconclusive: 0 }, created_at: "", updated_at: "",
    };
    server.use(http.get(`${API_BASE}/documents/:id/annotations`, () => HttpResponse.json({ items: [note] })));
    await openNotes();
    const c = await card("Finding by J. Ruiz");
    expect(c.querySelector("img")).toBeNull();
    expect(c.querySelector(".ntext b")).toBeNull();
    expect(within(c).getByText("bad").closest("a")).toBeNull();
    expect(within(c).getByRole("link", { name: "ok" })).toHaveAttribute("href", "https://example.org/");
  });

  it("votes with PUT {value} and reflects aria-pressed", async () => {
    await openNotes();
    const f = await card("Finding by J. Ruiz");
    const up = within(f).getAllByRole("button", { name: "Helpful" })[0];
    expect(up).toHaveAttribute("aria-pressed", "false");
    await userEvent.click(up);
    expect(await sent("PUT", "/annotations/n-finding/vote")).toEqual({ value: 1 });
    await waitFor(() => expect(within(f).getAllByRole("button", { name: "Helpful" })[0]).toHaveAttribute("aria-pressed", "true"));
  });

  it("disables voting on your own note", async () => {
    await openNotes();
    const mine = await card("Comment by researcher");
    const up = within(mine).getByRole("button", { name: "Helpful" });
    expect(up).toBeDisabled();
    expect(up).toHaveAttribute("title", "You can't vote on your own note");
  });

  it("reports with a reason", async () => {
    await openNotes();
    const f = await card("Finding by J. Ruiz");
    await userEvent.click(within(f).getByRole("button", { name: "More actions for note by J. Ruiz" }));
    await userEvent.click(await screen.findByRole("menuitem", { name: "Report…" }));
    await userEvent.click(await screen.findByRole("menuitem", { name: "Spam" }));
    expect(await sent("POST", "/annotations/n-finding/report")).toEqual({ reason: "spam" });
  });

  it("offers hide only to the document owner and remove only to admins", async () => {
    await openNotes(); // D1 belongs to the mock user
    const f = await card("Finding by J. Ruiz");
    await userEvent.click(within(f).getByRole("button", { name: /More actions/ }));
    expect(await screen.findByRole("menuitem", { name: "Hide from readers" })).toBeInTheDocument();
    expect(screen.queryByRole("menuitem", { name: "Remove (admin)" })).toBeNull();
    expect(screen.queryByRole("menuitem", { name: "Edit" })).toBeNull();
    await userEvent.click(screen.getByRole("menuitem", { name: "Hide from readers" }));
    expect(await sent("POST", "/annotations/n-finding/hide")).toEqual({ hidden: true });
  });

  it("hides the hide action on someone else's document; admins get Remove with a confirm", async () => {
    state.admin = true;
    await openNotes(`/d/${D2}`);
    const rel = await card("Relation by k.ito");
    await userEvent.click(within(rel).getByRole("button", { name: /More actions/ }));
    expect(screen.queryByRole("menuitem", { name: /Hide/ })).toBeNull();
    await userEvent.click(await screen.findByRole("menuitem", { name: "Remove (admin)" }));
    await userEvent.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Remove" }));
    await sent("DELETE", "/admin/annotations/n-relation");
    await waitFor(() => expect(screen.queryByRole("article", { name: "Relation by k.ito" })).toBeNull());
  });

  it("renders a relation with its target title, opening the target", async () => {
    await openNotes(`/d/${D2}`);
    const rel = await card("Relation by k.ito");
    expect(within(rel).getByText("extends →")).toBeInTheDocument();
    await userEvent.click(await within(rel).findByRole("button", { name: /Lower bounds on the spectral gap/ }));
    expect(getReaderState().active).toBe(D1);
  });

  it("lets the author edit and delete their own note", async () => {
    await openNotes();
    const mine = await card("Comment by researcher");
    await userEvent.click(within(mine).getByRole("button", { name: /More actions/ }));
    await userEvent.click(await screen.findByRole("menuitem", { name: "Delete" }));
    await userEvent.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Delete" }));
    await sent("DELETE", "/annotations/n-mine");
    await waitFor(() => expect(screen.queryByRole("article", { name: "Comment by researcher" })).toBeNull());
  });

  it("pages notes: 20 first, then Show more", async () => {
    const items = Array.from({ length: 25 }, (_, i) => ({
      id: `p${i}`, document_id: D1, version_id: "v", version: 2, parent_id: null, author: { id: `u${i}`, username: `user ${i}` }, kind: "comment",
      body: `note ${i}`, anchor: null, relation: null, visibility: "everyone", shares: [], status: "visible", agent_assisted: false,
      may_not_apply_to_version: null, votes: { up: 0, down: 0, mine: 0, score: 0.5 }, evidence: { supports: 0, refutes: 0, inconclusive: 0 }, created_at: "", updated_at: "",
    }));
    server.use(http.get(`${API_BASE}/documents/:id/annotations`, () => HttpResponse.json({ items })));
    const r = await openNotes();
    await card("Comment by user 0");
    expect(r.getAllByRole("article")).toHaveLength(20);
    await userEvent.click(r.getByRole("button", { name: "Show more (5 left)" }));
    expect(r.getAllByRole("article")).toHaveLength(25);
  });

  it("edits your own note with PATCH", async () => {
    await openNotes();
    const mine = await card("Comment by researcher");
    await userEvent.click(within(mine).getByRole("button", { name: /More actions/ }));
    await userEvent.click(await screen.findByRole("menuitem", { name: "Edit" }));
    const ed = within(within(mine).getByRole("group", { name: "Edit note" }));
    await userEvent.clear(ed.getByRole("textbox", { name: "Note" }));
    await userEvent.type(ed.getByRole("textbox", { name: "Note" }), "Rewritten");
    await userEvent.click(ed.getByRole("button", { name: "Save" }));
    expect(await sent("PATCH", "/annotations/n-mine")).toEqual({ body: "Rewritten", visibility: "private" });
    expect(await within(mine).findByText("Rewritten")).toBeInTheDocument();
  });

  it("reveals long reply threads 20 at a time", async () => {
    const mk = (id: string, parent_id: string | null) => ({
      id, document_id: D1, version_id: "v", version: 2, parent_id, author: { id: `u-${id}`, username: `user ${id}` }, kind: "comment",
      body: `body ${id}`, anchor: null, relation: null, visibility: "everyone", shares: [], status: "visible", agent_assisted: false,
      may_not_apply_to_version: null, votes: { up: 0, down: 0, mine: 0, score: 0.5 }, evidence: { supports: 0, refutes: 0, inconclusive: 0 }, created_at: "", updated_at: "",
    });
    const items = [mk("root", null), ...Array.from({ length: 25 }, (_, i) => mk(`r${i}`, "root"))];
    server.use(http.get(`${API_BASE}/documents/:id/annotations`, () => HttpResponse.json({ items })));
    await openNotes();
    const c = await card("Comment by user root");
    const replies = () => within(c).getAllByRole("listitem").filter((li) => li.classList.contains("nreply"));
    expect(replies()).toHaveLength(3);
    await userEvent.click(within(c).getByRole("button", { name: "Show 20 more replies" }));
    expect(replies()).toHaveLength(23);
    await userEvent.click(within(c).getByRole("button", { name: "Show 2 more replies" }));
    expect(replies()).toHaveLength(25);
  });

  it("replies with POST /replies {body}", async () => {
    await openNotes();
    const f = await card("Finding by J. Ruiz");
    await userEvent.click(within(f).getByRole("button", { name: "Reply" }));
    await userEvent.type(within(f).getByRole("textbox", { name: "Reply to J. Ruiz" }), "Nice sweep");
    await userEvent.click(within(f).getByRole("button", { name: "Post reply" }));
    expect(await sent("POST", "/annotations/n-finding/replies")).toEqual({ body: "Nice sweep" });
    expect(await within(f).findByText("Nice sweep")).toBeInTheDocument();
  });
});

describe("composer", () => {
  const compose = async () => {
    const r = await openNotes();
    await userEvent.click(r.getByRole("button", { name: "New note" }));
    return within(screen.getByRole("group", { name: "New note" }));
  };

  it("creates a document-level comment without an anchor", async () => {
    const c = await compose();
    await userEvent.type(c.getByRole("textbox", { name: "Note" }), "Great survey");
    await userEvent.click(c.getByRole("button", { name: "Post note" }));
    expect(await sent("POST", `/documents/${D1}/annotations`)).toEqual({ kind: "comment", body: "Great survey", visibility: "everyone" });
  });

  it("anchors a typed quote to a page without a box (keyboard path)", async () => {
    const c = await compose();
    await userEvent.type(c.getByRole("textbox", { name: /Quote from the page/ }), "tangle-free decomposition");
    await userEvent.clear(c.getByRole("spinbutton", { name: "Page" }));
    await userEvent.type(c.getByRole("spinbutton", { name: "Page" }), "1");
    await userEvent.selectOptions(c.getByRole("combobox", { name: "Kind" }), "question");
    await userEvent.type(c.getByRole("textbox", { name: "Note" }), "Which one?");
    await userEvent.click(c.getByRole("button", { name: "Post note" }));
    expect(await sent("POST", `/documents/${D1}/annotations`)).toMatchObject({
      kind: "question", anchor: { quote: "tangle-free decomposition", prefix: "", suffix: "", page: 1, bbox: null },
    });
  });

  it("creates a relation to a document picked from library search, shared with a group", async () => {
    const c = await compose();
    await userEvent.selectOptions(c.getByRole("combobox", { name: "Kind" }), "relation");
    await userEvent.selectOptions(c.getByRole("combobox", { name: "Relation" }), "contradicts");
    await userEvent.type(c.getByRole("searchbox", { name: "Related document" }), "Cheeger");
    await userEvent.click(await c.findByRole("button", { name: /Cheeger-type inequalities/ }));
    await userEvent.type(c.getByRole("textbox", { name: "Note" }), "Disagrees on the constant");
    await userEvent.selectOptions(c.getByRole("combobox", { name: "Visible to" }), "shared");
    await userEvent.type(c.getByRole("textbox", { name: "Groups" }), "/Mathematics{Enter}");
    await userEvent.click(c.getByRole("button", { name: "Post note" }));
    expect(await sent("POST", `/documents/${D1}/annotations`)).toEqual({
      kind: "relation", body: "Disagrees on the constant", visibility: "shared",
      shares: [{ kind: "group", principal: "/Mathematics" }], relation: { type: "contradicts", target_document_id: D2 },
    });
  });
});

describe("anchors in the document view", () => {
  it("draws markers for anchored notes on the viewed version only", async () => {
    await renderApp(`/d/${D1}`);
    expect(await screen.findByRole("button", { name: "Note by J. Ruiz: Finding" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Note by P. Lindqvist: Question" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Note by P. Lindqvist: Correction" })).toBeNull(); // note is on version 1
    const mark = screen.getByRole("button", { name: "Note by J. Ruiz: Finding" });
    expect(mark.closest("[data-page]")).toHaveAttribute("data-page", "3");
  });

  it("marker click switches to the Notes tab and flashes the card", async () => {
    await renderApp(`/d/${D1}`);
    await userEvent.click(await screen.findByRole("button", { name: "Note by J. Ruiz: Finding" }));
    expect(within(reader()).getByRole("tab", { name: /Notes/ })).toHaveAttribute("aria-selected", "true");
    expect(await card("Finding by J. Ruiz")).toHaveClass("flash");
    await waitFor(() => expect(screen.getByRole("article", { name: "Finding by J. Ruiz" })).toHaveFocus());
  });

  it("clears a flash whose note isn't in the list", async () => {
    notesUi.set((s) => ({ ...s, flash: "gone" }));
    await openNotes();
    await card("Finding by J. Ruiz");
    await waitFor(() => expect(notesUi.get().flash).toBeNull());
  });

  it("Add note is disabled on an older version", async () => {
    await renderApp(`/d/${D1}/v/1`);
    const add = await within(reader()).findByRole("button", { name: "Add note" });
    expect(add).toBeDisabled();
    expect(add).toHaveAttribute("title", "Switch to the latest version to anchor a note");
  });

  it("drops the box of a passage picked on an older version (notes attach to the latest)", async () => {
    notesUi.set((s) => ({ ...s, draft: { docId: D1, version: 1, quote: "old text", page: 3, bbox: CHUNK.bbox } }));
    const r = await openNotes();
    const c = within(r.getByRole("group", { name: "New note" }));
    await userEvent.type(c.getByRole("textbox", { name: "Note" }), "x");
    await userEvent.click(c.getByRole("button", { name: "Post note" }));
    expect(await sent("POST", `/documents/${D1}/annotations`)).toMatchObject({ anchor: { quote: "old text", page: 3, bbox: null } });
  });

  it("hints when a picked passage has no quote text", async () => {
    notesUi.set((s) => ({ ...s, draft: { docId: D1, version: 2, quote: "", page: 3, bbox: CHUNK.bbox } }));
    await openNotes();
    expect(screen.getByText("Type the passage to keep the anchor.")).toBeInTheDocument();
  });

  it("Write without picking opens the composer on the current page", async () => {
    await renderApp(`/d/${D1}`);
    await userEvent.click(within(reader()).getByRole("button", { name: "Add note" }));
    await userEvent.click(screen.getByRole("button", { name: "Write without picking" }));
    const c = within(await screen.findByRole("group", { name: "New note" }));
    expect(c.getByRole("spinbutton", { name: "Page" })).toHaveValue(1);
    expect(screen.queryByText(/Click the passage you want to note/)).toBeNull();
  });

  it("Add note: clicking a passage opens the composer with its quote, page and box; Esc cancels the mode", async () => {
    await renderApp(`/d/${D1}`);
    await screen.findByRole("button", { name: "Note by J. Ruiz: Finding" });
    const add = within(reader()).getByRole("button", { name: "Add note" });
    await userEvent.click(add);
    expect(screen.getByText(/Click the passage you want to note/)).toBeInTheDocument();
    await userEvent.keyboard("{Escape}");
    expect(screen.queryByText(/Click the passage you want to note/)).toBeNull();
    expect(getReaderState().tabs).toHaveLength(1); // Esc didn't close the reader

    await userEvent.click(add);
    const page = document.querySelector('[data-page="3"]')!;
    await waitFor(() => {
      act(() => { fireEvent.click(page, { clientX: TL.x + 20, clientY: TL.y + 5 }); });
      expect(screen.getByRole("group", { name: "New note" })).toBeInTheDocument();
    });
    const c = within(screen.getByRole("group", { name: "New note" }));
    expect(c.getByRole("textbox", { name: /Quote from the page/ })).toHaveValue("We conjecture an analogous bound.");
    expect(c.getByRole("spinbutton", { name: "Page" })).toHaveValue(3);
    await userEvent.type(c.getByRole("textbox", { name: "Note" }), "Testable");
    await userEvent.click(c.getByRole("button", { name: "Post note" }));
    const b = CHUNK.bbox;
    expect(await sent("POST", `/documents/${D1}/annotations`)).toMatchObject({
      anchor: { quote: "We conjecture an analogous bound.", page: 3, bbox: { x0: b.l, y0: b.t, x1: b.r, y1: b.b, coord_origin: "BOTTOMLEFT" } },
    });
  });
});
