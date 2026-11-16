import { render, renderHook, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClientProvider, useMutation } from "@tanstack/react-query";
import { RouterProvider } from "@tanstack/react-router";
import { http, HttpResponse } from "msw";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { toast } from "sonner";
import { API_BASE, apiFetch, QUIET } from "@/api/client";
import { createAppRouter } from "@/app/router";
import { createQueryClient } from "@/app/queryClient";
import { server } from "@/mocks/server";
import { state } from "@/mocks/state";

vi.mock("sonner", () => ({ toast: vi.fn(), Toaster: () => null }));
const toasts = () => vi.mocked(toast).mock.calls.map((c) => String(c[0]));
beforeEach(() => vi.mocked(toast).mockClear());

const fail = (path: string, method: "put" | "post", detail = "Not allowed", status = 403) =>
  server.use(http[method](`${API_BASE}${path}`, () => HttpResponse.json({ detail }, { status })));

async function renderApp(path: string) {
  const router = createAppRouter([path]);
  await router.load();
  render(<QueryClientProvider client={createQueryClient()}><RouterProvider router={router} /></QueryClientProvider>);
  await screen.findByRole("navigation", { name: "Main" });
}
const pressed = () => within(screen.getAllByRole("listitem").find((li) => li.classList.contains("result"))!).getByRole("button", { name: "Upvote" }).getAttribute("aria-pressed");
const card = async (i = 0) => (await screen.findAllByRole("listitem")).filter((li) => li.classList.contains("result"))[i];

describe("global mutation errors", () => {
  const wrap = ({ children }: { children: ReactNode }) => <QueryClientProvider client={createQueryClient()}>{children}</QueryClientProvider>;
  it("toasts the server's message", async () => {
    fail("/x", "post", "Disk full", 507);
    const { result } = renderHook(() => useMutation({ mutationFn: () => apiFetch("/x", { method: "POST" }) }), { wrapper: wrap });
    result.current.mutate();
    await waitFor(() => expect(toasts()).toEqual(["Disk full"]));
  });
  it("meta quiet opts out", async () => {
    fail("/x", "post");
    const { result } = renderHook(() => useMutation({ meta: QUIET, mutationFn: () => apiFetch("/x", { method: "POST" }) }), { wrapper: wrap });
    result.current.mutate();
    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(toasts()).toEqual([]);
  });
});

describe("votes", () => {
  it("a failed upvote rolls back and toasts the error", async () => {
    fail("/documents/:id/vote", "put");
    await renderApp("/library");
    await userEvent.click(within(await card()).getByRole("button", { name: "Upvote" }));
    await waitFor(() => expect(toasts()).toContain("Not allowed"));
    await waitFor(() => expect(pressed()).toBe("false"));
  });

  it("an optimistic upvote shows at once", async () => {
    let release = () => {};
    server.use(http.put(`${API_BASE}/documents/:id/vote`, async () => { await new Promise<void>((r) => { release = r; }); return HttpResponse.json({ up: 1, down: 0, mine: 1 }); }));
    await renderApp("/library");
    await userEvent.click(within(await card()).getByRole("button", { name: "Upvote" }));
    await waitFor(() => expect(pressed()).toBe("true"));
    release();
  });

  it("'Thanks' appears only after the server accepts the downvote", async () => {
    fail("/documents/:id/vote", "put");
    await renderApp("/library");
    await userEvent.click(within(await card()).getByRole("button", { name: "Downvote" }));
    await userEvent.click(await screen.findByRole("menuitem", { name: /Outdated/ }));
    await waitFor(() => expect(toasts()).toContain("Not allowed"));
    expect(toasts().some((t) => t.includes("Thanks"))).toBe(false);

    vi.mocked(toast).mockClear();
    server.resetHandlers();
    await userEvent.click(within(await card()).getByRole("button", { name: "Downvote" }));
    await userEvent.click(await screen.findByRole("menuitem", { name: /Outdated/ }));
    await waitFor(() => expect(toasts()).toContain("Marked “Outdated”. Thanks."));
  });
});

describe("notifications", () => {
  it("a failed mark-read rolls the row back to unread and toasts", async () => {
    fail("/me/notifications/read", "post", "Try later", 503);
    await renderApp("/notifications");
    const rows = await screen.findAllByRole("button", { name: /Unread/ });
    const n = rows.length;
    await userEvent.pointer({ keys: "[MouseRight]", target: rows[0] });
    await userEvent.click(await screen.findByRole("menuitem", { name: "Mark as read" }));
    await waitFor(() => expect(toasts()).toContain("Try later"));
    await waitFor(() => expect(screen.getAllByRole("button", { name: /Unread/ })).toHaveLength(n));
  });
});

describe("row menus and collections", () => {
  it("right-click on a result opens its menu; Add to collection picks an existing collection", async () => {
    const puts: string[] = [];
    server.use(http.put(`${API_BASE}/collections/:cid/items/:doc`, ({ params }) => { puts.push(String(params.cid)); return HttpResponse.json({ id: String(params.cid), count: 1 }); }));
    await renderApp("/library");
    const c = await card();
    await userEvent.pointer({ keys: "[MouseRight]", target: within(c).getByRole("heading", { level: 3 }) });
    await userEvent.click(await screen.findByRole("menuitem", { name: "Add to collection" }));
    const dialog = await screen.findByRole("dialog", { name: "Add to collection" });
    await userEvent.click(await within(dialog).findByRole("button", { name: /Thesis reading list/ }));
    await waitFor(() => expect(puts).toEqual(["c1"]));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  });

  it("the kebab button opens the same menu, and a new collection is created then filled", async () => {
    await renderApp("/library");
    const c = await card();
    await userEvent.click(within(c).getByRole("button", { name: /^Actions for / }));
    await userEvent.click(await screen.findByRole("menuitem", { name: "Add to collection" }));
    const dialog = await screen.findByRole("dialog", { name: "Add to collection" });
    await userEvent.type(within(dialog).getByLabelText("New collection name"), "Reading group");
    await userEvent.click(within(dialog).getByRole("button", { name: "Create and add" }));
    await waitFor(() => expect(state.newCollections.map((x) => x.name)).toEqual(["Reading group"]));
    expect(Object.values(state.collItems).flat()).toHaveLength(1);
    await waitFor(() => expect(toasts()).toContain("Added to “Reading group”."));
  });

  it("a failed add keeps the dialog open and toasts the reason", async () => {
    fail("/collections/:cid/items/:doc", "put", "not found", 404);
    await renderApp("/library");
    await userEvent.click(within(await card()).getByRole("button", { name: /^Actions for / }));
    await userEvent.click(await screen.findByRole("menuitem", { name: "Add to collection" }));
    await userEvent.click(await within(await screen.findByRole("dialog")).findByRole("button", { name: /Thesis reading list/ }));
    await waitFor(() => expect(toasts()).toContain("not found"));
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });
});
