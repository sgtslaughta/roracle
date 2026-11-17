import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider } from "@tanstack/react-router";
import { http, HttpResponse } from "msw";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { toast } from "sonner";
import { API_BASE } from "@/api/client";
import { createAppRouter } from "@/app/router";
import { server } from "@/mocks/server";

vi.mock("sonner", async (orig) => ({ ...(await orig<typeof import("sonner")>()), toast: Object.assign(vi.fn(), { error: vi.fn() }) }));
beforeEach(() => { vi.mocked(toast).mockClear(); });

const BASE = `${API_BASE}/me/saved-searches`;
async function go(path: string) {
  const router = createAppRouter([path]);
  await router.load();
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><RouterProvider router={router} /></QueryClientProvider>);
  await screen.findByRole("searchbox", { name: "Search the library" });
  return router;
}
const openMenu = async () => { await userEvent.click(screen.getByRole("button", { name: "Saved searches" })); return screen.findByRole("dialog", { name: "Saved searches" }); };

describe("saved searches", () => {
  it("saves the query without page or leading ?, name prefilled from the sentence", async () => {
    const posts: unknown[] = [];
    server.use(http.post(BASE, async ({ request }) => { const b = await request.json(); posts.push(b); return HttpResponse.json({ id: "x", ...(b as object), created_at: "", updated_at: "" }, { status: 201 }); }));
    await go("/library?q=graph&year_min=2020&page=3");
    const pop = await openMenu();
    const input = within(pop).getByLabelText("Name");
    expect((input as HTMLInputElement).value).toMatch(/^Papers /);
    await userEvent.clear(input);
    await userEvent.type(input, "Graphs");
    await userEvent.click(within(pop).getByRole("button", { name: "Save this search" }));
    await waitFor(() => expect(posts).toEqual([{ name: "Graphs", query: "q=graph&year_min=2020" }]));
  });

  it("opens a saved search: navigates and the sentence shows its clauses", async () => {
    server.use(http.get(BASE, () => HttpResponse.json([{ id: "s1", name: "Old tags", query: "tags=ml&year_min=2019", created_at: "", updated_at: "" }])));
    const router = await go("/library");
    const pop = await openMenu();
    await userEvent.click(await within(pop).findByRole("button", { name: "Old tags" }));
    await waitFor(() => expect(router.state.location.search).toMatchObject({ tags: ["ml"], year_min: 2019 }));
    expect(await screen.findByText(/ml/, { selector: ".qchip *" })).toBeInTheDocument();
  });

  it("shows the 409 copy for a duplicate name", async () => {
    await go("/library?q=a");
    let pop = await openMenu();
    await userEvent.clear(within(pop).getByLabelText("Name"));
    await userEvent.type(within(pop).getByLabelText("Name"), "Dup");
    await userEvent.click(within(pop).getByRole("button", { name: "Save this search" }));
    await within(pop).findByRole("button", { name: "Dup" });
    pop = screen.getByRole("dialog", { name: "Saved searches" });
    await userEvent.clear(within(pop).getByLabelText("Name"));
    await userEvent.type(within(pop).getByLabelText("Name"), "Dup");
    await userEvent.click(within(pop).getByRole("button", { name: "Save this search" }));
    expect(await within(pop).findByText("You already have a saved search with this name.")).toBeInTheDocument();
  });

  it("shows the server message for the 422 limit", async () => {
    server.use(http.post(BASE, () => HttpResponse.json({ detail: "at most 200 saved searches" }, { status: 422 })));
    await go("/library?q=a");
    const pop = await openMenu();
    await userEvent.click(within(pop).getByRole("button", { name: "Save this search" }));
    expect(await within(pop).findByText("at most 200 saved searches")).toBeInTheDocument();
  });

  it("renames a saved search", async () => {
    server.use(http.get(BASE, () => HttpResponse.json([{ id: "s1", name: "Old", query: "q=a", created_at: "", updated_at: "" }])));
    const patches: unknown[] = [];
    server.use(http.patch(`${BASE}/:id`, async ({ request }) => { patches.push(await request.json()); return HttpResponse.json({ id: "s1", name: "New", query: "q=a", created_at: "", updated_at: "" }); }));
    await go("/library");
    const pop = await openMenu();
    await userEvent.click(await within(pop).findByRole("button", { name: "Actions for Old" }));
    await userEvent.click(await screen.findByRole("menuitem", { name: "Rename" }));
    const box = within(pop).getByLabelText("Rename Old");
    await userEvent.clear(box);
    await userEvent.type(box, "New{Enter}");
    await waitFor(() => expect(patches).toEqual([{ name: "New" }]));
  });

  it("delete then Undo restores it", async () => {
    await go("/library?q=a");
    let pop = await openMenu();
    await userEvent.clear(within(pop).getByLabelText("Name"));
    await userEvent.type(within(pop).getByLabelText("Name"), "Keep");
    await userEvent.click(within(pop).getByRole("button", { name: "Save this search" }));
    await userEvent.click(await within(pop).findByRole("button", { name: "Actions for Keep" }));
    await userEvent.click(await screen.findByRole("menuitem", { name: "Delete" }));
    await waitFor(() => expect(within(pop).queryByRole("button", { name: "Keep" })).toBeNull());
    await waitFor(() => expect(toast).toHaveBeenCalledWith("Saved search deleted.", expect.objectContaining({ action: expect.objectContaining({ label: "Undo" }) })));
    const opts = vi.mocked(toast).mock.calls.find((c) => c[0] === "Saved search deleted.")![1] as unknown as { action: { onClick: () => void } };
    opts.action.onClick();
    pop = screen.getByRole("dialog", { name: "Saved searches" });
    expect(await within(pop).findByRole("button", { name: "Keep" })).toBeInTheDocument();
  });
});
