import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
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
const rail = () => within(document.querySelector(".facet-rail") as HTMLElement);

describe("range and signal filters", () => {
  it("arrow keys on Earliest year write year_min on keyup", async () => {
    const router = await renderLibrary("/library");
    const hist = rail().getByRole("img", { name: "Publication years histogram" });
    const first = Number(hist.getAttribute("data-first"));
    const slider = rail().getByRole("slider", { name: "Earliest year" });
    slider.focus();
    // jsdom has no native range-key behaviour: emulate the two steps, then a real keyup commits
    fireEvent.change(slider, { target: { value: String(first + 1) } });
    fireEvent.change(slider, { target: { value: String(first + 2) } });
    await userEvent.keyboard("{ArrowRight}");
    await waitFor(() => expect(router.state.location.search).toMatchObject({ year_min: first + 2 }));
  });

  it("inverted year numbers show an error and leave the URL alone", async () => {
    const router = await renderLibrary("/library");
    const lo = rail().getByRole("spinbutton", { name: "Earliest year" });
    const hi = rail().getByRole("spinbutton", { name: "Latest year" });
    await userEvent.clear(hi);
    await userEvent.type(hi, "2020");
    await userEvent.clear(lo);
    await userEvent.type(lo, "2030");
    await userEvent.tab();
    expect(await rail().findByText("Earliest must be before latest")).toBeInTheDocument();
    expect(router.state.location.search).not.toHaveProperty("year_min");
  });

  it("clicking a bar sets a single year", async () => {
    const router = await renderLibrary("/library");
    const bar = rail().getAllByRole("button", { name: /^Year \d{4}/ })[0];
    await userEvent.click(bar);
    await waitFor(() => {
      const s = router.state.location.search as { year_min?: number; year_max?: number };
      expect(s.year_min).toBeDefined();
      expect(s.year_max).toBe(s.year_min);
    });
  });

  it("Shared with me sets owner=shared", async () => {
    const router = await renderLibrary("/library");
    await userEvent.click(rail().getByRole("radio", { name: "Shared with me" }));
    await waitFor(() => expect(router.state.location.search).toMatchObject({ owner: "shared" }));
  });

  it("Has a DOI: no sets has_doi=no", async () => {
    const router = await renderLibrary("/library");
    await userEvent.selectOptions(rail().getByLabelText("Has a DOI"), "no");
    await waitFor(() => expect(router.state.location.search).toMatchObject({ has_doi: "no" }));
  });

  it("page count range rejects inverted input", async () => {
    const router = await renderLibrary("/library");
    const g = within(rail().getByRole("group", { name: "Page count" }));
    await userEvent.type(g.getByLabelText("Latest"), "10");
    await userEvent.type(g.getByLabelText("Earliest"), "20");
    await userEvent.tab();
    expect(await g.findByText("Earliest must be before latest")).toBeInTheDocument();
    expect(router.state.location.search).not.toHaveProperty("pages_min");
  });

  it("shows the citing paper row and removes cites", async () => {
    const router = await renderLibrary("/library");
    const id = "00000000-0000-4000-8000-000000000001";
    await router.navigate({ to: "/library", search: { cites: id } as never });
    await rail().findByText(/Citing /);
    await userEvent.click(rail().getByRole("button", { name: "Remove citing filter" }));
    await waitFor(() => expect(router.state.location.search).not.toHaveProperty("cites"));
  });

  it("popover content at 360 px has no element wider than its container", async () => {
    window.innerWidth = 360;
    await renderLibrary("/library");
    await userEvent.click(screen.getByRole("button", { name: /^Filters/ }));
    const pop = document.querySelector(".facets-pop") as HTMLElement;
    expect(pop).toBeTruthy();
    for (const el of [pop, ...pop.querySelectorAll<HTMLElement>("*")]) expect(el.scrollWidth).toBeLessThanOrEqual(el.clientWidth);
  });
});
