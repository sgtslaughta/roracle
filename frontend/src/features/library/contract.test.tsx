import { render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider } from "@tanstack/react-router";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";
import { API_BASE } from "@/api/client";
import type { SearchHit, SearchResponse } from "@/api/types";
import { createAppRouter } from "@/app/router";
import { server } from "@/mocks/server";
import { safeHighlight } from "./highlight";

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
const EMPTY_FACETS = { tags: [], year: [], file_type: [], org_unit: [], collection: [], author: [], venue: [], label: [] };
const hit = (o: Partial<SearchHit> = {}): SearchHit => ({
  doc_id: "d1", title: "A paper", authors: ["A. Author"], year: 2020, score: 1, passages: [], tags: [], votes: { up: 0, down: 0, mine: 0 },
  file_type: "PDF", version: 1, venue: "Journal", org_unit: "Math", owner_id: "x", flag: null, cited_by_count: 0, page_count: null, added_at: "2026-01-01T00:00:00Z", ...o,
});
const respond = (r: Partial<SearchResponse>) => server.use(http.get(`${API_BASE}/search`, () => HttpResponse.json({ total: 0, total_capped: false, next: null, page: 1, results: [], facets: EMPTY_FACETS, ...r })));

describe("P3 contract: collections by id", () => {
  it("sidebar collection link filters by id", async () => {
    await renderLibrary();
    const link = await screen.findByRole("link", { name: "Thesis reading list" });
    expect(link.getAttribute("href")).toContain("collection=c1");
    expect(link.getAttribute("href")).not.toContain("Thesis");
  });

  it("the collection chip and facet show the name, not the id", async () => {
    const router = await renderLibrary("/library?collection=c1");
    expect(await screen.findByRole("button", { name: "Remove filter Collection: Thesis reading list" })).toBeInTheDocument();
    const g = await screen.findByRole("group", { name: "Collection" });
    expect(await within(g).findByRole("checkbox", { name: "Include Collection: Thesis reading list" })).toBeChecked();
    expect(router.state.location.search).toMatchObject({ collection: ["c1"] });
    await waitFor(() => expect(document.querySelectorAll(".result").length).toBeGreaterThan(0));
  });

  it("facet label wins over the raw value", async () => {
    respond({ facets: { ...EMPTY_FACETS, collection: [{ value: "c9", count: 3, label: "Reading group" }] } });
    await renderLibrary();
    const g = await screen.findByRole("group", { name: "Collection" });
    expect(within(g).getByRole("checkbox", { name: "Include Collection: Reading group" })).toBeInTheDocument();
  });
});

describe("P3 contract: highlight", () => {
  it("keeps <em> as marks and drops every other tag", () => {
    render(<p data-testid="p">{safeHighlight('a <em>gap</em> &amp; <img src=x onerror="alert(1)"> <script>alert(2)</script>b &lt;i&gt;')}</p>);
    const p = screen.getByTestId("p");
    expect(p.querySelectorAll("mark")).toHaveLength(1);
    expect(p.querySelector("mark")).toHaveTextContent("gap");
    expect(p.querySelector("img, script, i")).toBeNull();
    expect(p.textContent).toContain("a gap & ");
    expect(p.textContent).toContain("b <i>");
    expect(p.textContent).not.toContain("alert(2)");
  });

  it("result cards render the server highlight; plain text when it is missing", async () => {
    respond({ total: 1, results: [hit({ passages: [
      { page: 2, chunk_id: "c1", text: "raw one", highlight: 'safe <em>match</em><img src=x onerror=alert(1)>' },
      { page: 3, chunk_id: "c2", text: "raw two" },
    ] })] });
    await renderLibrary("/library?q=match");
    await screen.findByText(/Showing 1–1 of 1/);
    const hits = document.querySelectorAll(".hit");
    expect(hits[0].querySelector("mark")).toHaveTextContent("match");
    expect(hits[0].querySelector("img")).toBeNull();
    expect(hits[1]).toHaveTextContent("raw two");
  });
});

describe("P3 contract: nullable fields and caps", () => {
  it("year null and venue null never print 'null'", async () => {
    respond({ total: 2, results: [hit({ year: null, venue: null }), hit({ doc_id: "d2", year: null, venue: "Journal" })] });
    await renderLibrary();
    await screen.findByText(/Showing 1–2 of 2/);
    expect(document.body.textContent).not.toMatch(/null|undefined/);
    const lines = [...document.querySelectorAll(".result .citeline")].map((e) => e.textContent);
    expect(lines).toEqual(["A. Author.", "A. Author. Journal."]);
  });

  it("total at the cap shows 200+ and the pager stops at ceil(total/size)", async () => {
    respond({ total: 200, total_capped: true, results: [hit()] });
    await renderLibrary();
    expect(await screen.findByText(/of 200\+ documents/)).toBeInTheDocument();
    const nav = await screen.findByRole("navigation", { name: "Pages" });
    expect(within(nav).getByRole("button", { name: "Page 10" })).toBeInTheDocument();
    expect(within(nav).queryByRole("button", { name: "Page 11" })).toBeNull();
  });

  it("one note: browse counts cover everything visible", async () => {
    respond({ total: 5, results: [hit()] });
    await renderLibrary();
    await screen.findByText(/of 5 documents/);
    expect(screen.getAllByText("Counts cover everything you can see.")).not.toHaveLength(0);
    expect(screen.queryByText("Counts cover the 200 newest documents.")).toBeNull();
  });

  it("sort offers 'Most useful' for votes", async () => {
    await renderLibrary();
    expect(await screen.findByRole("option", { name: "Most useful" })).toHaveValue("votes");
  });

  it("flag still renders when present", async () => {
    respond({ total: 1, results: [hit({ flag: "Marked outdated by 11 readers" })] });
    await renderLibrary();
    expect(await screen.findByText("Marked outdated by 11 readers")).toBeInTheDocument();
  });
});

describe("P3 contract: mock tags", () => {
  it("POST tags returns {tag, tags}", async () => {
    const r = await fetch(`${API_BASE}/documents/00000000-0000-4000-8000-000000000001/tags`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ tag: "New Tag" }) });
    const b = await r.json();
    expect(b.tag).toBe("new-tag");
    expect(b.tags).toContain("new-tag");
  });

  it("title PUT 409s when not awaiting; /admin/needs-title is 403 for non-admins", async () => {
    const put = await fetch(`${API_BASE}/documents/u-1/versions/uv-1/title`, { method: "PUT", headers: { "content-type": "application/json" }, body: JSON.stringify({ title: "x" }) });
    expect(put.status).toBe(409);
    const get = await fetch(`${API_BASE}/admin/needs-title`);
    expect(get.status).toBe(403);
    expect(await get.json()).toEqual({ detail: "admin only" });
  });
});

