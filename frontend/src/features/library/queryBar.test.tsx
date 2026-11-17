import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider } from "@tanstack/react-router";
import { describe, expect, it } from "vitest";
import { createAppRouter } from "@/app/router";

async function renderLibrary(path: string) {
  const router = createAppRouter([path]);
  await router.load();
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
  await screen.findByRole("searchbox", { name: "Search the library" });
  return router;
}

describe("QueryBar", () => {
  it("scope select writes in", async () => {
    const router = await renderLibrary("/library?q=graph");
    await userEvent.selectOptions(screen.getByLabelText("Search in"), "title");
    await waitFor(() => expect(router.state.location.search).toMatchObject({ in: "title", q: "graph" }));
  });

  it("Enter submits q", async () => {
    const router = await renderLibrary("/library");
    await userEvent.type(screen.getByRole("searchbox", { name: "Search the library" }), "attention{Enter}");
    await waitFor(() => expect(router.state.location.search).toMatchObject({ q: "attention" }));
  });

  it("Refine applies words, phrases and exclusions", async () => {
    const router = await renderLibrary("/library");
    await userEvent.click(screen.getByRole("button", { name: /Refine/ }));
    await userEvent.type(screen.getByLabelText("All of these words"), "graph");
    await userEvent.type(screen.getByLabelText("This exact phrase"), "neural network");
    await userEvent.type(screen.getByLabelText("None of these words"), "survey");
    await userEvent.click(screen.getByRole("button", { name: "Apply" }));
    await waitFor(() => expect(router.state.location.search).toMatchObject({ q: 'graph "neural network" -survey' }));
  });

  it("only exclusions disables Apply and explains", async () => {
    await renderLibrary("/library");
    await userEvent.click(screen.getByRole("button", { name: /Refine/ }));
    await userEvent.type(screen.getByLabelText("None of these words"), "survey");
    expect(screen.getByRole("button", { name: "Apply" })).toBeDisabled();
    expect(screen.getByText("Add at least one word to search for")).toBeInTheDocument();
  });

  it("Apply on an untouched Refine leaves q unchanged", async () => {
    const router = await renderLibrary('/library?q=graph+-"related+work"');
    await userEvent.click(screen.getByRole("button", { name: /Refine/ }));
    await userEvent.click(screen.getByRole("button", { name: "Apply" }));
    await waitFor(() => expect(router.state.location.search).toMatchObject({ q: 'graph -"related work"' }));
  });
});
