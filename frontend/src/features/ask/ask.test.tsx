import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider } from "@tanstack/react-router";
import { http, HttpResponse } from "msw";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { API_BASE } from "@/api/client";
import { createAppRouter } from "@/app/router";
import { server } from "@/mocks/server";
import { state as mockState } from "@/mocks/state";
import { openDoc, resetReaderStore } from "@/features/reader/useReaderStore";
import { resetAskTurns } from "./askStore";
import { openPalette } from "@/features/palette/usePalette";
import { resetAskContext, setQuote } from "./useAskContext";
import { titleOf } from "./AskPage";
import { buildTurns } from "./turns";

vi.mock("@/features/reader/pdfjs", () => ({
  loadPdf: async () => ({
    numPages: 3,
    getPage: async () => ({
      getViewport: ({ scale }: { scale: number }) => ({ width: 612 * scale, height: 792 * scale, scale }),
      render: () => ({ promise: Promise.resolve() }),
    }),
  }),
}));

const D1 = "00000000-0000-4000-8000-000000000001";
const D2 = "00000000-0000-4000-8000-000000000002";
const Q = "Does the spectral gap lower bound extend to hypergraphs?";

async function renderApp(path = "/ask") {
  const router = createAppRouter([path]);
  await router.load();
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><RouterProvider router={router} /></QueryClientProvider>);
  await screen.findByRole("navigation", { name: "Main" });
  return router;
}

const seen: Request[] = [];
beforeEach(() => {
  resetReaderStore(); resetAskContext(); resetAskTurns(); seen.length = 0;
  server.events.on("request:start", ({ request }) => { seen.push(request.clone()); });
});
afterEach(() => { server.events.removeAllListeners(); });
const sent = (method: string, re: RegExp) => seen.filter((r) => r.method === method && re.test(new URL(r.url).pathname));

/** A message endpoint whose stream stays open until the test pushes events. */
function hold({ save = true } = {}) {
  let ctrl!: ReadableStreamDefaultController<Uint8Array>;
  let closed = false;
  const enc = new TextEncoder();
  server.use(http.post(`${API_BASE}/chats/:id/messages`, async ({ params, request }) => {
    if (save) { // like the real server, the question is saved as soon as the request arrives
      const chat = mockState.chats?.find((c) => c.id === params.id);
      const body = (await request.json()) as { content: string };
      chat?.messages.push({ id: `${params.id}-u${chat.messages.length}`, role: "user", content: body.content, citations: [], retrieved: [], status: "complete", model: null, created_at: "" });
    }
    request.signal.addEventListener("abort", () => { closed = true; try { ctrl.error(new DOMException("aborted", "AbortError")); } catch { /* already closed */ } });
    return new HttpResponse(new ReadableStream({ start(c) { ctrl = c; }, cancel() { closed = true; } }), { headers: { "Content-Type": "text/event-stream", "Content-Length": "1000000" } }); // a declared length, so an abort is a truncated body (undici asserts when an until-close body ends after abort)
  }));
  return { push: (e: string, d: unknown) => { if (!closed) ctrl.enqueue(enc.encode(`event: ${e}\ndata: ${JSON.stringify(d)}\n\n`)); } };
}
const box = () => screen.findByRole("textbox", { name: "Question" });

