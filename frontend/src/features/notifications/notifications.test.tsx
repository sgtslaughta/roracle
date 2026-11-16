import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider } from "@tanstack/react-router";
import { afterEach, beforeEach, describe as group, expect, it, vi } from "vitest";
import { createAppRouter } from "@/app/router";
import { DOCS } from "@/mocks/docs";
import { server } from "@/mocks/server";
import { state } from "@/mocks/state";
import { resetReaderStore } from "@/features/reader/useReaderStore";
import { describe, type AppNotification } from "./describe";

vi.mock("@/features/reader/pdfjs", () => ({
  loadPdf: async () => ({
    numPages: 1,
    getPage: async () => ({ getViewport: ({ scale }: { scale: number }) => ({ width: 612 * scale, height: 792 * scale, scale }), render: () => ({ promise: Promise.resolve() }), getTextContent: async () => ({ items: [] }) }),
  }),
}));

async function renderApp(path = "/library") {
  const router = createAppRouter([path]);
  await router.load();
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><RouterProvider router={router} /></QueryClientProvider>);
  await screen.findByRole("navigation", { name: "Main" });
  return router;
}

const seen: Request[] = [];
beforeEach(() => {
  resetReaderStore(); seen.length = 0;
  server.events.on("request:start", ({ request }) => { seen.push(request.clone()); });
});
afterEach(() => { server.events.removeAllListeners(); });
const reads = () => seen.filter((r) => r.method === "POST" && new URL(r.url).pathname.endsWith("/me/notifications/read"));
const lists = () => seen.filter((r) => r.method === "GET" && new URL(r.url).pathname.endsWith("/me/notifications")).map((r) => new URL(r.url).searchParams);
const row = (id: number, kind: string, payload: Record<string, unknown>, read = false): AppNotification =>
  ({ id, kind, payload, created_at: "2026-11-15T09:00:00Z", read_at: read ? "2026-11-15T10:00:00Z" : null });

group("describe", () => {
  it("unknown kinds get generic text and no link", () => {
    expect(describe("chat.shared", { url: "https://evil.example" })).toEqual({ text: "Something changed." });
  });
  it("links only to internal paths from validated ids", () => {
    expect(describe("upload.searchable", { document_id: DOCS[0].id, title: "T" })).toEqual({ text: "“T” is ready to search.", to: `/d/${DOCS[0].id}` });
    expect(describe("upload.searchable", { document_id: "../admin", filename: "x.pdf" }).to).toBe("/uploads");
    expect(describe("ops.connector_run", { name: "Share", status: "partial" })).toEqual({ text: "Connector “Share” finished: partial.", to: "/admin/connectors" });
    expect(describe("upload.failed", null).text).toBe("Your upload couldn't be processed.");
  });
});

group("describe: notes and proposals", () => {
  const D = DOCS[0].id, N = "11111111-2222-4333-8444-555555555555";
  it("proposal.new names the client and links to the inbox", () => {
    expect(describe("proposal.new", { proposal_id: N, kind: "note", client: "claude-code" })).toEqual({ text: "claude-code proposed a note.", to: "/proposals" });
    expect(describe("proposal.new", {}).text).toBe("An agent proposed a note.");
  });
  it("note.reply opens the reply in the document's notes", () => {
    expect(describe("note.reply", { document_id: D, note_id: "x", reply_id: N })).toEqual({ text: "Someone replied to your note.", to: `/d/${D}`, note: { docId: D, noteId: N } });
  });
  it("note.on_document opens the note; bad ids give no link", () => {
    expect(describe("note.on_document", { document_id: D, note_id: N })).toEqual({ text: "Someone added a note to your document.", to: `/d/${D}`, note: { docId: D, noteId: N } });
    expect(describe("note.on_document", { document_id: "../x", note_id: N })).toEqual({ text: "Someone added a note to your document." });
  });
});

