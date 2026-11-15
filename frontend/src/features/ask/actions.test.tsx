import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider } from "@tanstack/react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createAppRouter } from "@/app/router";
import { http, HttpResponse } from "msw";
import { API_BASE } from "@/api/client";
import { server } from "@/mocks/server";
import { resetReaderStore } from "@/features/reader/useReaderStore";
import { resetAskTurns } from "./askStore";
import { resetAskContext } from "./useAskContext";
import { answerMarkdown } from "./answerMarkdown";

const D1 = "00000000-0000-4000-8000-000000000001";

async function renderApp(path: string) {
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
afterEach(() => { server.events.removeAllListeners(); vi.restoreAllMocks(); });
const sent = (method: string, re: RegExp) => seen.filter((r) => r.method === method && re.test(new URL(r.url).pathname));

describe("answerMarkdown", () => {
  const cite = (n: number, doc_id: string, page: number) => ({ n, doc_id, version_id: "v", chunk_id: "c", title: `T${n}`, page, bbox: null, quote: "q" });
  it("formats references, unavailable sources and missing authors", () => {
    const md = answerMarkdown("Why?", "Because [1].", [{ n: 2, available: false }, cite(1, "a", 3), cite(3, "b", 1)], { a: { authors: ["Ito", "Chen"], year: 2023 } });
    expect(answerMarkdown("  Why\n  so  much? ", "x", [], {})).toMatch(/^## Why so much\?\n/);
    expect(md).toBe("## Why?\n\nBecause [1].\n\n[1] Ito, Chen, T1, 2023, p. 3\n[2] Source not available\n[3] T3, p. 1");
  });
});

describe("answer actions", () => {
  it("toggles the passages drawer and pins / excludes through PATCH", async () => {
    const user = userEvent.setup();
    await renderApp("/ask/c1");
    const toggle = await screen.findByRole("button", { name: /Show the 8 passages read/ });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    await user.click(toggle);
    const list = await screen.findByRole("list", { name: "Passages read" });
    expect(within(list).getAllByRole("listitem")).toHaveLength(8);
    await user.click(screen.getByRole("button", { name: /Hide the 8 passages read/ }));
    expect(screen.queryByRole("list", { name: "Passages read" })).toBeNull();
    await user.click(screen.getByRole("button", { name: /Show the 8 passages read/ }));
    const first = within(await screen.findByRole("list", { name: "Passages read" })).getAllByRole("listitem")[0];
    await user.click(within(first).getByRole("button", { name: "Pin" }));
    await waitFor(() => expect(sent("PATCH", /\/chats\/c1$/)).toHaveLength(1));
    expect(await sent("PATCH", /\/chats\/c1$/)[0].json()).toEqual({ pins: [D1], excludes: [] });
    await waitFor(() => expect(within(first).getByRole("button", { name: "Pin" })).toHaveAttribute("aria-pressed", "true"));
    expect(first).toHaveTextContent("Pinned");
    await user.click(within(first).getByRole("button", { name: "Exclude" }));
    await waitFor(() => expect(sent("PATCH", /\/chats\/c1$/)).toHaveLength(2));
    expect(await sent("PATCH", /\/chats\/c1$/)[1].json()).toEqual({ excludes: [D1], pins: [] });
    expect(first).toHaveTextContent("Left out of next answers");
    await waitFor(() => expect(document.querySelectorAll(".note.excluded").length).toBeGreaterThan(0));
  });

  it("rates up, and down with a comment", async () => {
    const user = userEvent.setup();
    await renderApp("/ask/c1");
    const up = await screen.findByRole("button", { name: "Helpful" });
    await user.click(up);
    await waitFor(() => expect(up).toHaveAttribute("aria-pressed", "true"));
    expect(await sent("PUT", /\/messages\/c1-m2\/rating$/)[0].json()).toMatchObject({ value: 1 });
    await user.click(screen.getByRole("button", { name: "Not helpful" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "Not helpful" })).toHaveAttribute("aria-pressed", "true"));
    const dlg = await screen.findByRole("dialog");
    await user.type(within(dlg).getByLabelText(/What went wrong/), "Wrong page");
    const thanks = () => screen.queryAllByText(/^Thanks\. Ratings help/).length;
    const before = thanks();
    await user.click(within(dlg).getByRole("button", { name: "Send" }));
    await waitFor(() => expect(sent("PUT", /rating$/)).toHaveLength(3));
    expect(thanks()).toBe(before); // the comment PUT does not thank again
    expect(await sent("PUT", /rating$/)[2].json()).toEqual({ value: -1, comment: "Wrong page" });
  });

  it("copies the answer as Markdown", async () => {
    const user = userEvent.setup();
    await renderApp("/ask/c1");
    await user.click(await screen.findByRole("button", { name: "Copy as Markdown" }));
    const text = await waitFor(async () => { const t = await navigator.clipboard.readText(); expect(t).not.toBe(""); return t; });
    expect(text).toMatch(/^## Does the spectral-gap/);
    expect(text).toMatch(/\[1\] .+, p\. 1\n/);
    expect(text).toMatch(/\[6\] Source not available$/);
  });

  it("shares with group and user chips, validating group paths", async () => {
    const user = userEvent.setup();
    await renderApp("/ask/c1");
    await user.click(await screen.findByRole("button", { name: "Share" }));
    const dlg = await screen.findByRole("dialog", { name: "Share this chat" });
    await user.type(within(dlg).getByLabelText("Groups"), "Mathematics{Enter}");
    expect(await within(dlg).findByRole("alert")).toHaveTextContent(/start with/);
    await user.clear(within(dlg).getByLabelText("Groups"));
    await user.type(within(dlg).getByLabelText("Groups"), "/Mathematics/Spectral Theory{Enter}");
    await user.type(within(dlg).getByLabelText("People (user IDs)"), "u-42{Enter}");
    await user.click(within(dlg).getByRole("button", { name: "Save" }));
    await waitFor(() => expect(sent("PUT", /\/chats\/c1\/shares$/)).toHaveLength(1));
    expect(await sent("PUT", /shares$/)[0].json()).toEqual({ users: ["u-42"], groups: ["/Mathematics/Spectral Theory"] });
  });

  it("exports the chat and renames it", async () => {
    const user = userEvent.setup();
    URL.createObjectURL = vi.fn(() => "blob:x"); URL.revokeObjectURL = vi.fn();
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
    await renderApp("/ask/c1");
    await user.click(await screen.findByRole("button", { name: "Export" }));
    await waitFor(() => expect(sent("GET", /\/chats\/c1\/export\.md$/)).toHaveLength(1));
    await user.click(screen.getByRole("button", { name: "Rename" }));
    const input = await screen.findByRole("textbox", { name: "Chat title" });
    await user.clear(input);
    await user.type(input, "Renamed{Enter}");
    await waitFor(() => expect(sent("PATCH", /\/chats\/c1$/)).toHaveLength(1));
    expect(await sent("PATCH", /\/chats\/c1$/)[0].json()).toEqual({ title: "Renamed" });
    expect(await screen.findByRole("heading", { level: 1, name: "Renamed" })).toBeInTheDocument();
  });

  it("rename has a Save button and Escape cancels", async () => {
    const user = userEvent.setup();
    await renderApp("/ask/c1");
    await user.click(await screen.findByRole("button", { name: "Rename" }));
    let input = await screen.findByRole("textbox", { name: "Chat title" });
    await user.clear(input);
    await user.type(input, "Dropped{Escape}");
    await waitFor(() => expect(screen.queryByRole("textbox", { name: "Chat title" })).toBeNull());
    expect(sent("PATCH", /\/chats\/c1$/)).toHaveLength(0);
    await user.click(screen.getByRole("button", { name: "Rename" }));
    input = await screen.findByRole("textbox", { name: "Chat title" });
    expect(input).toHaveValue("Spectral gap in hypergraphs");
    await user.clear(input);
    await user.type(input, "Via button");
    await user.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(sent("PATCH", /\/chats\/c1$/)).toHaveLength(1));
    expect(await sent("PATCH", /\/chats\/c1$/)[0].json()).toEqual({ title: "Via button" });
  });

  it("Quote on a passage sets the quote chip and the next question carries it", async () => {
    const user = userEvent.setup();
    await renderApp("/ask/c1");
    await user.click(await screen.findByRole("button", { name: /Show the 8 passages read/ }));
    const first = within(await screen.findByRole("list", { name: "Passages read" })).getAllByRole("listitem")[0];
    await user.click(within(first).getByRole("button", { name: "Quote" }));
    expect(await screen.findByRole("button", { name: "Remove quote" })).toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole("textbox", { name: "Question" })).toHaveFocus());
    await user.type(screen.getByRole("textbox", { name: "Question" }), "What does this mean?{Enter}");
    await waitFor(() => expect(sent("POST", /\/chats\/c1\/messages$/)).toHaveLength(1));
    const sel = (await sent("POST", /\/messages$/)[0].json()).viewer.selection;
    expect(sel).toMatchObject({ doc_id: D1 });
    expect(sel.text.length).toBeGreaterThan(0);
  });

  it("copies a share link built by the router", async () => {
    const user = userEvent.setup();
    await renderApp("/ask/c1");
    await user.click(await screen.findByRole("button", { name: "Share" }));
    await user.click(await screen.findByRole("button", { name: "Copy link" }));
    await waitFor(async () => expect(await navigator.clipboard.readText()).toBe(`${location.origin}/ask/c1`));
  });

  it("commits pending chip text on Save, and blocks invalid text", async () => {
    const user = userEvent.setup();
    await renderApp("/ask/c1");
    await user.click(await screen.findByRole("button", { name: "Share" }));
    const dlg = await screen.findByRole("dialog", { name: "Share this chat" });
    await user.type(within(dlg).getByLabelText("Groups"), "nope");
    await user.click(within(dlg).getByRole("button", { name: "Save" }));
    expect(await within(dlg).findByRole("alert")).toHaveTextContent(/start with/);
    expect(sent("PUT", /shares$/)).toHaveLength(0);
    await user.clear(within(dlg).getByLabelText("Groups"));
    await user.type(within(dlg).getByLabelText("Groups"), "/Math");
    await user.type(within(dlg).getByLabelText("People (user IDs)"), "u-7");
    await user.click(within(dlg).getByRole("button", { name: "Save" }));
    await waitFor(() => expect(sent("PUT", /shares$/)).toHaveLength(1));
    expect(await sent("PUT", /shares$/)[0].json()).toEqual({ users: ["u-7"], groups: ["/Math"] });
  });

  it("starts the share dialog empty each time it opens", async () => {
    const user = userEvent.setup();
    await renderApp("/ask/c1");
    await user.click(await screen.findByRole("button", { name: "Share" }));
    let dlg = await screen.findByRole("dialog", { name: "Share this chat" });
    await user.type(within(dlg).getByLabelText("Groups"), "/Math{Enter}");
    expect(within(dlg).getByText("/Math")).toBeInTheDocument();
    await user.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    await user.click(screen.getByRole("button", { name: "Share" }));
    dlg = await screen.findByRole("dialog", { name: "Share this chat" });
    expect(within(dlg).queryByText("/Math")).toBeNull();
  });

  it("rolls back a failed pin and shows a toast", async () => {
    const user = userEvent.setup();
    server.use(http.patch(`${API_BASE}/chats/:id`, () => HttpResponse.json({ detail: "boom" }, { status: 500 })));
    await renderApp("/ask/c1");
    await user.click(await screen.findByRole("button", { name: /Show the 8 passages read/ }));
    const first = within(await screen.findByRole("list", { name: "Passages read" })).getAllByRole("listitem")[0];
    const pin = within(first).getByRole("button", { name: "Pin" });
    await user.click(pin);
    await waitFor(() => expect(sent("PATCH", /\/chats\/c1$/)).toHaveLength(1));
    await waitFor(() => expect(pin).toHaveAttribute("aria-pressed", "false"));
    expect(first).not.toHaveTextContent("Pinned");
    expect(await screen.findByText("The change couldn't be saved. Try again.")).toBeInTheDocument();
  });

  it("does not re-send a Not helpful rating, only opens the comment box", async () => {
    const user = userEvent.setup();
    await renderApp("/ask/c1");
    await user.click(await screen.findByRole("button", { name: "Not helpful" }));
    await waitFor(() => expect(sent("PUT", /rating$/)).toHaveLength(1));
    await user.keyboard("{Escape}");
    await waitFor(() => expect(screen.getByRole("button", { name: "Not helpful" })).toHaveAttribute("aria-pressed", "true"));
    await user.click(screen.getByRole("button", { name: "Not helpful" }));
    expect(await screen.findByRole("textbox", { name: /What went wrong/ })).toBeInTheDocument();
    expect(sent("PUT", /rating$/)).toHaveLength(1);
  });

  it("caps a long passages drawer at 20 until Show all", async () => {
    const user = userEvent.setup();
    const base = await (await fetch(`${API_BASE}/chats/c1`)).json();
    const msgs = base.messages.map((m: { role: string; retrieved: { chunk_id: string }[] }) =>
      m.role === "assistant" ? { ...m, retrieved: Array.from({ length: 25 }, (_, i) => ({ ...(m.retrieved[0] ?? {}), n: undefined, chunk_id: `x${i}`, score: 1 - i / 100, text: `p${i}` })) } : m);
    server.use(http.get(`${API_BASE}/chats/c1`, () => HttpResponse.json({ ...base, messages: msgs })));
    await renderApp("/ask/c1");
    await user.click(await screen.findByRole("button", { name: /Show the 25 passages read/ }));
    const list = await screen.findByRole("list", { name: "Passages read" });
    expect(within(list).getAllByRole("listitem")).toHaveLength(20);
    await user.click(screen.getByRole("button", { name: "Show all 25" }));
    expect(within(list).getAllByRole("listitem")).toHaveLength(25);
  });

  it("toasts when export fails", async () => {
    const user = userEvent.setup();
    server.use(http.get(`${API_BASE}/chats/c1/export.md`, () => HttpResponse.json({ detail: "x" }, { status: 500 })));
    await renderApp("/ask/c1");
    await user.click(await screen.findByRole("button", { name: "Export" }));
    expect(await screen.findByText("The chat couldn't be exported. Try again.")).toBeInTheDocument();
  });

  it("shows a shared chat read-only", async () => {
    await renderApp("/ask/shared-1");
    expect(await screen.findByText("Shared with you. Read-only.")).toBeInTheDocument();
    expect(screen.queryByRole("textbox", { name: "Question" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Share" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Rename" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Helpful" })).toBeNull();
    expect(screen.getByRole("button", { name: "Export" })).toBeInTheDocument();
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: /Show the 8 passages read/ }));
    expect(screen.queryByRole("button", { name: "Pin" })).toBeNull();
  });
});