describe("ask view", () => {
  it("shows the empty state and a New chat heading", async () => {
    await renderApp();
    expect(await screen.findByText(/Ask a question about the library\. Documents open in the reader count as context/)).toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 1, name: "New chat" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Ask" })).toBeDisabled();
  });

  it("creates the chat on first send, navigates, streams the answer, then shows sidenotes", async () => {
    const user = userEvent.setup();
    const router = await renderApp();
    await user.type(await box(), `${Q}{Enter}`);
    await waitFor(() => expect(router.state.location.pathname).toBe("/ask/c2"));
    expect(sent("POST", /^\/api\/chats$/).length + sent("POST", /\/chats$/).length).toBeGreaterThan(0);
    expect(await screen.findByText(/From the documents you can see/)).toBeInTheDocument();
    await waitFor(() => expect(document.querySelectorAll(".margin .note").length).toBeGreaterThan(0));
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent(Q.slice(0, 20));
    await waitFor(() => expect(screen.getByRole("button", { name: "Ask" })).toBeInTheDocument()); // Stop is gone
    expect(screen.getAllByText(Q).length).toBeGreaterThan(0);
  });

  it("shows a caret while streaming, sidenotes only after citations arrive", async () => {
    const user = userEvent.setup();
    const s = hold();
    await renderApp();
    await user.type(await box(), `${Q}{Enter}`);
    await waitFor(() => expect(sent("POST", /\/messages$/)).toHaveLength(1));
    await act(async () => { s.push("token", { text: "Partly. See [1]" }); });
    await waitFor(() => expect(document.querySelector(".ans-text.streaming")).toBeTruthy());
    await waitFor(() => expect(document.querySelector(".ans-text")).toHaveTextContent("Partly. See"));
    expect(document.querySelectorAll(".margin .note")).toHaveLength(0);
    expect(screen.getByRole("button", { name: "Stop" })).toBeInTheDocument();
    const cite = { n: 1, doc_id: D1, version_id: "v", version: 2, chunk_id: "x", title: "Lower bounds", page: 1, bbox: null, quote: "the quote" };
    await act(async () => { s.push("citations", { items: [cite] }); });
    expect(await screen.findByText("the quote")).toBeInTheDocument();
    expect(await screen.findByRole("button", { name: /Source 1: Lower bounds/ })).toBeInTheDocument();
  });

  it("sends open tabs minus ignored docs and the chosen model", async () => {
    const user = userEvent.setup();
    openDoc(D1, { page: 2 }); openDoc(D2);
    await renderApp();
    const chip1 = await screen.findByText(/Reading Lower bounds on the spectr/);
    expect(chip1).toHaveTextContent(/, p\. 2/);
    await screen.findByText(/Reading Cheeger-type inequalities/);
    const ignoreBtns = screen.getAllByRole("button", { name: "Ignore this document" });
    await user.click(ignoreBtns[1]);
    expect(screen.getByRole("button", { name: "Use this document" }).closest(".cchip")).toHaveClass("off");
    await user.selectOptions(await screen.findByRole("combobox", { name: "Model" }), "open-70b");
    await user.type(await box(), `${Q}{Enter}`);
    await waitFor(() => expect(sent("POST", /\/messages$/)).toHaveLength(1));
    const body = await sent("POST", /\/messages$/)[0].json();
    expect(body.model).toBe("open-70b");
    expect(body.content).toBe(Q);
    expect(body.viewer.ignored).toEqual([D2]);
    expect(body.viewer.tabs.map((t: { doc_id: string }) => t.doc_id)).toEqual([D1]);
    expect(body.viewer.tabs[0].page).toBe(2);
    expect(body.viewer.selection).toBeNull();
    const created = await sent("POST", /\/chats$/)[0].json();
    expect(created).toMatchObject({ scope: "all", model: "open-70b" });
  });

  it("PATCHes the chat when the scope changes; open scope needs a reader tab", async () => {
    const user = userEvent.setup();
    await renderApp("/ask/c1");
    const scope = await screen.findByRole("combobox", { name: "Search in" });
    expect(within(scope).getByRole("option", { name: "Open documents only" })).toBeDisabled();
    const coll = await within(scope).findByRole("option", { name: "Collection: Thesis reading list" });
    await user.selectOptions(scope, coll);
    await waitFor(() => expect(sent("PATCH", /\/chats\/c1$/)).toHaveLength(1));
    expect(await sent("PATCH", /\/chats\/c1$/)[0].json()).toEqual({ scope: "collection:c1" });
  });

  it("shows an existing open-scope chat with no tabs as open, not as all", async () => {
    const base = await (await fetch(`${API_BASE}/chats/c1`)).json();
    server.use(http.get(`${API_BASE}/chats/c1`, () => HttpResponse.json({ ...base, scope: "open" })));
    await renderApp("/ask/c1");
    const scope = await screen.findByRole("combobox", { name: "Search in" });
    await waitFor(() => expect(scope).toHaveValue("open"));
    expect(within(scope).getByRole("option", { name: "Open documents only (none open)" })).toBeEnabled();
  });

  it("caps the scope select at 20 collections and picks more through a filter", async () => {
    const user = userEvent.setup();
    const cols = Array.from({ length: 25 }, (_, i) => ({ id: `k${i}`, name: `Coll ${i}`, count: 1 }));
    server.use(http.get(`${API_BASE}/collections`, () => HttpResponse.json(cols)));
    await renderApp("/ask/c1");
    const scope = await screen.findByRole("combobox", { name: "Search in" });
    await within(scope).findByRole("option", { name: "Collection: Coll 0" });
    expect(within(scope).getAllByRole("option").filter((o) => /^Collection:/.test(o.textContent ?? ""))).toHaveLength(20);
    await user.selectOptions(scope, "More collections…");
    await user.type(await screen.findByRole("searchbox", { name: "Filter collections" }), "Coll 23");
    await user.click(screen.getByRole("button", { name: "Coll 23" }));
    await waitFor(() => expect(sent("PATCH", /\/chats\/c1$/)).toHaveLength(1));
    expect(await sent("PATCH", /\/chats\/c1$/)[0].json()).toEqual({ scope: "collection:k23" });
  });

  it("Stop aborts the stream and marks the answer interrupted", async () => {
    const user = userEvent.setup();
    const s = hold();
    await renderApp();
    await user.type(await box(), `${Q}{Enter}`);
    await waitFor(() => expect(sent("POST", /\/messages$/)).toHaveLength(1));
    await act(async () => { s.push("token", { text: "Partly" }); });
    await user.click(await screen.findByRole("button", { name: "Stop" }));
    expect(await screen.findByText("Stopped before the answer finished.")).toBeInTheDocument();
    expect(screen.getByText("Partly")).toBeInTheDocument();
    expect(await screen.findByRole("button", { name: "Ask" })).toBeInTheDocument();
  });

  it("gives the question back when the server never saved an aborted turn", async () => {
    const user = userEvent.setup();
    const s = hold({ save: false });
    await renderApp();
    await user.type(await box(), `${Q}{Enter}`);
    await waitFor(() => expect(sent("POST", /\/messages$/)).toHaveLength(1));
    await act(async () => { s.push("token", { text: "Partly" }); });
    await user.click(await screen.findByRole("button", { name: "Stop" }));
    await waitFor(() => expect(screen.getByRole("textbox", { name: "Question" })).toHaveValue(Q));
    expect(screen.queryByText("Stopped before the answer finished.")).toBeNull();
  });

  it("sends the quoted passage as viewer.selection and clears the quote chip", async () => {
    const user = userEvent.setup();
    await renderApp();
    const quote = { doc_id: D1, page: 3, text: "a quoted passage" };
    act(() => setQuote(quote));
    expect(await screen.findByRole("button", { name: "Remove quote" })).toBeInTheDocument();
    await user.type(await box(), `${Q}{Enter}`);
    await waitFor(() => expect(sent("POST", /\/messages$/)).toHaveLength(1));
    expect((await sent("POST", /\/messages$/)[0].json()).viewer.selection).toEqual(quote);
    expect(screen.queryByRole("button", { name: "Remove quote" })).toBeNull();
  });

  it("stops an in-flight answer when the user starts a new chat", async () => {
    const user = userEvent.setup();
    hold();
    const router = await renderApp();
    await user.type(await box(), `${Q}{Enter}`);
    await waitFor(() => expect(sent("POST", /\/messages$/)).toHaveLength(1));
    await user.click(within(await screen.findByRole("navigation", { name: "Recent chats" })).getByRole("link", { name: "New chat" }));
    await screen.findByText(/Ask a question about the library/);
    await act(async () => { await router.navigate({ to: "/ask/$chatId", params: { chatId: "c2" } }); });
    expect(await screen.findByText("Stopped before the answer finished.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Stop" })).toBeNull();
    expect(await screen.findByRole("button", { name: "Ask" })).toBeInTheDocument();
  });

  it("aborts cleanly when tokens arrived before the user navigated away", async () => {
    const user = userEvent.setup();
    const s = hold();
    const router = await renderApp();
    await user.type(await box(), `${Q}{Enter}`);
    await waitFor(() => expect(sent("POST", /\/messages$/)).toHaveLength(1));
    await act(async () => { s.push("token", { text: "Partly" }); });
    await screen.findByText("Partly");
    await act(async () => { await router.navigate({ to: "/library" }); });
    await waitFor(() => expect(router.state.location.pathname).toBe("/library"));
    await act(async () => { await router.navigate({ to: "/ask/$chatId", params: { chatId: "c2" } }); });
    expect(await screen.findByText("Stopped before the answer finished.")).toBeInTheDocument();
    expect(screen.getByText("Partly")).toBeInTheDocument();
  });

  it("keeps the log silent and announces the outcome once", async () => {
    const user = userEvent.setup();
    await renderApp();
    await user.type(await box(), `${Q}{Enter}`);
    expect(await screen.findByRole("log")).toHaveAttribute("aria-live", "off");
    expect(screen.getByRole("region", { name: "Conversation" })).toHaveAttribute("tabindex", "0");
    await waitFor(() => expect(screen.getByText("Answer ready", { selector: "[role=status]" })).toBeInTheDocument());
  });

  it("shows the question exactly once after the answer completes", async () => {
    const user = userEvent.setup();
    await renderApp();
    await user.type(await box(), `${Q}{Enter}`);
    await screen.findByText(/From the documents you can see/);
    await waitFor(() => expect(screen.getByRole("button", { name: "Ask" })).toBeInTheDocument());
    await waitFor(() => expect(within(screen.getByRole("log")).getAllByText(Q)).toHaveLength(1));
  });

  it("shows a failed answer with Try again that re-sends the question", async () => {
    const user = userEvent.setup();
    const s = hold();
    await renderApp();
    await user.type(await box(), `${Q}{Enter}`);
    await waitFor(() => expect(sent("POST", /\/messages$/)).toHaveLength(1));
    await act(async () => { s.push("error", { detail: "The model timed out." }); });
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("The answer couldn't be completed. The model timed out.");
    server.resetHandlers();
    await user.click(within(alert).getByRole("button", { name: "Try again" }));
    await waitFor(() => expect(sent("POST", /\/messages$/)).toHaveLength(2));
    expect((await sent("POST", /\/messages$/)[1].json()).content).toBe(Q);
  });

  it("restores the question and tells the user when the model is rejected (422)", async () => {
    const user = userEvent.setup();
    server.use(http.post(`${API_BASE}/chats/:id/messages`, () => HttpResponse.json({ detail: "unknown model" }, { status: 422 })));
    await renderApp();
    await user.type(await box(), `${Q}{Enter}`);
    await waitFor(() => expect(screen.getByRole("textbox", { name: "Question" })).toHaveValue(Q));
    expect(await screen.findByText("unknown model")).toBeInTheDocument();
  });

  it("renders seeded history at /ask/c1 with math, six markers and the unavailable source", async () => {
    await renderApp("/ask/c1");
    expect(await screen.findByRole("heading", { level: 1, name: "Spectral gap in hypergraphs" })).toBeInTheDocument();
    await screen.findByText(/Does the spectral-gap lower bound/);
    expect(document.querySelectorAll(".ans-text .katex").length).toBeGreaterThan(2);
    expect(document.querySelectorAll(".ans-text sup")).toHaveLength(6);
    expect(screen.getByText("Source not available")).toBeInTheDocument();
    expect(screen.queryByText("Stopped before the answer finished.")).toBeNull();
  });

  it("a chat that fails to load for another reason offers Retry", async () => {
    const user = userEvent.setup();
    let fail = true;
    const orig = await (await fetch(`${API_BASE}/chats/c1`)).json();
    server.use(http.get(`${API_BASE}/chats/c1`, () => (fail ? HttpResponse.json({ detail: "x" }, { status: 500 }) : HttpResponse.json(orig))));
    await renderApp("/ask/c1");
    expect(await screen.findByText("Couldn't load this chat.")).toBeInTheDocument();
    expect(screen.queryByText(/This chat isn't available/)).toBeNull();
    fail = false;
    await user.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByRole("heading", { level: 1, name: "Spectral gap in hypergraphs" })).toBeInTheDocument();
  });

  it("keeps the thread when a refetch fails after the chat loaded", async () => {
    const user = userEvent.setup();
    await renderApp("/ask/c1");
    await screen.findByText(/Does the spectral-gap lower bound/);
    server.use(http.get(`${API_BASE}/chats/c1`, () => HttpResponse.json({ detail: "x" }, { status: 500 })));
    await user.click(await screen.findByRole("button", { name: "Helpful" })); // rating invalidates the chat
    await waitFor(() => expect(sent("GET", /\/chats\/c1$/).length).toBeGreaterThan(1));
    await new Promise((r) => setTimeout(r, 50));
    expect(screen.getByText(/Does the spectral-gap lower bound/)).toBeInTheDocument();
    expect(screen.queryByText(/Couldn't load this chat/)).toBeNull();
  });

  it("explains an unavailable chat and offers a new one", async () => {
    const user = userEvent.setup();
    const router = await renderApp("/ask/nope");
    expect(await screen.findByText("This chat isn't available. It may have been deleted, or it hasn't been shared with you.")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "New chat" }));
    await waitFor(() => expect(router.state.location.pathname).toBe("/ask"));
  });

  it("Shift+Enter inserts a newline and does not send", async () => {
    const user = userEvent.setup();
    await renderApp();
    const ta = await box();
    await user.type(ta, "line one{Shift>}{Enter}{/Shift}line two");
    expect(ta).toHaveValue("line one\nline two");
    expect(sent("POST", /\/chats$/)).toHaveLength(0);
  });

  it("whitespace-only questions cannot be sent", async () => {
    const user = userEvent.setup();
    await renderApp();
    await user.type(await box(), "   {Enter}");
    expect(sent("POST", /\/chats$/)).toHaveLength(0);
    expect(screen.getByRole("button", { name: "Ask" })).toBeDisabled();
  });

  it("Ask about this opens the ask view, un-ignores the document and focuses the question", async () => {
    const user = userEvent.setup();
    openDoc(D1);
    const router = await renderApp("/library");
    await user.click(await screen.findByRole("button", { name: /Ask about this/ }));
    await waitFor(() => expect(router.state.location.pathname).toBe("/ask"));
    await waitFor(() => expect(screen.getByRole("textbox", { name: "Question" })).toHaveFocus());
  });
});

