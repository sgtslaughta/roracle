import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider } from "@tanstack/react-router";
import { http, HttpResponse } from "msw";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { API_BASE } from "@/api/client";
import { createAppRouter } from "@/app/router";
import { server } from "@/mocks/server";
import { getReaderState, resetReaderStore } from "@/features/reader/useReaderStore";
import { resetAskTurns } from "./askStore";
import { getAskContext, resetAskContext, setQuote } from "./useAskContext";
import { streamMessage } from "./stream";
import { buildViewer } from "./viewer";
import { ScopeSelect } from "./ScopeSelect";

vi.mock("@/features/reader/pdfjs", () => ({
  loadPdf: async () => ({ numPages: 3, getPage: async () => ({ getViewport: ({ scale }: { scale: number }) => ({ width: 612 * scale, height: 792 * scale, scale }), render: () => ({ promise: Promise.resolve() }) }) }),
}));

async function renderApp(path = "/ask") {
  const router = createAppRouter([path]);
  await router.load();
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><RouterProvider router={router} /></QueryClientProvider>);
  await screen.findByRole("navigation", { name: "Main" });
}

const seen: Request[] = [];
beforeEach(() => {
  resetReaderStore(); resetAskContext(); resetAskTurns(); seen.length = 0;
  server.events.on("request:start", ({ request }) => { seen.push(request.clone()); });
});
afterEach(() => { server.events.removeAllListeners(); });
const sent = (method: string, re: RegExp) => seen.filter((r) => r.method === method && re.test(new URL(r.url).pathname));
const box = () => screen.findByRole("textbox", { name: "Question" });
const get = (p: string) => fetch(`${API_BASE}${p}`);

describe("GET /chats pagination (mock)", () => {
  it("returns {items,next} with an opaque cursor and 422 on a bad one", async () => {
    await get("/chats").then((r) => r.json());
    for (let i = 0; i < 3; i++) await fetch(`${API_BASE}/chats`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ title: `n${i}` }) });
    const p1 = await (await get("/chats?limit=2")).json();
    expect(p1.items).toHaveLength(2);
    expect(typeof p1.next).toBe("string");
    const p2 = await (await get(`/chats?limit=2&before=${encodeURIComponent(p1.next)}`)).json();
    expect(p2.items.map((c: { id: string }) => c.id)).not.toContain(p1.items[0].id);
    expect(p2.items.length).toBeGreaterThan(0);
    const bad = await get("/chats?before=%25%25");
    expect(bad.status).toBe(422);
    expect(await bad.json()).toEqual({ detail: "invalid cursor" });
  });
});

