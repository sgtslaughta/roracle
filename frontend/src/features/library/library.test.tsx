import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider } from "@tanstack/react-router";
import { http, HttpResponse } from "msw";
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { pageItems } from "./Pager";
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
    expect(await screen.findByText(/Showing 1–7 of 7 papers/)).toBeInTheDocument();
    expect(results()).toHaveLength(7);
  });

  it("typing in the global search updates the URL (debounced) and the results", async () => {
    const router = await renderLibrary();
    await screen.findByText(/of 7 papers/);
    await userEvent.type(screen.getByLabelText("Search documents"), "preconditioning");
    await waitFor(() => expect(router.state.location.search).toMatchObject({ q: "preconditioning" }), { timeout: 2000 });
    await waitFor(() => expect(results()).toHaveLength(1));
    expect(screen.getByRole("heading", { level: 1 }).textContent).toBe("Results for “preconditioning”");
    expect(screen.getAllByText("Preconditioning", { exact: false }).length).toBeGreaterThan(0);
    expect(document.querySelector("mark")).not.toBeNull();
  });

  it("clicking a facet value adds a chip and filters; removing it restores counts", async () => {
    const router = await renderLibrary();
    await screen.findByText(/of 7 papers/);
    const tagGroup = screen.getByRole("group", { name: "Tags" });
    await userEvent.click(within(tagGroup).getByRole("checkbox", { name: "Include Tags: expanders" }));
    await waitFor(() => expect(results()).toHaveLength(2));
    expect(router.state.location.search).toMatchObject({ tags: ["expanders"] });
    const chip = screen.getByRole("button", { name: "Remove: tagged expanders" });
    // facets show only values present in the current results
    expect(within(screen.getByRole("group", { name: "File type" })).queryByRole("checkbox", { name: "Include File type: Word" })).toBeNull();
    await userEvent.click(chip);
    await waitFor(() => expect(results()).toHaveLength(7));
    expect(within(screen.getByRole("group", { name: "File type" })).getByRole("checkbox", { name: "Include File type: Word" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^Remove: / })).toBeNull();
  });

  it("a selected facet value with no matches stays visible at zero", async () => {
    await renderLibrary("/library?q=spectral&file_type=zzz");
    const g = await screen.findByRole("group", { name: "File type" });
    await waitFor(() => expect(within(g).getByRole("checkbox", { name: "Include File type: zzz" })).toBeChecked());
    expect(within(g).getByRole("checkbox", { name: "Include File type: zzz" }).closest("label")).toHaveTextContent("0");
  });

  it("clicking a tag chip on a result filters by that tag", async () => {
    const router = await renderLibrary();
    await screen.findByText(/of 7 papers/);
    await userEvent.click(within(results()[0]).getAllByRole("button", { name: /^#/ })[0]);
    await waitFor(() => expect(router.state.location.search.tags).toHaveLength(1));
  });

  it("vote down opens reasons, then records the vote", async () => {
    let body: unknown;
    server.use(http.put(`${API_BASE}/documents/:id/vote`, async ({ request }) => { body = await request.json(); return HttpResponse.json({ up: 1, down: 1, mine: -1 }); }));
    await renderLibrary();
    await screen.findByText(/of 7 papers/);
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
    await screen.findByText(/of 7 papers/);
    await userEvent.click(within(results()[0]).getByRole("button", { name: "Downvote" }));
    await userEvent.click(await screen.findByRole("menuitem", { name: /Superseded/ }));
    await userEvent.click(await screen.findByRole("menuitem", { name: "Confirm" }));
    await waitFor(() => expect(body).toMatchObject({ value: -1, reason: "superseded", superseded_by: expect.any(String) }));
  });

  it("many values of one facet read as one clause", async () => {
    await renderLibrary("/library?tags=a&tags=b&tags=c");
    const sentence = await screen.findByLabelText("Current search");
    expect(within(sentence).getByRole("button", { name: "tagged a, b or c" })).toBeInTheDocument();
  });

  it("facet groups show 8 values then Show all K in a searchable menu", async () => {
    await renderLibrary();
    await screen.findByText(/of 7 papers/);
    const g = screen.getByRole("group", { name: "Tags" });
    expect(within(g).getAllByRole("checkbox")).toHaveLength(8);
    await userEvent.click(within(g).getByRole("button", { name: /Show all 18/ }));
    const menu = await screen.findByLabelText("All tags values", { selector: ".ovl-menu" });
    expect(within(menu).getByRole("searchbox")).toBeInTheDocument();
  });

  it("caps tags on a result card at 4", async () => {
    await renderLibrary();
    await screen.findByText(/of 7 papers/);
    expect(within(results()[0]).getAllByRole("button", { name: /^#/ }).length).toBeLessThanOrEqual(4);
  });

  it("empty state copy", async () => {
    await renderLibrary("/library?q=zzzzqqq");
    expect(await screen.findByText("Nothing you can see matches “zzzzqqq”. Try fewer words, or clear the filters.")).toBeInTheDocument();
  });

  it("paging controls follow total / page size", async () => {
    server.use(http.get(`${API_BASE}/search`, () => HttpResponse.json({ total: 45, page: 2, results: [], facets: { tags: [], year: [], file_type: [], org_unit: [], collection: [], author: [], venue: [] } })));
    await renderLibrary("/library?page=2");
    const nav = await screen.findByRole("navigation", { name: "Pages" });
    expect(within(nav).getByRole("button", { name: "Page 2" })).toHaveAttribute("aria-current", "true");
    expect(within(nav).getByRole("button", { name: "Page 3" })).toBeInTheDocument();
    expect(within(nav).queryByRole("button", { name: "Page 4" })).toBeNull();
  });

  it("sends the page size to the API but keeps it out of the browser URL", async () => {
    let seen: URL | undefined;
    server.use(http.get(`${API_BASE}/search`, ({ request }) => { seen = new URL(request.url); return HttpResponse.json({ total: 0, page: 1, results: [], facets: { tags: [], year: [], file_type: [], org_unit: [], collection: [], author: [], venue: [] } }); }));
    const router = await renderLibrary("/library?q=x");
    await screen.findByText(/No papers match/);
    expect(seen?.searchParams.get("size")).toBe("20");
    expect(router.state.location.search).toEqual({ q: "x" });
  });

  it("pager is capped for thousands of hits: 1 ... p-1 p p+1 ... last", async () => {
    expect(pageItems(50, 100)).toEqual([1, "gap-l", 49, 50, 51, "gap-r", 100]);
    expect(pageItems(1, 100)).toEqual([1, 2, "gap-r", 100]);
    expect(pageItems(100, 100)).toEqual([1, "gap-l", 99, 100]);
    expect(pageItems(3, 5)).toEqual([1, 2, 3, 4, 5]);
    server.use(http.get(`${API_BASE}/search`, () => HttpResponse.json({ total: 2000, page: 50, results: [], facets: { tags: [], year: [], file_type: [], org_unit: [], collection: [], author: [], venue: [] } })));
    await renderLibrary("/library?page=50");
    const nav = await screen.findByRole("navigation", { name: "Pages" });
    expect(within(nav).getAllByRole("button").length).toBeLessThanOrEqual(7 + 2);
    expect(within(nav).getByRole("button", { name: "Page 100" })).toBeInTheDocument();
  });

  it("offers a Filters (n) popover for narrow panes; facets never use a stacked auto-fill grid", async () => {
    await renderLibrary("/library?tags=expanders&file_type=PDF");
    const btn = await screen.findByRole("button", { name: "Filters (2)" });
    await userEvent.click(btn);
    const pop = await screen.findByLabelText("Filters", { selector: ".facets-pop" });
    expect(within(pop).getByRole("group", { name: "Tags" })).toBeInTheDocument();
    const css = readFileSync("src/styles/library.css", "utf8");
    expect(css).not.toMatch(/\.facets\s*\{[^}]*auto-fill/);
    expect(css).toMatch(/@container \(max-width: 560px\)/);
  });

  it("owner sees Remove tag in the chip context menu and it calls DELETE; non-owner does not", async () => {
    const calls: string[] = [];
    server.use(http.delete(`${API_BASE}/documents/:id/tags/:tag`, ({ params }) => { calls.push(String(params.tag)); return HttpResponse.json({ tags: [] }); }));
    await renderLibrary();
    await screen.findByText(/of 7 papers/);
    await screen.findAllByRole("button", { name: /^#/ });
    const owned = results().find((r) => r.getAttribute("data-doc") === "00000000-0000-4000-8000-000000000001")!;
    await waitFor(async () => { await userEvent.pointer({ keys: "[MouseRight]", target: within(owned).getAllByRole("button", { name: /^#/ })[0] }); expect(screen.queryByRole("menuitem", { name: "Remove tag" })).not.toBeNull(); });
    await userEvent.click(screen.getByRole("menuitem", { name: "Remove tag" }));
    await waitFor(() => expect(calls).toHaveLength(1));
    const other = results().find((r) => r.getAttribute("data-doc") === "00000000-0000-4000-8000-000000000002")!;
    await userEvent.pointer({ keys: "[MouseRight]", target: within(other).getAllByRole("button", { name: /^#/ })[0] });
    await screen.findByRole("menuitem", { name: "Copy tag" });
    expect(screen.queryByRole("menuitem", { name: "Remove tag" })).toBeNull();
  });
});

describe("global search typing", () => {
  it("navigates once, after 300 ms and 3 characters, and clears on empty", async () => {
    const qs: (string | null)[] = [];
    server.use(http.get(`${API_BASE}/search`, ({ request }) => { qs.push(new URL(request.url).searchParams.get("q")); return HttpResponse.json({ total: 0, page: 1, results: [], facets: { tags: [], year: [], file_type: [], org_unit: [], collection: [], author: [], venue: [] } }); }));
    await renderLibrary();
    await waitFor(() => expect(qs).toHaveLength(1)); // the initial browse
    const box = screen.getByLabelText("Search documents");
    await userEvent.type(box, "ab");
    await new Promise((r) => setTimeout(r, 450));
    expect(qs).toHaveLength(1); // 2 characters: nothing sent
    await userEvent.type(box, "c");
    await waitFor(() => expect(qs).toHaveLength(2));
    expect(qs[1]).toBe("abc");
    await userEvent.clear(box);
    await waitFor(() => expect(qs).toHaveLength(3)); // empty goes back to browse
    expect(qs[2]).toBeNull(); // no q param
  });
});

const FACETS = { tags: [{ value: "ml", count: 3 }], year: [], file_type: [], org_unit: [], collection: [], author: [], venue: [], journal: [], label: [] };
const hitN = (i: number) => ({ doc_id: `d${i}`, title: `Paper ${i}`, authors: ["A. Author"], year: 2020, score: 1, passages: [], tags: [], votes: { up: 0, down: 0, mine: 0 }, file_type: "PDF", version: 1, venue: null, org_unit: null, owner_id: "x", flag: null, cited_by_count: 0, page_count: null, added_at: "2026-01-01T00:00:00Z" });
const answer = (r: object) => server.use(http.get(`${API_BASE}/search`, () => HttpResponse.json({ total: 1, total_capped: false, next: null, page: 1, results: [hitN(1)], facets: FACETS, ...r })));
const NOTE = "Some filters will be available when the search index upgrade finishes.";

describe("page composition", () => {
  it("formats the count with thousands separators and says papers", async () => {
    answer({ total: 1234, results: Array.from({ length: 20 }, (_, i) => hitN(i)) });
    await renderLibrary();
    expect(await screen.findByText("Showing 1–20 of 1,234 papers")).toBeInTheDocument();
  });

  it("a capped browse reads 10,000+; the pager stops at the browse window with a refine hint", async () => {
    answer({ total: 10000, total_capped: true, page: 500, results: [hitN(1)] });
    await renderLibrary("/library?page=500");
    expect(await screen.findByText(/of 10,000\+ papers/)).toBeInTheDocument();
    const nav = screen.getByRole("navigation", { name: "Pages" });
    expect(within(nav).getByRole("button", { name: "Page 500" })).toHaveAttribute("aria-current", "true");
    expect(within(nav).queryByRole("button", { name: "Page 501" })).toBeNull();
    expect(within(nav).getByRole("button", { name: /Next/ })).toBeDisabled();
    expect(screen.getByText("Refine the search to see more papers.")).toBeInTheDocument();
  });

  it("no refine hint before the last reachable page, nor on an uncapped result", async () => {
    answer({ total: 10000, total_capped: true, page: 1, results: [hitN(1)] });
    await renderLibrary();
    await screen.findByText(/of 10,000\+ papers/);
    expect(screen.queryByText("Refine the search to see more papers.")).toBeNull();
  });

  it("a capped query reads 200+", async () => {
    answer({ total: 200, total_capped: true, results: [hitN(1)] });
    await renderLibrary("/library?q=graphs");
    expect(await screen.findByText(/of 200\+ papers/)).toBeInTheDocument();
  });

  it("upgrading: an inline note, and exclude / new-only controls are disabled", async () => {
    answer({ upgrading: true });
    await renderLibrary();
    expect(await screen.findByText(NOTE)).toBeInTheDocument();
    const g = screen.getByRole("group", { name: "Tags" });
    expect(within(g).getByRole("button", { name: "Exclude Tags: ml" })).toBeDisabled();
    expect(within(g).getByRole("checkbox", { name: "Include Tags: ml" })).toBeEnabled();
    expect(screen.getAllByLabelText("Search in")[0]).toBeDisabled();
  });

  it("a 503 shows the upgrade note instead of the error alert, and no stale results", async () => {
    await renderLibrary();
    await screen.findByText(/of 7 papers/);
    server.use(http.get(`${API_BASE}/search`, () => HttpResponse.json({ detail: "search index upgrade in progress" }, { status: 503 })));
    await userEvent.click(within(screen.getByRole("group", { name: "Tags" })).getAllByRole("button", { name: /^Exclude Tags: / })[0]);
    expect(await screen.findByText(NOTE)).toBeInTheDocument();
    expect(screen.queryByText(/Search failed/)).toBeNull();
    expect(results()).toHaveLength(0);
  });

  it.each(["list", "compact", "table"])("%s view has no horizontal page scroll at 360 px", async (view) => {
    window.innerWidth = 360;
    await renderLibrary(`/library?view=${view}`);
    await screen.findByText(/of \d+ papers/);
    expect(document.documentElement.scrollWidth).toBeLessThanOrEqual(360);
  });
});
