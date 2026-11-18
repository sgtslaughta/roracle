import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider } from "@tanstack/react-router";
import { http, HttpResponse } from "msw";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { API_BASE } from "@/api/client";
import type { Label } from "@/api/types";
import { createAppRouter } from "@/app/router";
import { LabelPicker } from "@/components/LabelPicker";
import { SensitivityBadge, textOn } from "@/components/SensitivityBadge";
import { ExcludedNote } from "@/features/ask/ExcludedNote";
import { streamMessage } from "@/features/ask/stream";
import { NoteComposer } from "@/features/notes/NoteComposer";
import { resetReaderStore } from "@/features/reader/useReaderStore";
import { DOCS } from "@/mocks/docs";
import { D1 } from "@/mocks/drafts";
import { labelJson, labels, SEED_LABELS } from "@/mocks/labels";
import { server } from "@/mocks/server";
import { state } from "@/mocks/state";
import type { Proposal } from "@/features/proposals/api";

vi.mock("@/features/reader/pdfjs", () => ({
  loadPdf: async () => ({
    numPages: 1,
    getPage: async () => ({ getViewport: ({ scale }: { scale: number }) => ({ width: 612 * scale, height: 792 * scale, scale }), render: () => ({ promise: Promise.resolve() }), getTextContent: async () => ({ items: [] }) }),
  }),
}));

const mkClient = () => new QueryClient({ defaultOptions: { queries: { retry: false } } });
const wrap = (ui: ReactNode) => render(<QueryClientProvider client={mkClient()}>{ui}</QueryClientProvider>);
async function renderApp(path: string) {
  const router = createAppRouter([path]);
  await router.load();
  render(<QueryClientProvider client={mkClient()}><RouterProvider router={router} /></QueryClientProvider>);
  return router;
}
const [GEN, INTERNAL, CONF] = SEED_LABELS.map((l) => labelJson(l.id));
const DOC_MINE = DOCS[0].id, DOC_CONF = DOCS[1].id;

const seen: Request[] = [];
beforeEach(() => {
  resetReaderStore(); seen.length = 0;
  server.events.on("request:start", ({ request }) => { seen.push(request.clone()); });
});
afterEach(() => { server.events.removeAllListeners(); });
const sent = (method: string, re: RegExp) => seen.filter((r) => r.method === method && re.test(new URL(r.url).pathname));

describe("badge", () => {
  it("shows colour and name as text, with the description as tooltip, and picks readable text", async () => {
    wrap(<SensitivityBadge label={CONF} />);
    const b = await screen.findByText("Confidential");
    expect(b.closest(".slabel")).toHaveStyle({ background: "#a02a2a" });
    expect(screen.getByText("Sensitivity:")).toBeInTheDocument(); // accessible text, never colour alone
    await waitFor(() => expect(b.closest(".slabel")).toHaveAttribute("title", expect.stringContaining("Restricted handling.")));
    expect(textOn("#ffffff")).toBe("#111");
    expect(textOn("#000000")).toBe("#fff");
  });
});

describe("picker", () => {
  const labels = SEED_LABELS.map((l) => labelJson(l.id));
  it("a non-admin cannot pick a lower label and is told why", () => {
    render(<LabelPicker labels={labels} current={INTERNAL} isAdmin={false} onApply={() => {}} />);
    expect(screen.getByRole("option", { name: /General \(only admins can lower a label\)/ })).toBeDisabled();
    expect(screen.getByRole("option", { name: "Confidential" })).toBeEnabled();
    expect(screen.getByText(/only admins can lower a label/, { selector: "p" })).toBeInTheDocument();
  });
  it("an admin lowering from a reason_on_lower label must give a reason", async () => {
    const apply = vi.fn();
    render(<LabelPicker labels={labels} current={CONF} isAdmin onApply={apply} />);
    await userEvent.selectOptions(screen.getByLabelText("Sensitivity label"), "General");
    const apply$ = screen.getByRole("button", { name: "Apply label" });
    expect(apply$).toBeDisabled();
    await userEvent.type(screen.getByLabelText(/Reason for lowering \(required\)/), "Cleared by legal");
    await userEvent.click(apply$);
    expect(apply).toHaveBeenCalledWith(GEN.id, "Cleared by legal");
  });
  it("raising needs no reason", async () => {
    const apply = vi.fn();
    render(<LabelPicker labels={labels} current={GEN} isAdmin={false} onApply={apply} />);
    await userEvent.selectOptions(screen.getByLabelText("Sensitivity label"), "Confidential");
    await userEvent.click(screen.getByRole("button", { name: "Apply label" }));
    expect(apply).toHaveBeenCalledWith(CONF.id, undefined);
  });
});

