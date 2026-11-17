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
const headers = () => screen.getAllByRole("columnheader").map((h) => h.textContent?.trim());

describe("library views", () => {
  it("table renders only the chosen columns", async () => {
    await renderLibrary("/library?view=table&cols=title,year");
    expect(headers()).toEqual(["Title", "Year"]);
  });

  it("year header sorts desc, then asc, with aria-sort", async () => {
    const router = await renderLibrary("/library?view=table&cols=title,year");
    await userEvent.click(screen.getByRole("button", { name: "Year" }));
    await waitFor(() => expect(router.state.location.search).toMatchObject({ sort: "published_desc" }));
    await userEvent.click(screen.getByRole("button", { name: "Year" }));
    await waitFor(() => expect(router.state.location.search).toMatchObject({ sort: "published_asc" }));
    expect(screen.getByRole("columnheader", { name: "Year" })).toHaveAttribute("aria-sort", "ascending");
    expect(screen.getByRole("columnheader", { name: "Title" })).toHaveAttribute("aria-sort", "none");
  });

  it("column picker unticks venue and keeps Title locked", async () => {
    const router = await renderLibrary("/library?view=table");
    await userEvent.click(screen.getByRole("button", { name: "Columns" }));
    expect(screen.getByRole("checkbox", { name: "Title" })).toBeDisabled();
    await userEvent.click(screen.getByRole("checkbox", { name: "Venue" }));
    await waitFor(() => expect(String((router.state.location.search as { cols?: string[] }).cols)).not.toContain("venue"));
    expect((router.state.location.search as { cols?: string[] }).cols).toContain("title");
  });

  it("compact renders li.result.compact rows", async () => {
    await renderLibrary("/library?view=compact");
    expect(document.querySelectorAll("li.result.compact").length).toBeGreaterThan(0);
  });

  it("view switch writes view and keeps page", async () => {
    const router = await renderLibrary("/library?page=2");
    const group = screen.getByRole("radiogroup", { name: "View" });
    await userEvent.click(within(group).getByRole("radio", { name: "Table" }));
    await waitFor(() => expect(router.state.location.search).toMatchObject({ view: "table", page: 2 }));
  });
});
