import { render, screen, waitFor, within } from "@testing-library/react";
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
  await screen.findByText(/of \d+ documents/);
  return router;
}
// the rail renders once for wide panes and once in the popover only when opened, so the first group is the rail's
const group = (name: string) => screen.getByRole("group", { name });

describe("facet rail", () => {
  it("exclude puts value in tags_not and unchecks include", async () => {
    const router = await renderLibrary("/library?tags=expanders");
    const g = group("Tags");
    expect(within(g).getByRole("checkbox", { name: "Include Tags: expanders" })).toBeChecked();
    await userEvent.click(within(g).getByRole("button", { name: "Exclude Tags: expanders" }));
    await waitFor(() => expect(router.state.location.search).toEqual({ tags_not: ["expanders"] }));
    const ex = await within(group("Tags")).findByRole("button", { name: "Exclude Tags: expanders" });
    expect(ex).toHaveAttribute("aria-pressed", "true");
    expect(within(group("Tags")).getByRole("checkbox", { name: "Include Tags: expanders" })).not.toBeChecked();
  });

  it("Match all sets author_op=and", async () => {
    const router = await renderLibrary("/library?author=M.%20Chen&author=R.%20Aldana");
    const rg = within(group("Author")).getByRole("radiogroup", { name: "Match any / Match all" });
    expect(within(rg).getByRole("radio", { name: "Match any" })).toBeChecked();
    await userEvent.click(within(rg).getByRole("radio", { name: "Match all" }));
    await waitFor(() => expect(router.state.location.search).toMatchObject({ author_op: "and" }));
  });

  it("find box shows server values and selecting adds the filter", async () => {
    const router = await renderLibrary("/library");
    await userEvent.type(within(group("Author")).getByLabelText("Find author"), "zol");
    const box = await within(group("Author")).findByRole("checkbox", { name: "Include Author: Émile Zola" }, { timeout: 2000 });
    await userEvent.click(box);
    await waitFor(() => expect(router.state.location.search).toMatchObject({ author: ["Émile Zola"] }));
  });

  it("uses one sentence-case note and no uppercase text", async () => {
    await renderLibrary("/library");
    expect(screen.getByText("Counts cover everything you can see.")).toBeInTheDocument();
    const rail = document.querySelector(".facet-rail")!;
    for (const el of rail.querySelectorAll("*")) expect(getComputedStyle(el).textTransform).not.toBe("uppercase");
  });
});
