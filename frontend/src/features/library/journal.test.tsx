import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider } from "@tanstack/react-router";
import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";
import { API_BASE } from "@/api/client";
import { createAppRouter } from "@/app/router";
import { server } from "@/mocks/server";
import { validateLibrarySearch } from "@/lib/searchParams";
import { runSearch } from "@/mocks/search";
import { buildSentence, sentenceText, type Names } from "./querySentence";

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
const group = (name: string) => screen.getByRole("group", { name });
const names: Names = { collection: (i) => i, label: (i) => i, doc: () => "x", journal: (i) => (i === "0028-0836" ? "Nature" : i) };

describe("journal facet and has-identifier filter", () => {
  it("lists the journal with its count and excludes by ISSN", async () => {
    server.use(http.get(`${API_BASE}/search`, ({ request }) => {
      const r = runSearch(new URL(request.url).searchParams);
      return HttpResponse.json({ ...r, facets: { ...r.facets, journal: [{ value: "0028-0836", count: 3, label: "Nature" }] } });
    }));
    const router = await renderLibrary("/library");
    const g = await waitFor(() => { const x = group("Journal"); within(x).getByText("Nature"); return x; });
    expect(within(g).getByText("3")).toBeInTheDocument();
    expect(within(g).getByText("Nature").closest("[title]")).toHaveAttribute("title", "0028-0836");
    await userEvent.click(within(g).getByRole("button", { name: "Exclude Journal: Nature" }));
    await waitFor(() => expect(router.state.location.search).toEqual({ journal_not: ["0028-0836"] }));
  });

  it("writes has_id_not from the PMID select", async () => {
    const router = await renderLibrary("/library");
    const sel = within(screen.getByRole("group", { name: "Has identifier" })).getByLabelText("PMID");
    await userEvent.selectOptions(sel, "lacks");
    await waitFor(() => expect(router.state.location.search).toEqual({ has_id_not: ["pmid"] }));
  });

  it("reads as a sentence", () => {
    expect(sentenceText(buildSentence({ journal: ["0028-0836"], has_id: ["pmid", "arxiv"] }, names))).toBe("Papers in journal “Nature” · with a PMID or arXiv ID");
    expect(sentenceText(buildSentence({ journal_not: ["0028-0836"], has_id_not: ["pmid"] }, names))).toBe("Papers not in journal “Nature” · without a PMID");
    expect(buildSentence({ has_id: ["isbn"] }, names)[0].text).toBe("with an ISBN");
  });

  it("drops invalid journal and has_id values", () => {
    expect(validateLibrarySearch({ journal: ["nope", "0028-0836"], has_id: ["foo", "pmid"], has_id_not: ["bar"] })).toEqual({ journal: ["0028-0836"], has_id: ["pmid"] });
    expect(validateLibrarySearch({ journal: ["nope"], has_id: ["foo"] })).toEqual({});
  });
});
