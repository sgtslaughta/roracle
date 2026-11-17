import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider } from "@tanstack/react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createAppRouter } from "@/app/router";
import { server } from "@/mocks/server";
import { state as mockState } from "@/mocks/state";
import { resetReaderStore } from "@/features/reader/useReaderStore";
import { resetAskTurns } from "./askStore";
import { resetAskContext } from "./useAskContext";

async function renderApp(path = "/ask") {
  const router = createAppRouter([path]);
  await router.load();
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><RouterProvider router={router} /></QueryClientProvider>);
  await screen.findByRole("navigation", { name: "Main" });
  return router;
}
const seen: Request[] = [];
beforeEach(() => {
  resetReaderStore(); resetAskContext(); resetAskTurns(); seen.length = 0; mockState.chats = null; localStorage.clear();
  server.events.on("request:start", ({ request }) => { seen.push(request.clone()); });
});
afterEach(() => { server.events.removeAllListeners(); vi.restoreAllMocks(); });
const box = () => screen.findByRole("textbox", { name: "Question" }) as Promise<HTMLTextAreaElement>;

describe("prompt recall", () => {
  it("cycles Up/Down through sent prompts and restores the draft", async () => {
    localStorage.setItem("roracle.prompts.u-me", JSON.stringify(["first", "second"]));
    const user = userEvent.setup();
    await renderApp();
    const q = await box();
    await user.type(q, "work in progress");
    await user.keyboard("{ArrowUp}");
    expect(q).toHaveValue("second");
    await user.keyboard("{ArrowUp}");
    expect(q).toHaveValue("first");
    await user.keyboard("{ArrowUp}");
    expect(q).toHaveValue("first");
    await user.keyboard("{ArrowDown}");
    expect(q).toHaveValue("second");
    await user.keyboard("{ArrowDown}");
    expect(q).toHaveValue("work in progress");
  });

  it("does not intercept Up with the caret inside multiline text, or with a modifier", async () => {
    localStorage.setItem("roracle.prompts.u-me", JSON.stringify(["old"]));
    const user = userEvent.setup();
    await renderApp();
    const q = await box();
    await user.type(q, "line1{Shift>}{Enter}{/Shift}line2");
    await user.keyboard("{ArrowUp}");
    expect(q).toHaveValue("line1\nline2");
    q.setSelectionRange(0, 0);
    await user.keyboard("{Shift>}{ArrowUp}{/Shift}");
    expect(q).toHaveValue("line1\nline2");
    await user.keyboard("{ArrowUp}");
    expect(q).toHaveValue("old");
  });

  it("while cycling, a recalled multi-line prompt keeps its caret movement until an edge", async () => {
    localStorage.setItem("roracle.prompts.u-me", JSON.stringify(["old", "a\nb"]));
    const user = userEvent.setup();
    await renderApp();
    const q = await box();
    await user.click(q);
    await user.keyboard("{ArrowUp}");
    expect(q).toHaveValue("a\nb");
    q.setSelectionRange(1, 1);
    await user.keyboard("{ArrowUp}");
    expect(q).toHaveValue("a\nb");
    q.setSelectionRange(0, 0);
    await user.keyboard("{ArrowUp}");
    expect(q).toHaveValue("old");
    q.focus();
    await user.keyboard("{ArrowDown}");
    expect(q).toHaveValue("a\nb");
    q.setSelectionRange(1, 1);
    await user.keyboard("{ArrowDown}");
    expect(q).toHaveValue("a\nb");
    q.setSelectionRange(3, 3);
    await user.keyboard("{ArrowDown}");
    expect(q).toHaveValue("");
  });

  it("records a sent prompt", async () => {
    const user = userEvent.setup();
    await renderApp();
    await user.type(await box(), "hello there{Enter}");
    await waitFor(() => expect(JSON.parse(localStorage.getItem("roracle.prompts.u-me") ?? "[]")).toEqual(["hello there"]));
  });
});

describe("remove chat from my list", () => {
  it("confirms, calls DELETE, refreshes the list and leaves the open chat", async () => {
    const user = userEvent.setup();
    const router = await renderApp("/ask/c1");
    const nav = await screen.findByRole("navigation", { name: "Recent chats" });
    await user.click(await within(nav).findByRole("button", { name: /Actions for chat Spectral gap/ }));
    await user.click(await screen.findByRole("menuitem", { name: "Remove from my chats" }));
    expect(await screen.findByText("Remove this chat from your list? Shared links keep working.")).toBeInTheDocument();
    expect(seen.filter((r) => r.method === "DELETE")).toHaveLength(0);
    await user.click(screen.getByRole("button", { name: "Remove" }));
    await waitFor(() => expect(seen.filter((r) => r.method === "DELETE" && new URL(r.url).pathname.endsWith("/chats/c1"))).toHaveLength(1));
    await waitFor(() => expect(router.state.location.pathname).toBe("/ask"));
    await waitFor(() => expect(within(screen.getByRole("navigation", { name: "Recent chats" })).queryByText("Spectral gap in hypergraphs")).toBeNull());
  });
});
