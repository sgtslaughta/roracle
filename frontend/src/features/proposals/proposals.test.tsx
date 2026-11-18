import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider } from "@tanstack/react-router";
import { http, HttpResponse } from "msw";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { API_BASE } from "@/api/client";
import { createAppRouter } from "@/app/router";
import { DOCS } from "@/mocks/docs";
import { server } from "@/mocks/server";
import { state } from "@/mocks/state";
import { resetReaderStore } from "@/features/reader/useReaderStore";
import type { Proposal } from "./api";

vi.mock("@/features/reader/pdfjs", () => ({
  loadPdf: async () => ({
    numPages: 1,
    getPage: async () => ({ getViewport: ({ scale }: { scale: number }) => ({ width: 612 * scale, height: 792 * scale, scale }), render: () => ({ promise: Promise.resolve() }), getTextContent: async () => ({ items: [] }) }),
  }),
}));

async function renderApp(path = "/proposals") {
  const router = createAppRouter([path]);
  await router.load();
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><RouterProvider router={router} /></QueryClientProvider>);
  await screen.findByRole("navigation", { name: "Main" });
  return router;
}
const card = (name: RegExp) => screen.findByRole("article", { name });

const seen: Request[] = [];
beforeEach(() => {
  resetReaderStore(); seen.length = 0;
  server.events.on("request:start", ({ request }) => { seen.push(request.clone()); });
});
afterEach(() => { server.events.removeAllListeners(); });
const posts = (suffix: string) => seen.filter((r) => r.method === "POST" && new URL(r.url).pathname.endsWith(suffix));
async function bodyOf(suffix: string) {
  await waitFor(() => expect(posts(suffix)).toHaveLength(1));
  const t = await posts(suffix)[0].text();
  return t ? JSON.parse(t) : null;
}

function many(n: number): Proposal[] {
  return Array.from({ length: n }, (_, i) => ({
    id: `bulk-${i}`, kind: "note", client: "cursor", status: "pending", annotation_id: null, created_at: "2026-11-15T09:00:00Z", decided_at: null,
    payload: { document_id: DOCS[0].id, kind: "comment", body: `note ${i}` }, document: { id: DOCS[0].id, title: DOCS[0].title },
  }));
}