describe("sidebar", () => {
  it("offers Load more in the overflow menu while next is set", async () => {
    const user = userEvent.setup();
    const mk = (a: number, b: number) => Array.from({ length: b - a }, (_, i) => ({ id: `x${a + i}`, title: `Chat ${a + i}`, updated_at: "2026-11-10T09:00:00Z" }));
    server.use(http.get(`${API_BASE}/chats`, ({ request }) => {
      const before = new URL(request.url).searchParams.get("before");
      return HttpResponse.json(before ? { items: mk(8, 10), next: null } : { items: mk(0, 8), next: "cur1" });
    }));
    await renderApp("/ask");
    const nav = await screen.findByRole("navigation", { name: "Recent chats" });
    await user.click(await within(nav).findByRole("button", { name: "+3 more" }));
    await user.click(await screen.findByRole("button", { name: "Load more" }));
    await waitFor(() => expect(sent("GET", /\/chats$/).some((r) => new URL(r.url).searchParams.get("before") === "cur1")).toBe(true));
    expect(await screen.findByRole("link", { name: "Chat 9" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Load more" })).toBeNull();
  });
});

describe("model", () => {
  it("falls back to the default when chat.model is null and persists a change with PATCH {model}", async () => {
    const user = userEvent.setup();
    const c = await (await get("/chats/c1")).json();
    server.use(http.get(`${API_BASE}/chats/c1`, () => HttpResponse.json({ ...c, model: null })));
    await renderApp("/ask/c1");
    const sel = await screen.findByRole("combobox", { name: "Model" });
    await waitFor(() => expect(sel).toHaveValue("frontier-large"));
    await user.selectOptions(sel, "open-70b");
    await waitFor(() => expect(sent("PATCH", /\/chats\/c1$/)).toHaveLength(1));
    expect(await sent("PATCH", /\/chats\/c1$/)[0].json()).toEqual({ model: "open-70b" });
  });
});

describe("rating", () => {
  it("un-rates a pressed thumb and pre-fills the stored comment", async () => {
    const user = userEvent.setup();
    const c = await (await get("/chats/c1")).json();
    const messages = c.messages.map((m: { role: string }) => (m.role === "assistant" ? { ...m, rating: { value: 1, comment: null } } : m));
    server.use(http.get(`${API_BASE}/chats/c1`, () => HttpResponse.json({ ...c, messages })));
    await renderApp("/ask/c1");
    const up = await screen.findByRole("button", { name: "Helpful" });
    expect(up).toHaveAttribute("aria-pressed", "true");
    await user.click(up);
    await waitFor(() => expect(sent("PUT", /rating$/)).toHaveLength(1));
    expect(await sent("PUT", /rating$/)[0].json()).toMatchObject({ value: 0 });
  });
  it("pre-fills the thumbs-down popover with the existing comment", async () => {
    const user = userEvent.setup();
    const c = await (await get("/chats/c1")).json();
    const messages = c.messages.map((m: { role: string }) => (m.role === "assistant" ? { ...m, rating: { value: 1, comment: "was fine" } } : m));
    server.use(http.get(`${API_BASE}/chats/c1`, () => HttpResponse.json({ ...c, messages })));
    await renderApp("/ask/c1");
    await user.click(await screen.findByRole("button", { name: "Not helpful" }));
    expect(await screen.findByRole("textbox", { name: /What went wrong/ })).toHaveValue("was fine");
  });
});

describe("citations and passages", () => {
  it("opens a passage with version and bbox, and omits chunkId when null", async () => {
    const user = userEvent.setup();
    const c = await (await get("/chats/c1")).json();
    const bbox = { l: 1, r: 2, t: 3, b: 4, coord_origin: "BOTTOMLEFT" };
    const messages = c.messages.map((m: { role: string; retrieved: object[] }) => (m.role === "assistant" ? { ...m, retrieved: [{ ...m.retrieved[0], version: 7, chunk_id: null, bbox }] } : m));
    server.use(http.get(`${API_BASE}/chats/c1`, () => HttpResponse.json({ ...c, messages })));
    await renderApp("/ask/c1");
    await user.click(await screen.findByRole("button", { name: /Show the 1 passages read/ }));
    await user.click(within(await screen.findByRole("list", { name: "Passages read" })).getAllByRole("button", { name: /./ })[0]);
    const tab = getReaderState().tabs[0];
    expect(tab.version).toBe(7);
    expect(tab.bbox).toEqual(bbox);
    expect(tab.chunkId).toBeUndefined();
  });
  it("copies Markdown from citation authors and year without fetching the document", async () => {
    const user = userEvent.setup();
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
    await renderApp("/ask/c1");
    await user.click(await screen.findByRole("button", { name: "Copy as Markdown" }));
    await waitFor(() => expect(writeText).toHaveBeenCalled());
    expect(writeText.mock.calls[0][0]).toMatch(/\[1\] M\. Chen, R\. Aldana, .*, 2021, p\. 1/);
    expect(sent("GET", /\/documents\/[^/]+$/)).toHaveLength(0);
  });
  it("seeded citations match the contract, with one gone citation exactly {n,available:false}", async () => {
    const c = await (await get("/chats/c1")).json();
    const cites = c.messages[1].citations;
    expect(cites[0]).toMatchObject({ available: true, version: 2, authors: ["M. Chen", "R. Aldana"], year: 2021 });
    expect(cites.at(-1)).toEqual({ n: 6, available: false });
    expect(c.messages[1].retrieved[0]).toHaveProperty("version");
    expect(c.messages[1].retrieved[0]).toHaveProperty("bbox");
  });
});

describe("shares", () => {
  it("pre-fills from GET and saves with PUT; shows the 422 detail", async () => {
    const user = userEvent.setup();
    server.use(http.get(`${API_BASE}/chats/c1/shares`, () => HttpResponse.json({ users: ["11111111-1111-4111-8111-111111111111"], groups: ["/Math"] })));
    await renderApp("/ask/c1");
    await user.click(await screen.findByRole("button", { name: "Share" }));
    const dlg = await screen.findByRole("dialog", { name: "Share this chat" });
    expect(await within(dlg).findByText("/Math")).toBeInTheDocument();
    expect(await within(dlg).findByText("ana")).toBeInTheDocument(); // existing ids are shown by name
    server.use(http.put(`${API_BASE}/chats/c1/shares`, () => HttpResponse.json({ detail: "unknown user u-9" }, { status: 422 })));
    await user.click(within(dlg).getByRole("button", { name: "Save" }));
    expect(await screen.findByText("unknown user u-9")).toBeInTheDocument();
    expect(await sent("PUT", /shares$/)[0].json()).toEqual({ users: ["11111111-1111-4111-8111-111111111111"], groups: ["/Math"] });
  });
  it("mock stores shares per chat", async () => {
    await fetch(`${API_BASE}/chats/c1/shares`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ users: ["u1"], groups: [] }) });
    expect(await (await get("/chats/c1/shares")).json()).toEqual({ users: ["u1"], groups: [] });
  });
});