describe("library", () => {
  it("shows a badge on each card and the label facet filters results", async () => {
    await renderApp("/library");
    await screen.findByText(/of \d+ papers/);
    expect(document.querySelectorAll(".result .slabel").length).toBeGreaterThan(0);
    const g = await screen.findByRole("group", { name: "Sensitivity" });
    await userEvent.click(within(g).getByRole("checkbox", { name: "Include Sensitivity: Confidential" }));
    await waitFor(() => expect(document.querySelectorAll(".result")).toHaveLength(1));
    expect(sent("GET", /\/search$/).some((r) => new URL(r.url).searchParams.get("label") === CONF.id)).toBe(true);
    expect(document.querySelector(".result .slabel")).toHaveTextContent("Confidential");
    expect(screen.getByRole("button", { name: "Remove: labelled Confidential" })).toBeInTheDocument();
  });
  it("orders label facet values by rank", async () => {
    await renderApp("/library");
    const g = await screen.findByRole("group", { name: "Sensitivity" });
    const names = within(g).getAllByRole("checkbox").map((c) => c.getAttribute("aria-label"));
    expect(names).toEqual(["Include Sensitivity: General", "Include Sensitivity: Internal", "Include Sensitivity: Confidential"]);
  });
});

describe("reader", () => {
  it("disables download with a tooltip when the label blocks export and the viewer is not the owner", async () => {
    await renderApp(`/d/${DOC_CONF}`);
    const btn = await screen.findByRole("button", { name: "Download original" });
    expect(btn).toBeDisabled();
    expect(btn).toHaveAttribute("title", "Downloads are disabled by the Confidential label");
    expect(screen.queryByRole("button", { name: /Change label/ })).not.toBeInTheDocument();
  });
  it("owners keep download and can raise the label; the request carries no reason", async () => {
    await renderApp(`/d/${DOC_MINE}`);
    expect(await screen.findByRole("button", { name: "Download original" })).toBeEnabled();
    await userEvent.click(await screen.findByRole("button", { name: /Change label of/ }));
    const dlg = await screen.findByRole("dialog");
    expect(within(dlg).getByRole("option", { name: /General \(only admins can lower a label\)/ })).toBeDisabled();
    await userEvent.selectOptions(within(dlg).getByLabelText("Sensitivity label"), "Confidential");
    await userEvent.click(within(dlg).getByRole("button", { name: "Apply label" }));
    await waitFor(() => expect(sent("PUT", /\/label$/)).toHaveLength(1));
    expect(await sent("PUT", /\/label$/)[0].json()).toEqual({ label_id: CONF.id });
    await waitFor(() => expect(screen.getByRole("button", { name: "Download original" })).toBeEnabled()); // owner stays exempt
    expect(await screen.findAllByText("Confidential")).not.toHaveLength(0);
  });
  it("admins lowering from Confidential are asked for a reason", async () => {
    state.admin = true;
    await renderApp(`/d/${DOC_CONF}`);
    await userEvent.click(await screen.findByRole("button", { name: /Change label of/ }));
    const dlg = await screen.findByRole("dialog");
    await userEvent.selectOptions(within(dlg).getByLabelText("Sensitivity label"), "General");
    expect(within(dlg).getByRole("button", { name: "Apply label" })).toBeDisabled();
    await userEvent.type(within(dlg).getByLabelText(/Reason for lowering/), "Declassified");
    await userEvent.click(within(dlg).getByRole("button", { name: "Apply label" }));
    await waitFor(() => expect(sent("PUT", /\/label$/)).toHaveLength(1));
    expect(await sent("PUT", /\/label$/)[0].json()).toEqual({ label_id: GEN.id, reason: "Declassified" });
  });
});

describe("notes", () => {
  it("the composer offers only labels above the document's", async () => {
    wrap(<NoteComposer docId={DOC_MINE} onDone={() => {}} />);
    const sel = await screen.findByLabelText(/Sensitivity label/);
    expect(await within(sel).findByRole("option", { name: "Same as the document (Internal)" })).toBeInTheDocument();
    expect(within(sel).getByRole("option", { name: "Confidential" })).toBeInTheDocument();
    expect(within(sel).queryByRole("option", { name: "General" })).not.toBeInTheDocument();
  });
});

