import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider } from "@tanstack/react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createAppRouter } from "@/app/router";
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
    await user.click(within(dlg).getByRole("button", { name: "Send" }));
    await waitFor(() => expect(sent("PUT", /rating$/)).toHaveLength(3));
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