describe("proposals inbox", () => {
  it("token clients read as 'your token' and a no-op label change can't be approved", async () => {
    state.proposals = [{ id: "p-noop", kind: "label", client: "pat:test", status: "pending", annotation_id: null, created_at: "2026-11-15T09:00:00Z", decided_at: null,
      payload: { document_id: DOCS[0].id, body: "", label_id: "l-internal" }, document: { id: DOCS[0].id, title: DOCS[0].title } }];
    await renderApp();
    expect(await screen.findByRole("region", { name: "From your token “test”" })).toBeInTheDocument();
    expect(await screen.findByText(/approving changes nothing/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Approve" })).toBeDisabled();
    // a document label change has no note to look up: no request for an empty document id
    expect(seen.some((r) => new URL(r.url).pathname.includes("/documents//"))).toBe(false);
  });

  it("groups pending proposals by client, marks them agent-assisted and shows payload text as plain text", async () => {
    await renderApp();
    const group = await screen.findByRole("region", { name: "From claude-code" });
    expect(within(group).getByText("4 waiting")).toBeInTheDocument();
    const f = await card(/Finding proposed by claude-code/);
    expect(within(f).getByText(/Agent-assisted, from claude-code/)).toBeInTheDocument();
    expect(within(f).getByText("Add a finding to")).toBeInTheDocument();
    expect(within(f).getByText(/<b>Raw<\/b> numbers/)).toBeInTheDocument();
    expect(f.querySelector(".ptext b")).toBeNull();
    expect(f.querySelector("blockquote")).not.toBeNull();
    const r = await card(/Relation proposed by/);
    expect(within(r).getByText("extends →")).toBeInTheDocument();
  });

  it("sidebar badge counts pending proposals and drops after a decision", async () => {
    const user = userEvent.setup();
    await renderApp();
    expect(await screen.findByLabelText("4 waiting")).toBeInTheDocument();
    await user.click(within(await card(/Comment proposed by/)).getByRole("button", { name: "Reject" }));
    expect(await bodyOf("/p-comment/reject")).toBeNull();
    expect(await screen.findByLabelText("3 waiting")).toBeInTheDocument();
    expect(await screen.findByText("Proposal rejected.")).toBeInTheDocument();
  });

  it("approve as proposed sends an empty body and the note lands on the document as agent-assisted", async () => {
    const user = userEvent.setup();
    await renderApp();
    await user.click(within(await card(/Finding proposed by/)).getByRole("button", { name: "Approve" }));
    expect(await bodyOf("/p-finding/approve")).toEqual({});
    await waitFor(() => expect(state.proposals!.find((p) => p.id === "p-finding")!.status).toBe("approved"));
    const n = state.notes!.find((x) => x.id === state.proposals!.find((p) => p.id === "p-finding")!.annotation_id)!;
    expect(n).toMatchObject({ document_id: DOCS[0].id, kind: "finding", agent_assisted: true, visibility: "private" });
    await waitFor(() => expect(screen.queryByRole("article", { name: /Finding proposed by/ })).toBeNull());
  });

  it("edit then approve sends body and visibility", async () => {
    const user = userEvent.setup();
    await renderApp();
    const c = await card(/Comment proposed by/);
    await user.click(within(c).getByRole("button", { name: "Edit" }));
    const box = within(c).getByRole("textbox", { name: "Note" });
    await user.clear(box);
    await user.type(box, "Tightened wording");
    await user.selectOptions(within(c).getByRole("combobox", { name: "Visible to" }), "everyone");
    await user.click(within(c).getByRole("button", { name: "Approve with edits" }));
    expect(await bodyOf("/p-comment/approve")).toEqual({ body: "Tightened wording", visibility: "everyone" });
  });

  it("approve all chunks by 100 and reports partial failures", async () => {
    state.proposals = many(150);
    server.use(http.post(`${API_BASE}/proposals/bulk-approve`, async ({ request }) => {
      const { ids } = (await request.json()) as { ids: string[] };
      const [bad, ...ok] = ids;
      return HttpResponse.json({ approved: ok.map((proposal_id) => ({ proposal_id, annotation_id: "n" })), failed: [{ proposal_id: bad, detail: "proposal already approved" }] });
    }));
    const user = userEvent.setup();
    await renderApp();
    await screen.findByRole("region", { name: "From cursor" });
    await user.click(screen.getByRole("button", { name: "Approve all…" }));
    await user.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Approve all" }));
    await waitFor(() => expect(posts("/bulk-approve")).toHaveLength(2));
    const sizes = await Promise.all(posts("/bulk-approve").map(async (r) => ((await r.json()) as { ids: string[] }).ids.length));
    expect(sizes).toEqual([100, 50]);
    expect(await screen.findByText("Approved 148. 2 couldn't be approved: proposal already approved")).toBeInTheDocument();
  });

  it("approve selected sends only the checked ids", async () => {
    const user = userEvent.setup();
    await renderApp();
    await user.click(await screen.findByRole("checkbox", { name: /Select Comment on/ }));
    await user.click(screen.getByRole("button", { name: "Approve selected (1)" }));
    expect(await bodyOf("/bulk-approve")).toEqual({ ids: ["p-comment"] });
    expect(await screen.findByText("Approved 1 proposal.")).toBeInTheDocument();
  });

  it("missing document and approved tab with a link to the note", async () => {
    const [first] = many(1);
    state.proposals = [{ ...first, document: null }, { ...many(2)[1], id: "done", status: "approved", annotation_id: "n-finding", decided_at: "2026-11-15T10:00:00Z" }];
    const user = userEvent.setup();
    await renderApp();
    expect(await screen.findByText("a document that is no longer available")).toBeInTheDocument();
    await user.click(screen.getByRole("tab", { name: "Approved" }));
    await user.click(await screen.findByRole("button", { name: "Open note" }));
    expect(await screen.findByRole("complementary", { name: "Reader" })).toBeInTheDocument();
  });

  it("editing a reply sends only the body", async () => {
    const user = userEvent.setup();
    await renderApp();
    const c = await card(/Reply proposed by/);
    await user.click(within(c).getByRole("button", { name: "Edit" }));
    expect(within(c).queryByRole("combobox", { name: "Visible to" })).toBeNull();
    const box = within(c).getByRole("textbox", { name: "Reply" });
    await user.clear(box);
    await user.type(box, "Shorter reply");
    await user.click(within(c).getByRole("button", { name: "Approve with edits" }));
    expect(await bodyOf("/p-reply/approve")).toEqual({ body: "Shorter reply" });
    await waitFor(() => expect(state.notes!.some((n) => n.parent_id === "n-question" && n.body === "Shorter reply" && n.agent_assisted)).toBe(true));
  });

  it("editing to shared sends the shares", async () => {
    const user = userEvent.setup();
    await renderApp();
    const c = await card(/Comment proposed by/);
    await user.click(within(c).getByRole("button", { name: "Edit" }));
    await user.selectOptions(within(c).getByRole("combobox", { name: "Visible to" }), "shared");
    await user.type(within(c).getByRole("textbox", { name: "Groups" }), "/Mathematics{Enter}");
    await user.type(within(c).getByRole("textbox", { name: "People (user IDs)" }), "u-ruiz");
    await user.click(within(c).getByRole("button", { name: "Approve with edits" }));
    expect(await bodyOf("/p-comment/approve")).toEqual({
      body: "Section 4 restates Lemma 2 without the polylog factor.", visibility: "shared",
      shares: [{ kind: "group", principal: "/Mathematics" }, { kind: "user", principal: "u-ruiz" }],
    });
  });

  it("a failed bulk request reports a user-facing reason", async () => {
    server.use(http.post(`${API_BASE}/proposals/bulk-approve`, () => HttpResponse.json({ detail: "boom" }, { status: 500 })));
    const user = userEvent.setup();
    await renderApp();
    await screen.findByRole("region", { name: "From claude-code" });
    await user.click(screen.getByRole("button", { name: "Approve all…" }));
    await user.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Approve all" }));
    expect(await screen.findByText("Approved 0. 4 couldn't be approved: The request failed. Try again.")).toBeInTheDocument();
  });

  it("does not link a relation target that isn't a document id", async () => {
    const [p] = many(1);
    state.proposals = [{ ...p, kind: "relation", payload: { ...p.payload, kind: "relation", relation: { type: "extends", target_document_id: "../admin" } } }];
    await renderApp();
    await card(/Relation proposed by/);
    expect(screen.queryByText("extends →")).toBeNull();
  });
});