describe("ask", () => {
  it("an SSE meta frame reports excluded_by_label", async () => {
    server.use(http.post(`${API_BASE}/chats/c9/messages`, () => new HttpResponse(
      `event: meta\ndata: ${JSON.stringify({ excluded_by_label: [{ doc_id: DOC_CONF, label: "Confidential" }] })}\n\nevent: done\ndata: {"message_id":"m1"}\n\n`,
      { headers: { "Content-Type": "text/event-stream" } },
    )));
    const onExcluded = vi.fn();
    await streamMessage("c9", { content: "q", viewer: { tabs: [], ignored: [], selection: null } }, { onToken() {}, onCitations() {}, onExcluded, onDone() {}, onError() {} });
    expect(onExcluded).toHaveBeenCalledWith([{ doc_id: DOC_CONF, label: "Confidential" }]);
  });
  it("shows a note naming what was left out, capped with +K", async () => {
    const items = Array.from({ length: 5 }, (_, i) => ({ doc_id: i === 0 ? DOC_CONF : `x${i}`, label: "Confidential" }));
    wrap(<ExcludedNote items={items} />);
    expect(screen.getByRole("note")).toHaveTextContent(/Not used for this answer/);
    expect(await screen.findByText(DOCS[1].title)).toBeInTheDocument();
    expect(screen.getByText("+2")).toBeInTheDocument();
  });
});

describe("uploads", () => {
  it("preselects the default label and sends a raised one", async () => {
    await renderApp("/uploads");
    await screen.findByText("chen-aldana-preprint.pdf");
    const sel = (await screen.findByLabelText("Sensitivity label")) as HTMLSelectElement;
    await waitFor(() => expect(sel.value).toBe(GEN.id));
    expect(within(sel).getByRole("option", { name: "General (default)" })).toBeInTheDocument();
    await userEvent.selectOptions(sel, "Internal");
    await userEvent.upload(screen.getByLabelText(/Drop files here or choose files/), new File(["x"], "fresh.pdf", { type: "application/pdf" }));
    await waitFor(() => expect(sent("POST", /\/documents$/)).toHaveLength(1));
    expect((await sent("POST", /\/documents$/)[0].formData()).get("label_id")).toBe(INTERNAL.id);
  });
});

describe("proposals", () => {
  const label = (to: Label, doc = DOCS[0]): Proposal => ({
    id: "p-label", kind: "label", client: "claude-code", status: "pending", annotation_id: null, created_at: "2026-11-16T09:00:00Z", decided_at: null,
    payload: { document_id: doc.id, label_id: to.id, body: "" }, document: { id: doc.id, title: doc.title },
  });
  it("renders a label proposal and approves a raise", async () => {
    state.proposals = [label(CONF)];
    await renderApp("/proposals");
    const card = await screen.findByRole("article", { name: /Label change proposed by claude-code/ });
    expect(await within(card).findByText("Confidential")).toBeInTheDocument();
    expect(within(card).getByText("Internal")).toBeInTheDocument();
    await userEvent.click(within(card).getByRole("button", { name: "Approve" }));
    await waitFor(() => expect(state.docLabels[DOC_MINE]).toBe(CONF.id));
  });
  it("a note label change is compared with the note's own label and shows the agent's reason", async () => {
    state.admin = true;
    state.noteLabels["n-finding"] = CONF.id; // the note is Confidential; its document is Internal
    state.proposals = [{ ...label(INTERNAL), payload: { document_id: DOC_MINE, label_id: INTERNAL.id, body: "", annotation_id: "n-finding", reason: "looks public" } }];
    await renderApp("/proposals");
    const card = await screen.findByRole("article", { name: /Label change proposed by claude-code/ });
    expect(await within(card).findByText("Note label change")).toBeInTheDocument();
    expect(await within(card).findByText(/Lowers the label/)).toBeInTheDocument();
    expect(within(card).getByLabelText(/Reason for lowering \(required\)/)).toBeInTheDocument();
    expect(within(card).getByText(/looks public/)).toBeInTheDocument();
    expect(within(card).getByRole("button", { name: "Approve" })).toBeDisabled();
  });
  it("a lowering by a non-admin is refused and the API error shown", async () => {
    state.proposals = [label(GEN)];
    await renderApp("/proposals");
    const card = await screen.findByRole("article", { name: /Label change/ });
    await userEvent.click(await within(card).findByRole("button", { name: "Approve" }));
    expect(await within(card).findByRole("alert")).toHaveTextContent("only admins can lower a label");
  });
});

