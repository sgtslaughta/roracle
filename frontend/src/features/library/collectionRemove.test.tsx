import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider } from "@tanstack/react-router";
import { describe, expect, it } from "vitest";
import { createAppRouter } from "@/app/router";

async function renderLibrary(path: string) {
  const router = createAppRouter([path]);
  await router.load();
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><RouterProvider router={router} /></QueryClientProvider>);
}
const cards = () => screen.getAllByRole("listitem").filter((li) => li.classList.contains("result"));

describe("remove from collection", () => {
  it("is offered only when filtered to one collection, and the document leaves the list", async () => {
    await renderLibrary("/library?collection=c1");
    await screen.findByText(/of \d+ papers/);
    const before = cards().length;
    await userEvent.click(within(cards()[0]).getByRole("button", { name: /^Actions for/ }));
    await userEvent.click(await screen.findByRole("menuitem", { name: "Remove from collection" }));
    await waitFor(() => expect(cards().length).toBe(before - 1));
  });

  it("is absent without a collection filter", async () => {
    await renderLibrary("/library");
    await screen.findByText(/of 7 papers/);
    await userEvent.click(within(cards()[0]).getByRole("button", { name: /^Actions for/ }));
    expect(await screen.findByRole("menuitem", { name: "Open" })).toBeInTheDocument();
    expect(screen.queryByRole("menuitem", { name: "Remove from collection" })).toBeNull();
  });
});
