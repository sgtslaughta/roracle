import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider } from "@tanstack/react-router";
import { http, HttpResponse } from "msw";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { API_BASE } from "@/api/client";
import { createAppRouter } from "@/app/router";
import { server } from "@/mocks/server";
import { openDoc, resetReaderStore } from "@/features/reader/useReaderStore";
import { resetAskTurns } from "./askStore";
import { OPEN_PAPER, TEMPLATES } from "./quickPrompts";
import { resetAskContext } from "./useAskContext";

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

async function renderApp() {
  const router = createAppRouter(["/ask"]);
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
const posts = () => seen.filter((r) => r.method === "POST" && /\/messages$/.test(new URL(r.url).pathname));
const box = () => screen.findByRole("textbox", { name: "Question" }) as Promise<HTMLTextAreaElement>;
const names = () => screen.queryAllByRole("button").map((b) => b.textContent).filter((t) => t && [...OPEN_PAPER, ...TEMPLATES].includes(t));

describe("quick prompts", () => {
  it("shows templates with no open paper and the paper prompts when one is open", async () => {
    await renderApp();
    await box();
    expect(names()).toEqual(TEMPLATES);
  });

  it("shows paper prompts for an open tab and sends the exact text with it in the viewer", async () => {
    openDoc(D1, { page: 2 });
    await renderApp();
    await screen.findByRole("group", { name: "Ask about this paper" });
    expect(names()).toEqual(OPEN_PAPER);
    await userEvent.setup().click(screen.getByRole("button", { name: "Key findings" }));
    await waitFor(() => expect(posts()).toHaveLength(1));
    const body = await posts()[0].json();
    expect(body.content).toBe("Key findings");
    expect(body.viewer.tabs.map((t: { doc_id: string }) => t.doc_id)).toEqual([D1]);
  });

  it("a template fills the composer, focuses it and selects the placeholder", async () => {
    await renderApp();
    const area = await box();
    await userEvent.setup().click(screen.getByRole("button", { name: "Compare … and …" }));
    expect(area).toHaveValue("Compare … and …");
    expect(area).toHaveFocus();
    expect([area.selectionStart, area.selectionEnd]).toEqual([8, 9]);
    expect(posts()).toHaveLength(0);
    expect(names()).toEqual([]); // hidden once the composer has text
  });

  it("is hidden while a turn streams", async () => {
    server.use(http.post(`${API_BASE}/chats/:id/messages`, () =>
      new HttpResponse(new ReadableStream({ start() { /* stays open */ } }), { headers: { "Content-Type": "text/event-stream", "Content-Length": "1000000" } })));
    openDoc(D1);
    await renderApp();
    await userEvent.setup().click(await screen.findByRole("button", { name: "Limitations" }));
    await screen.findByRole("button", { name: "Stop" });
    expect(names()).toEqual([]);
    await waitFor(async () => expect(await box()).toHaveFocus());
  });

  it("shows which paper is summarized and the reading progress", async () => {
    const enc = new TextEncoder();
    const ev = (e: string, d: unknown) => enc.encode(`event: ${e}\ndata: ${JSON.stringify(d)}\n\n`);
    server.use(http.post(`${API_BASE}/chats/:id/messages`, () =>
      new HttpResponse(new ReadableStream({ start(c) {
        c.enqueue(ev("reading", { part: 2, of: 5 }));
        c.enqueue(ev("meta", { summarize: { doc_id: D1, title: "Spectral gaps", version: 2 } }));
      } }), { headers: { "Content-Type": "text/event-stream", "Content-Length": "1000000" } })));
    openDoc(D1);
    await renderApp();
    await userEvent.setup().click(await screen.findByRole("button", { name: "Summarize this paper" }));
    expect(await screen.findByText("Summarizing Spectral gaps (v2)")).toBeInTheDocument();
    expect(await screen.findByText("Reading part 2 of 5…")).toBeInTheDocument();
  });
});
