import { act, render, waitFor } from "@testing-library/react";
import { RouterProvider } from "@tanstack/react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { describe, expect, it, vi } from "vitest";
import { createAppRouter } from "@/app/router";
import { useLibraryNav } from "./useLibraryNav";

const ref: { nav?: ReturnType<typeof useLibraryNav> } = {};
// The library route renders a probe that exposes the hook, so it runs inside the real router.
vi.mock("@/routes/LibraryRoute", () => ({ LibraryRoute: () => { ref.nav = useLibraryNav(); return null; } }));

/** Mount the hook inside the real router so it reads and writes the URL. */
async function setup(path: string) {
  const router = createAppRouter([path]);
  await router.load();
  ref.nav = undefined;
  render(<QueryClientProvider client={new QueryClient()}><RouterProvider router={router} /></QueryClientProvider>);
  await waitFor(() => expect(ref.nav).toBeDefined());
  return { router, ref };
}

describe("useLibraryNav", () => {
  it("exclude after include moves the value to _not and resets the page", async () => {
    const { router, ref } = await setup("/library?tags=ml&page=3");
    await act(() => ref.nav!.toggle("tags", "ml", "exclude"));
    await waitFor(() => expect(router.state.location.search).toEqual({ tags_not: ["ml"] }));
  });
  it("include removes the value from _not; toggling again clears it", async () => {
    const { router, ref } = await setup("/library?tags_not=ml");
    await act(() => ref.nav!.toggle("tags", "ml", "include"));
    await waitFor(() => expect(router.state.location.search).toEqual({ tags: ["ml"] }));
    await act(() => ref.nav!.toggle("tags", "ml", "include"));
    await waitFor(() => expect(router.state.location.search).toEqual({}));
  });
  it("patch resets page, undefined removes; setPage keeps it; clear keeps q/in/sort/view/cols", async () => {
    const { router, ref } = await setup("/library?q=x&in=title&sort=cited&view=table&cols=title,year&year_min=2000&page=2");
    await act(() => ref.nav!.setPage(4));
    await waitFor(() => expect(router.state.location.search.page).toBe(4));
    await act(() => ref.nav!.patch({ year_min: undefined, year_max: 2020 }));
    await waitFor(() => expect(router.state.location.search).toMatchObject({ year_max: 2020 }));
    expect(router.state.location.search.page).toBeUndefined();
    expect(router.state.location.search.year_min).toBeUndefined();
    await act(() => ref.nav!.clear());
    await waitFor(() => expect(router.state.location.search).toEqual({ q: "x", in: "title", sort: "cited", view: "table", cols: ["title", "year"] }));
  });
});
