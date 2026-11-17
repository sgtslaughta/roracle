import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider } from "@tanstack/react-router";
import { afterEach, describe, expect, it, vi } from "vitest";
import { createAppRouter } from "@/app/router";

async function renderLibrary(path: string) {
  const router = createAppRouter([path]);
  await router.load();
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
  await screen.findByText(/of \d+ papers|No papers match/);
  return router;
}
const sentence = () => within(screen.getByLabelText("Current search"));

describe("query sentence UI", () => {
  it("reads the search as a sentence", async () => {
    await renderLibrary("/library?tags=ml&year_min=2018&year_max=2024");
    expect(screen.getByLabelText("Current search").textContent).toMatch(/^Papers.*published 2018–2024.*tagged ml/);
  });

  it("clicking Remove: published 2018–2024 removes year_min and year_max", async () => {
    const router = await renderLibrary("/library?year_min=2018&year_max=2024&tags=ml");
    await userEvent.click(sentence().getByRole("button", { name: "Remove: published 2018–2024" }));
    await waitFor(() => expect(router.state.location.search).toEqual({ tags: ["ml"] }));
  });

  it("Tab to a chip and Delete removes it; focus lands on the next chip", async () => {
    const router = await renderLibrary("/library?year_min=2018&year_max=2024&tags=ml");
    screen.getByRole("button", { name: "Refine" }).focus();
    await userEvent.tab();
    expect(sentence().getByRole("button", { name: "published 2018–2024" })).toHaveFocus();
    await userEvent.keyboard("{Delete}");
    await waitFor(() => expect(router.state.location.search).toEqual({ tags: ["ml"] }));
    await waitFor(() => expect(sentence().getByRole("button", { name: "tagged ml" })).toHaveFocus());
  });

  it("Backspace on the last chip moves focus to the search input", async () => {
    await renderLibrary("/library?tags=ml");
    sentence().getByRole("button", { name: "tagged ml" }).focus();
    await userEvent.keyboard("{Backspace}");
    await waitFor(() => expect(screen.getByLabelText("Search the library")).toHaveFocus());
    expect(screen.getByLabelText("Current search").textContent).toBe("All papers you can see");
  });

  it("Enter on a chip opens its editor with the first control focused", async () => {
    const router = await renderLibrary("/library?tags=expanders");
    const chip = sentence().getByRole("button", { name: "tagged expanders" });
    expect(chip).toHaveAttribute("aria-haspopup", "dialog");
    chip.focus();
    await userEvent.keyboard("{Enter}");
    const dialog = await screen.findByRole("dialog");
    await waitFor(() => expect(dialog.contains(document.activeElement)).toBe(true));
    await userEvent.click(within(dialog).getByRole("checkbox", { name: "Include Tags: expanders" }));
    await waitFor(() => expect(router.state.location.search).toEqual({}));
  });

  it("Escape closes the editor and returns focus to the chip", async () => {
    await renderLibrary("/library?year_min=2018");
    const y = sentence().getByRole("button", { name: "published 2018 or later" });
    y.focus();
    await userEvent.keyboard("{Enter}");
    await screen.findByRole("dialog");
    await userEvent.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(y).toHaveFocus();
  });

  it("Clear all appears with two or more clauses and clears them", async () => {
    const router = await renderLibrary("/library?tags=ml&sort=title&view=compact");
    await userEvent.click(sentence().getByRole("button", { name: "Clear all" }));
    await waitFor(() => expect(router.state.location.search).toEqual({ view: "compact" }));
  });

  it("no Clear all with one clause", async () => {
    await renderLibrary("/library?tags=ml");
    expect(screen.queryByRole("button", { name: "Clear all" })).toBeNull();
  });

  describe("with motion allowed", () => {
    const motion = () => vi.spyOn(window, "matchMedia").mockImplementation((q: string) => ({ matches: q.includes("no-preference"), media: q, onchange: null, addListener: () => {}, removeListener: () => {}, addEventListener: () => {}, removeEventListener: () => {}, dispatchEvent: () => false }) as MediaQueryList);
    afterEach(() => { vi.useRealTimers(); vi.restoreAllMocks(); });

    it("two removals inside the leave animation both apply", async () => {
      const router = await renderLibrary("/library?tags=ml&year_min=2018&venue=Nature");
      motion();
      vi.useFakeTimers();
      fireEvent.click(sentence().getByRole("button", { name: "Remove: tagged ml" }));
      fireEvent.click(sentence().getByRole("button", { name: "Remove: published 2018 or later" }));
      expect(document.querySelectorAll(".qchip[data-leaving]")).toHaveLength(2); // the first chip keeps leaving
      await act(async () => { vi.advanceTimersByTime(400); });
      vi.useRealTimers();
      await waitFor(() => expect(router.state.location.search).toEqual({ venue: ["Nature"] }));
    });

    it("a removal pending at unmount never navigates back to the library", async () => {
      const router = await renderLibrary("/library?tags=ml");
      motion();
      vi.useFakeTimers();
      fireEvent.click(sentence().getByRole("button", { name: "Remove: tagged ml" }));
      await act(async () => { await router.navigate({ to: "/uploads" }); });
      await act(async () => { vi.advanceTimersByTime(400); });
      vi.useRealTimers();
      await new Promise((r) => setTimeout(r, 50));
      expect(router.state.location.pathname).toBe("/uploads");
    });
  });
});
