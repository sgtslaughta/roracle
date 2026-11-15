import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider } from "@tanstack/react-router";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";
import { API_BASE } from "@/api/client";
import { createAppRouter } from "@/app/router";
import { server } from "@/mocks/server";

async function renderLibrary(path = "/library") {
  const router = createAppRouter([path]);
  await router.load();
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
  return router;
}
const results = () => screen.getAllByRole("listitem").filter((li) => li.classList.contains("result"));

describe("library search", () => {
  it("lists the library and counts documents", async () => {
    await renderLibrary();
    expect(await screen.findByText(/Showing 1–7 of 7 documents/)).toBeInTheDocument();
    expect(results()).toHaveLength(7);
  });

  it("typing in the global search updates the URL (debounced) and the results", async () => {
    const router = await renderLibrary();
    await screen.findByText(/of 7 documents/);
    await userEvent.type(screen.getByLabelText("Search documents"), "preconditioning");
    await waitFor(() => expect(router.state.location.search).toMatchObject({ q: "preconditioning" }), { timeout: 2000 });
    await waitFor(() => expect(results()).toHaveLength(1));
    expect(screen.getByRole("heading", { level: 1 }).textContent).toBe("Results for “preconditioning”");
    expect(screen.getAllByText("Preconditioning", { exact: false }).length).toBeGreaterThan(0);
    expect(document.querySelector("mark")).not.toBeNull();
  });

  it("clicking a facet value adds a chip and filters; removing it restores counts", async () => {
    const router = await renderLibrary();
    await screen.findByText(/of 7 documents/);
    const tagGroup = screen.getByRole("group", { name: "Tags" });
    await userEvent.click(within(tagGroup).getByRole("checkbox", { name: "Tags: expanders" }));
    await waitFor(() => expect(results()).toHaveLength(2));
    expect(router.state.location.search).toMatchObject({ tags: ["expanders"] });
    const chip = screen.getByRole("button", { name: "Remove filter Tags: expanders" });
    // facets show only values present in the current results
    expect(within(screen.getByRole("group", { name: "File type" })).queryByRole("checkbox", { name: "File type: Word" })).toBeNull();
    await userEvent.click(chip);
    await waitFor(() => expect(results()).toHaveLength(7));
    expect(within(screen.getByRole("group", { name: "File type" })).getByRole("checkbox", { name: "File type: Word" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Remove filter/ })).toBeNull();
  });

  it("a selected facet value with no matches stays visible at zero", async () => {
    await renderLibrary("/library?q=spectral&year=1999");
    const g = await screen.findByRole("group", { name: "Year" });
    await waitFor(() => expect(within(g).getByRole("checkbox", { name: "Year: 1999" })).toBeChecked());
    expect(within(g).getByRole("checkbox", { name: "Year: 1999" }).closest("label")).toHaveTextContent("0");
  });

  it("clicking a tag chip on a result filters by that tag", async () => {
    const router = await renderLibrary();
    await screen.findByText(/of 7 documents/);
    await userEvent.click(within(results()[0]).getAllByRole("button", { name: /^#/ })[0]);
    await waitFor(() => expect(router.state.location.search.tags).toHaveLength(1));
  });

  it("vote down opens reasons, then records the vote", async () => {
    let body: unknown;
    server.use(http.put(`${API_BASE}/documents/:id/vote`, async ({ request }) => { body = await request.json(); return HttpResponse.json({ up: 1, down: 1, mine: -1 }); }));
    await renderLibrary();
    await screen.findByText(/of 7 documents/);
    await userEvent.click(within(results()[0]).getByRole("button", { name: "Downvote" }));
    const menu = await screen.findByRole("menu");
    expect(within(menu).getByText("Outdated")).toBeInTheDocument();
    expect(within(menu).getByText("Wrong or misleading")).toBeInTheDocument();
    expect(within(menu).getByText("Low quality")).toBeInTheDocument();
    await userEvent.click(within(menu).getByRole("menuitem", { name: "Outdated" }));
    await waitFor(() => expect(body).toEqual({ value: -1, reason: "outdated" }));
  });

  it("superseded asks which document replaces it", async () => {
    let body: unknown;
    server.use(http.put(`${API_BASE}/documents/:id/vote`, async ({ request }) => { body = await request.json(); return HttpResponse.json({ up: 1, down: 1, mine: -1 }); }));
    await renderLibrary();
    await screen.findByText(/of 7 documents/);
    await userEvent.click(within(results()[0]).getByRole("button", { name: "Downvote" }));
    await userEvent.click(await screen.findByRole("menuitem", { name: /Superseded/ }));
    await userEvent.click(await screen.findByRole("menuitem", { name: "Confirm" }));
    await waitFor(() => expect(body).toMatchObject({ value: -1, reason: "superseded", superseded_by: expect.any(String) }));
  });

  it("caps active filter chips at 5 with a +K menu offering Clear all", async () => {
    await renderLibrary("/library?tags=a&tags=b&tags=c&tags=d&tags=e&tags=f&tags=g");
    const bar = await screen.findByLabelText("Active filters");
    expect(within(bar).getAllByRole("button", { name: /Remove filter/ })).toHaveLength(5);
    await userEvent.click(within(bar).getByRole("button", { name: "More filters: 2 more" }));
    expect(await screen.findByRole("button", { name: "Clear all" })).toBeInTheDocument();
  });

  it("facet groups show 8 values then Show all K in a searchable menu", async () => {
    await renderLibrary();
    await screen.findByText(/of 7 documents/);
    const g = screen.getByRole("group", { name: "Tags" });
    expect(within(g).getAllByRole("checkbox")).toHaveLength(8);
    await userEvent.click(within(g).getByRole("button", { name: /Show all 18/ }));
    const menu = await screen.findByLabelText("All tags values", { selector: ".ovl-menu" });
    expect(within(menu).getByRole("searchbox")).toBeInTheDocument();
  });

  it("caps tags on a result card at 4", async () => {
    await renderLibrary();
    await screen.findByText(/of 7 documents/);
    expect(within(results()[0]).getAllByRole("button", { name: /^#/ }).length).toBeLessThanOrEqual(4);
  });

  it("empty state copy", async () => {
    await renderLibrary("/library?q=zzzzqqq");
    expect(await screen.findByText("Nothing you can see matches “zzzzqqq”. Try fewer words, or clear the filters.")).toBeInTheDocument();
  });

  it("paging controls follow total / page size", async () => {
    server.use(http.get(`${API_BASE}/search`, () => HttpResponse.json({ total: 45, page: 2, results: [], facets: { tags: [], year: [], file_type: [], org_unit: [], collection: [], author: [] } })));
    await renderLibrary("/library?page=2");
    const nav = await screen.findByRole("navigation", { name: "Pages" });
    expect(within(nav).getByRole("button", { name: "2" })).toHaveAttribute("aria-current", "true");
    expect(within(nav).getByRole("button", { name: "3" })).toBeInTheDocument();
    expect(within(nav).queryByRole("button", { name: "4" })).toBeNull();
  });
});