describe("non-owner chat", () => {
  it("renders with scope 'collection' without crashing, and ScopeSelect reads 'A collection'", async () => {
    await renderApp("/ask/shared-1");
    expect(await screen.findByText("Shared with you. Read-only.")).toBeInTheDocument();
    const qc = new QueryClient();
    render(<QueryClientProvider client={qc}><ScopeSelect scope="collection" onScope={() => {}} readOnly /></QueryClientProvider>);
    expect(screen.getByLabelText("Search in")).toHaveTextContent("A collection");
  });
});

describe("pre-stream errors restore the draft", () => {
  const ask = async (q: string) => { await userEvent.setup().type(await box(), `${q}{Enter}`); };
  it.each([
    [403, { detail: "read-only" }, "This chat is read-only."],
    [422, { detail: "bad scope" }, "bad scope"],
  ])("%i", async (status, body, copy) => {
    server.use(http.post(`${API_BASE}/chats/c1/messages`, () => HttpResponse.json(body, { status })));
    await renderApp("/ask/c1");
    await ask("hello there");
    expect(await screen.findByText(copy)).toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole("textbox", { name: "Question" })).toHaveValue("hello there"));
  });
  it("404 shows the chat-unavailable state and restores the quote", async () => {
    server.use(http.post(`${API_BASE}/chats/c1/messages`, () => HttpResponse.json({ detail: "not found" }, { status: 404 })));
    await renderApp("/ask/c1");
    setQuote({ doc_id: "d", page: 1, text: "quoted" });
    await ask("hello");
    expect(await screen.findByText(/This chat isn't available/)).toBeInTheDocument();
    await waitFor(() => expect(getAskContext().quote?.text).toBe("quoted"));
  });
  it("503 (the mock's magic question) fails inside the turn with a reason and Retry", async () => {
    await renderApp("/ask/c1");
    await ask("please trigger 503");
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("The answer could not be generated: answering is unavailable right now (the search or the language model is down).");
    expect(within(alert).getByRole("button", { name: "Retry" })).toBeInTheDocument();
  });
});

describe("limits", () => {
  it("composer maxLength is 4000", async () => {
    await renderApp("/ask");
    expect(await box()).toHaveAttribute("maxlength", "4000");
  });
  it("viewer selection is truncated to 4000 characters", () => {
    const v = buildViewer({ tabs: [], active: null }, [], () => null, { doc_id: "d", page: 1, text: "x".repeat(5000) });
    expect(v.selection?.text).toHaveLength(4000);
  });
});

describe("SSE inference failure", () => {
  it("maps the error event to user copy and calls onError once with no cut-off error", async () => {
    server.use(http.post(`${API_BASE}/chats/c1/messages`, () => new HttpResponse('event: token\ndata: {"text":"a"}\n\nevent: error\ndata: {"detail":"inference failed"}\n\n', { headers: { "Content-Type": "text/event-stream" } })));
    const h = { onToken: vi.fn(), onCitations: vi.fn(), onDone: vi.fn(), onError: vi.fn() };
    await streamMessage("c1", { content: "q", viewer: { tabs: [], ignored: [], selection: null } }, h);
    expect(h.onError).toHaveBeenCalledTimes(1);
    expect(h.onError).toHaveBeenCalledWith("the language model could not finish it");
    expect(h.onDone).not.toHaveBeenCalled();
  });
});