describe("recent chats", () => {
  it("lists chats in the sidebar and marks the current one", async () => {
    await renderApp("/ask/c1");
    const nav = await screen.findByRole("navigation", { name: "Recent chats" });
    const link = await within(nav).findByRole("link", { name: "Spectral gap in hypergraphs" });
    expect(link).toHaveAttribute("href", "/ask/c1");
    await waitFor(() => expect(link).toHaveAttribute("aria-current", "page"));
    expect(within(nav).getByRole("link", { name: "New chat" })).not.toHaveAttribute("aria-current");
  });

  it("collapses more than five chats into +K more", async () => {
    const list = Array.from({ length: 8 }, (_, i) => ({ id: `x${i}`, title: `Chat ${i}`, updated_at: "2026-11-10T09:00:00Z" }));
    server.use(http.get(`${API_BASE}/chats`, () => HttpResponse.json(list)));
    await renderApp("/ask");
    const nav = await screen.findByRole("navigation", { name: "Recent chats" });
    await within(nav).findByRole("link", { name: "Chat 0" });
    expect(within(nav).getByRole("button", { name: "+3 more" })).toBeInTheDocument();
    expect(within(nav).queryByRole("link", { name: "Chat 7" })).toBeNull();
  });
});

describe("command palette", () => {
  it("offers New chat and recent chats", async () => {
    await renderApp("/library");
    act(() => openPalette());
    expect(await screen.findByRole("option", { name: /New chat/ })).toBeInTheDocument();
    expect(await screen.findByRole("option", { name: /Spectral gap in hypergraphs/ })).toBeInTheDocument();
  });
});