describe("admin labels page", () => {
  beforeEach(() => { state.admin = true; });
  const names = (list: HTMLElement) => within(list).getAllByRole("listitem").map((li) => /General|Internal|Confidential/.exec(li.textContent ?? "")?.[0]);
  it("reorders with the arrows and shows the new order", async () => {
    labels().find((l) => l.id === INTERNAL.id)!.gates = { ...GEN.gates }; // equal gates: either order is valid
    await renderApp("/admin/labels");
    const list = await screen.findByRole("list", { name: /Sensitivity labels/ });
    await within(list).findByText("Confidential");
    await userEvent.click(within(list).getByRole("button", { name: "Move Internal up" }));
    await waitFor(() => expect(sent("PUT", /\/admin\/labels\/order$/)).toHaveLength(1));
    expect((await sent("PUT", /order$/)[0].json()).ids).toEqual([INTERNAL.id, GEN.id, CONF.id]);
    await waitFor(() => expect(names(list)).toEqual(["Internal", "General", "Confidential"]));
  });
  it("shows the API's error when a reorder breaks the gates and keeps the order", async () => {
    await renderApp("/admin/labels");
    const list = await screen.findByRole("list", { name: /Sensitivity labels/ });
    await within(list).findByText("Confidential");
    await userEvent.click(within(list).getByRole("button", { name: "Move Confidential up" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("gates must not decrease with rank");
    expect(names(list)).toEqual(["General", "Internal", "Confidential"]);
  });
  it("shows the API's monotonic-gates error next to the toggle", async () => {
    await renderApp("/admin/labels");
    await userEvent.click(await screen.findByRole("checkbox", { name: "General: Kept out of AI answers" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("gates must not decrease with rank");
  });
  it("creates a label and refuses to delete the default", async () => {
    await renderApp("/admin/labels");
    await userEvent.click(await screen.findByRole("button", { name: /New label/ }));
    const dlg = await screen.findByRole("dialog");
    await userEvent.type(within(dlg).getByLabelText("Name"), "Embargoed");
    await userEvent.click(within(dlg).getByRole("button", { name: "Create" }));
    expect(await screen.findByText("Embargoed")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Actions for General" }));
    await userEvent.click(await screen.findByRole("menuitem", { name: "Delete…" }));
    await userEvent.click(await screen.findByRole("button", { name: "Delete label" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("label in use");
  });
});

describe("handoff manifest", () => {
  it("shows each document's label", async () => {
    const m = {
      code: "ABCD2345", display: "abcd-2345", url: "/h/abcd-2345", uri: "roracle://handoff/ABCD2345", kind: "document", title: "Pack", goal: "", brief: "", source: {},
      doc_ids: [DOC_CONF], documents: [{ id: DOC_CONF, title: "Secret paper", label: CONF, text_withheld: true }], passages: [], notes: [], withheld: 0, withheld_by_reason: { unavailable: 0, label_text: 1 },
      parts: { transcript: 0, notes: 0 }, audience: "owner", expires_at: "2027-01-01T00:00:00Z", created_at: "2026-11-16T00:00:00Z", created_by: "researcher", created_via: "ui",
    };
    server.use(http.get(`${API_BASE}/handoffs/:code`, () => HttpResponse.json(m)));
    await renderApp("/h/ABCD2345");
    const li = (await screen.findByText("Secret paper")).closest("li")!;
    expect(within(li).getByText("Confidential")).toBeInTheDocument();
    expect(within(li).getByText(/Text withheld by its label/)).toBeInTheDocument();
  });
});

describe("drafts", () => {
  it("loads stored check reports from GET /drafts/{id}/checks", async () => {
    server.use(http.get(`${API_BASE}/drafts/${D1}/checks`, () => HttpResponse.json({
      version: 2, citations: null,
      validation: { version: 2, profile: { name: "journal-article", version: 1, source: "builtin" }, checked_by: "u-me", ok: true, blocking: [], warnings: [] },
    })));
    await renderApp(`/drafts/${D1}`);
    const v = await screen.findByLabelText("Validation results");
    expect(within(v).getByText("No blocking findings")).toBeInTheDocument();
    expect(sent("GET", /\/checks$/)).toHaveLength(1);
  });
  it("the submit dialog can raise the label", async () => {
    await renderApp(`/drafts/${D1}`);
    await userEvent.click(await screen.findByRole("button", { name: /^Submit/ }));
    const dlg = await screen.findByRole("dialog");
    const sel = await within(dlg).findByLabelText(/Sensitivity label/);
    expect(within(sel).getByRole("option", { name: /Keep the draft's label/ })).toBeInTheDocument();
    expect(within(sel).getByRole("option", { name: "Confidential" })).toBeInTheDocument();
    await userEvent.selectOptions(sel, "Confidential");
    await userEvent.click(within(dlg).getByRole("button", { name: "Submit" }));
    await waitFor(() => expect(sent("POST", /\/submit$/)).toHaveLength(1));
    expect((await sent("POST", /\/submit$/)[0].json()).label_id).toBe(CONF.id);
  });
});

describe("document management", () => {
  it("delete asks to confirm, then removes the document and closes the tab", async () => {
    await renderApp(`/d/${DOC_MINE}`);
    await userEvent.click(await screen.findByRole("button", { name: /^Manage / }));
    await userEvent.click(await screen.findByRole("menuitem", { name: "Delete document…" }));
    expect(sent("DELETE", /\/documents\//)).toHaveLength(0);
    await userEvent.click(await screen.findByRole("button", { name: "Delete" }));
    await waitFor(() => expect(sent("DELETE", /\/documents\/[^/]+$/)).toHaveLength(1));
    expect(state.deletedDocs).toContain(DOC_MINE);
    expect(await screen.findByText("Document deleted.")).toBeInTheDocument();
  });
  it("transfer picks a user from the directory", async () => {
    await renderApp(`/d/${DOC_MINE}`);
    await userEvent.click(await screen.findByRole("button", { name: /^Manage / }));
    await userEvent.click(await screen.findByRole("menuitem", { name: "Transfer ownership…" }));
    const dlg = await screen.findByRole("dialog");
    expect(within(dlg).getByRole("button", { name: "Transfer" })).toBeDisabled();
    await userEvent.type(within(dlg).getByLabelText("New owner"), "b");
    expect(within(dlg).queryByRole("list")).not.toBeInTheDocument(); // needs 2 letters
    await userEvent.type(within(dlg).getByLabelText("New owner"), "e");
    await userEvent.click(await within(dlg).findByRole("button", { name: "ben" }));
    await userEvent.click(within(dlg).getByRole("button", { name: "Transfer" }));
    await waitFor(() => expect(sent("POST", /\/transfer$/)).toHaveLength(1));
    expect(await sent("POST", /\/transfer$/)[0].json()).toEqual({ user_id: "22222222-2222-4222-8222-222222222222" });
  });
  it("the sharing dialog starts from the current shares", async () => {
    state.docPatch[DOC_MINE] = { visibility: "shared", share_users: ["11111111-1111-4111-8111-111111111111"], share_groups: ["/Physics"] };
    await renderApp(`/d/${DOC_MINE}`);
    await userEvent.click(await screen.findByRole("button", { name: "Sharing" }));
    expect(await screen.findByRole("button", { name: "Remove ana" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Remove /Physics" })).toBeInTheDocument();
  });
});

describe("collections", () => {
  it("renames and deletes from the sidebar menu", async () => {
    await renderApp("/library");
    await userEvent.click(await screen.findByRole("button", { name: "Actions for collection Thesis reading list" }));
    await userEvent.click(await screen.findByRole("menuitem", { name: "Rename…" }));
    const name = await screen.findByLabelText("Name");
    await userEvent.clear(name);
    await userEvent.type(name, "Reading list");
    await userEvent.click(screen.getByRole("button", { name: "Rename" }));
    await waitFor(() => expect(sent("PATCH", /\/collections\/c1$/)).toHaveLength(1));
    expect(await sent("PATCH", /collections/)[0].json()).toEqual({ name: "Reading list" });
    await userEvent.click(await screen.findByRole("button", { name: "Actions for collection Reading list" }));
    await userEvent.click(await screen.findByRole("menuitem", { name: "Delete…" }));
    await userEvent.click(await screen.findByRole("button", { name: "Delete collection" }));
    await waitFor(() => expect(sent("DELETE", /\/collections\/c1$/)).toHaveLength(1));
  });
});
