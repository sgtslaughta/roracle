import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider } from "@tanstack/react-router";
import { http, HttpResponse } from "msw";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { API_BASE } from "@/api/client";
import { createAppRouter } from "@/app/router";
import { server } from "@/mocks/server";
import { state } from "@/mocks/state";
import { resetReaderStore } from "@/features/reader/useReaderStore";
import { resetAskTurns } from "@/features/ask/askStore";
import { resetAskContext } from "@/features/ask/useAskContext";

vi.mock("@/features/reader/pdfjs", () => ({
  loadPdf: async () => ({
    numPages: 1,
    getPage: async () => ({ getViewport: ({ scale }: { scale: number }) => ({ width: 612 * scale, height: 792 * scale, scale }), render: () => ({ promise: Promise.resolve() }) }),
  }),
}));

const D1 = "00000000-0000-4000-8000-000000000001";

async function renderApp(path: string) {
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
const posted = async () => {
  await waitFor(() => expect(seen.filter((r) => r.method === "POST" && new URL(r.url).pathname.endsWith("/handoffs"))).toHaveLength(1));
  return seen.find((r) => r.method === "POST" && new URL(r.url).pathname.endsWith("/handoffs"))!.json();
};
const openDialog = async (user: ReturnType<typeof userEvent.setup>, scope: () => HTMLElement = () => document.body) => {
  await user.click((await within(scope()).findAllByRole("button", { name: "Continue in agent" }))[0]);
  return screen.findByRole("dialog", { name: "Continue in an agent" });
};

describe("continue in agent", () => {
  it("chat answer: kind chat with the chat id, creator-only by default, then shows the code and prompt", async () => {
    const user = userEvent.setup();
    await renderApp("/ask/c1");
    const dlg = await openDialog(user);
    expect(within(dlg).getByLabelText("Brief the agent starts from")).not.toHaveValue("");
    await user.click(within(dlg).getByRole("button", { name: "Create handoff link" }));
    const body = await posted();
    expect(body).toMatchObject({ kind: "chat", source_id: "c1", audience: "creator", expires_in_days: 7 });
    expect(body).not.toHaveProperty("shares");
    const code = Object.values(state.handoffs)[0].m.display;
    expect(await within(dlg).findByText(code)).toBeInTheDocument();
    expect(within(dlg).getByRole("button", { name: "Copy link" })).toBeInTheDocument();
    expect(within(dlg).getByRole("button", { name: "Copy agent prompt" })).toBeInTheDocument();
  });

  it("sends shares only for the shared audience", async () => {
    const user = userEvent.setup();
    await renderApp("/ask/c1");
    const dlg = await openDialog(user);
    await user.selectOptions(within(dlg).getByLabelText("Who can load it"), "shared");
    expect(within(dlg).getByRole("button", { name: "Create handoff link" })).toBeDisabled();
    await user.type(within(dlg).getByLabelText("Groups"), "/Mathematics{Enter}");
    await user.selectOptions(within(dlg).getByLabelText("Expires"), "30");
    await user.click(within(dlg).getByRole("button", { name: "Create handoff link" }));
    expect(await posted()).toMatchObject({ audience: "shared", shares: [{ kind: "group", principal: "/Mathematics" }], expires_in_days: 30 });
  });

  it("reader: kind document with the open document", async () => {
    const user = userEvent.setup();
    await renderApp(`/d/${D1}`);
    const reader = await screen.findByRole("complementary", { name: "Reader" });
    await within(reader).findByRole("heading", { level: 2 });
    const dlg = await openDialog(user, () => reader);
    await user.click(within(dlg).getByRole("button", { name: "Create handoff link" }));
    expect(await posted()).toMatchObject({ kind: "document", source_id: D1, doc_ids: [D1] });
  });

  it("library search: kind search with the query as goal", async () => {
    const user = userEvent.setup();
    await renderApp("/library?q=spectral");
    await screen.findAllByRole("listitem");
    const dlg = await openDialog(user);
    await user.click(within(dlg).getByRole("button", { name: "Create handoff link" }));
    const body = await posted();
    expect(body).toMatchObject({ kind: "search", goal: expect.stringContaining("spectral") });
    expect(body).not.toHaveProperty("source_id");
  });

  it("library with one collection filter: kind collection with its id", async () => {
    const user = userEvent.setup();
    await renderApp("/library?collection=c1");
    const dlg = await openDialog(user);
    await user.click(within(dlg).getByRole("button", { name: "Create handoff link" }));
    expect(await posted()).toMatchObject({ kind: "collection", source_id: "c1" });
  });
});

describe("handoff landing", () => {
  it("shows the manifest as plain text with its documents and withheld count", async () => {
    state.handoffs.ABCDEFGH = {
      revoked_at: null, shares: [],
      m: {
        code: "ABCDEFGH", display: "ABCD-EFGH", url: "/h/ABCD-EFGH", uri: "roracle://handoff/ABCDEFGH", kind: "document",
        title: "Hypergraph gap", goal: "Check the constant", brief: "<b>not bold</b>\nline two", source: {}, doc_ids: [D1], passages: [],
        notes: [{ at: "2026-11-01T10:00:00Z", by: "agent-bot", text: "Ran the sweep", via: "mcp" }], withheld: 2,
        parts: { transcript: 0, notes: 1 }, audience: "authenticated", expires_at: "2099-01-01T00:00:00Z", created_at: "2026-11-01T09:00:00Z",
        created_by: "k.ito", created_via: "ui",
      },
    };
    await renderApp("/h/abcd-efgh");
    expect(await screen.findByRole("heading", { level: 1, name: "Hypergraph gap" })).toBeInTheDocument();
    expect(screen.getByText(/<b>not bold<\/b>/)).toBeInTheDocument();
    expect(document.querySelector("main b")?.textContent).not.toBe("not bold");
    expect(screen.getByText("2 sources withheld", { exact: false })).toBeInTheDocument();
    expect(screen.getByText("Ran the sweep")).toBeInTheDocument();
    expect(await screen.findByRole("button", { name: /Lower bounds on the spectral gap/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Copy agent prompt" })).toBeInTheDocument();
  });

  it("404 says not found or not shared", async () => {
    await renderApp("/h/ZZZZ-ZZZZ");
    expect(await screen.findByText(/not found or not shared with you/i)).toBeInTheDocument();
  });

  it.each([["handoff expired", /expired/i], ["handoff revoked", /revoked/i]])("410 %s", async (detail, re) => {
    server.use(http.get(`${API_BASE}/handoffs/:code`, () => HttpResponse.json({ detail }, { status: 410 })));
    await renderApp("/h/ABCD-EFGH");
    const msg = await screen.findByRole("alert");
    expect(msg.textContent).toMatch(re);
  });
});

describe("handoffs panel", () => {
  it("lists my handoffs and revokes after confirmation", async () => {
    const user = userEvent.setup();
    await renderApp("/ask/c1");
    const dlg = await openDialog(user);
    await user.clear(within(dlg).getByLabelText("Title"));
    await user.type(within(dlg).getByLabelText("Title"), "Gap handoff");
    await user.click(within(dlg).getByRole("button", { name: "Create handoff link" }));
    await within(dlg).findByRole("button", { name: "Copy agent prompt" });
    await user.keyboard("{Escape}");
    cleanupRender();
    await renderApp("/settings");
    await user.click(await screen.findByRole("tab", { name: "Handoffs" }));
    const row = (await screen.findByText("Gap handoff")).closest("li")!;
    await user.click(within(row).getByRole("button", { name: "Revoke Gap handoff" }));
    await user.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Revoke" }));
    await waitFor(() => expect(seen.some((r) => r.method === "DELETE" && /\/handoffs\/[A-Z0-9]{8}$/.test(new URL(r.url).pathname))).toBe(true));
    expect(await within(row).findByText("Revoked")).toBeInTheDocument();
  });
});

function cleanupRender() { cleanup(); resetReaderStore(); }