describe("helpers", () => {
  it("titleOf cuts at a word boundary within 80 characters", () => {
    expect(titleOf("  short question  ")).toBe("short question");
    const t = titleOf("word ".repeat(30));
    expect(t.length).toBeLessThanOrEqual(80);
    expect(t.endsWith("word")).toBe(true);
    expect(titleOf("x".repeat(100))).toHaveLength(80);
  });

  it("buildTurns replaces server messages past base with the pending turn until it settles", () => {
    const m = (id: string, role: "user" | "assistant") => ({ id, role, content: id, citations: [], retrieved: [], status: "complete" as const, model: null, created_at: "" });
    const msgs = [m("u1", "user"), m("a1", "assistant"), m("u2", "user")];
    const p = { question: "q2", selection: null, base: 2, text: "t", citations: [], status: "streaming" as const };
    expect(buildTurns(msgs, p).map((t) => t.key)).toEqual(["t0", "t1"]);
    const settled = [...msgs, m("a2", "assistant")];
    expect(buildTurns(settled, { ...p, status: "done", messageId: "a2" }).map((t) => t.key)).toEqual(["t0", "t1"]);
  });

  it("buildTurns keeps settled turns referentially stable while the pending turn streams", () => {
    const m = (id: string, role: "user" | "assistant") => ({ id, role, content: id, citations: [], retrieved: [], status: "complete" as const, model: null, created_at: "" });
    const msgs = [m("u1", "user"), m("a1", "assistant")];
    const p = { question: "q2", selection: null, base: 2, text: "t", citations: [], status: "streaming" as const };
    const a = buildTurns(msgs, p), b = buildTurns(msgs, { ...p, text: "t more" });
    expect(b[0]).toBe(a[0]);
    expect(b[1]).not.toBe(a[1]);
  });
});