group("bell", () => {
  it("shows the unread count, hides admin kinds from non-admins, and marks all read with {all:true}", async () => {
    const user = userEvent.setup();
    await renderApp();
    const bell = await screen.findByRole("button", { name: "Notifications, 2 unread" });
    expect(within(bell).getByText("2")).toBeInTheDocument();
    await user.click(bell);
    const pop = await screen.findByRole("dialog", { name: "Notifications" });
    expect(within(pop).queryByText(/job failed/)).toBeNull();
    await user.click(within(pop).getByRole("button", { name: "Mark all read" }));
    await waitFor(() => expect(reads()).toHaveLength(1));
    expect(await reads()[0].json()).toEqual({ all: true });
    expect(await screen.findByRole("button", { name: "Notifications" })).toBeInTheDocument();
  });

  it("caps the badge at 99+ and asks for the latest 10", async () => {
    state.notifications = Array.from({ length: 120 }, (_, i) => row(i + 1, "upload.searchable", { filename: `f${i}.pdf` }));
    await renderApp();
    const bell = await screen.findByRole("button", { name: "Notifications, 120 unread" });
    expect(within(bell).getByText("99+")).toBeInTheDocument();
    expect(lists()[0].get("limit")).toBe("10");
  });

  it("admins see ops kinds; clicking one marks it read and navigates", async () => {
    state.admin = true;
    state.notifications = [row(1, "upload.searchable", { document_id: DOCS[1].id, title: "Hypergraphs" }), row(2, "ops.job_failed", { kind: "reembed" })];
    const user = userEvent.setup();
    const router = await renderApp();
    await user.click(await screen.findByRole("button", { name: "Notifications, 2 unread" }));
    expect(await screen.findByText("A reembed job failed.")).toBeInTheDocument();
    await user.click(await screen.findByRole("button", { name: /“Hypergraphs” is ready to search/ }));
    await waitFor(() => expect(reads()).toHaveLength(1));
    expect(await reads()[0].json()).toEqual({ ids: [1] });
    await waitFor(() => expect(router.state.location.pathname).toBe(`/d/${DOCS[1].id}`));
  });

  it("never renders payload strings as HTML", async () => {
    state.notifications = [row(1, "upload.failed", { filename: '<img src=x onerror="alert(1)">', error: "<b>bad</b>" }), row(2, "weird.kind", { html: "<i>x</i>" })];
    const user = userEvent.setup();
    await renderApp();
    await user.click(await screen.findByRole("button", { name: /2 unread/ }));
    const pop = await screen.findByRole("dialog", { name: "Notifications" });
    expect(within(pop).getByText(/<img src=x onerror="alert\(1\)">/)).toBeInTheDocument();
    expect(within(pop).getByText("Something changed.")).toBeInTheDocument();
    expect(pop.querySelector("img, b.bad, i")).toBeNull();
  });
});

group("bell: notes and proposals", () => {
  it("clicking a note notification opens the reader on its Notes tab", async () => {
    state.notifications = [row(1, "note.on_document", { document_id: DOCS[0].id, note_id: "11111111-2222-4333-8444-555555555555" })];
    const user = userEvent.setup();
    await renderApp();
    await user.click(await screen.findByRole("button", { name: "Notifications, 1 unread" }));
    await user.click(await screen.findByRole("button", { name: /Someone added a note/ }));
    const reader = await screen.findByRole("complementary", { name: "Reader" });
    expect(await within(reader).findByRole("tab", { name: /Notes/, selected: true })).toBeInTheDocument();
  });

  it("an unread proposal.new refreshes the proposals badge", async () => {
    state.notifications = [row(1, "proposal.new", { client: "claude-code" })];
    await renderApp();
    expect(await screen.findByLabelText("4 waiting")).toBeInTheDocument();
    const pend = () => seen.filter((r) => new URL(r.url).pathname.endsWith("/me/proposals"));
    await waitFor(() => expect(pend().length).toBeGreaterThanOrEqual(2));
  });
});

group("notifications page", () => {
  it("loads more with before_id and filters to unread", async () => {
    state.notifications = Array.from({ length: 45 }, (_, i) => row(i + 1, "upload.searchable", { filename: `f${i + 1}.pdf` }, i % 2 === 0));
    const user = userEvent.setup();
    await renderApp("/notifications");
    expect(await screen.findByText("“f45.pdf” is ready to search.")).toBeInTheDocument();
    expect(screen.queryByText("“f1.pdf” is ready to search.")).toBeNull();
    await user.click(screen.getByRole("button", { name: "Load more" }));
    expect(await screen.findByText("“f1.pdf” is ready to search.")).toBeInTheDocument();
    expect(lists().some((sp) => sp.get("before_id") === "16")).toBe(true);
    await user.click(screen.getByRole("checkbox", { name: "Unread only" }));
    await waitFor(() => expect(lists().some((sp) => sp.get("unread_only") === "true")).toBe(true));
    await waitFor(() => expect(screen.queryByText("“f45.pdf” is ready to search.")).toBeNull());
    expect(screen.getByText("“f44.pdf” is ready to search.")).toBeInTheDocument();
  });
});
