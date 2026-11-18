import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider } from "@tanstack/react-router";
import { createAppRouter } from "@/app/router";
import { state } from "@/mocks/state";
import { closeHelp } from "./helpStore";

async function renderAt(path: string) {
  const router = createAppRouter([path]);
  await router.load();
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
  return router;
}
const gone = () => waitFor(() => expect(screen.queryByRole("dialog", { name: "Help" })).toBeNull());
const dlg = () => screen.findByRole("dialog", { name: "Help" });

afterEach(async () => {
  closeHelp();
  await waitFor(() => expect(document.body.hasAttribute("data-scroll-locked")).toBe(false));
  history.replaceState(null, "", "/");
  vi.restoreAllMocks();
});

describe("help dialog", () => {
  it("? opens help on the route's topic, but not while typing", async () => {
    await renderAt("/library");
    const search = await screen.findByRole("searchbox", { name: "Search documents" });
    await userEvent.type(search, "?");
    expect(screen.queryByRole("dialog", { name: "Help" })).toBeNull();
    search.blur();
    await userEvent.keyboard("?");
    const d = await dlg();
    expect(await within(d).findByRole("heading", { level: 1, name: "Finding papers" })).toBeInTheDocument();
  });

  it("opens from the top bar and the sidebar", async () => {
    await renderAt("/library");
    await userEvent.click(await screen.findByTitle("Help (?)"));
    await dlg();
    await userEvent.keyboard("{Escape}");
    await gone();
    await userEvent.click(within(screen.getByRole("navigation", { name: "Main" })).getByRole("button", { name: "Help" }));
    await dlg();
  });

  it("deep links by hash; unknown or admin-only topics fall back to the route topic", async () => {
    history.replaceState(null, "", "/library#help-chat");
    await renderAt("/library");
    expect(await screen.findByRole("heading", { level: 1, name: "Asking questions" })).toBeInTheDocument();
    closeHelp();
    await gone();
    location.hash = "#help-admin-README";
    const d = await dlg();
    expect(await within(d).findByRole("heading", { level: 1, name: "Finding papers" })).toBeInTheDocument();
    expect(within(d).queryByText("Admin")).toBeNull();
    closeHelp();
    await gone();
    location.hash = "#help-unknown";
    expect(await within(await dlg()).findByRole("heading", { level: 1, name: "Finding papers" })).toBeInTheDocument();
  });

  it("Esc closes and clears the hash, keeping history state", async () => {
    const router = await renderAt("/library");
    await router.navigate({ to: "/library" });
    const before = JSON.stringify(history.state);
    await userEvent.keyboard("?");
    await dlg();
    await waitFor(() => expect(location.hash).toBe("#help-library-search"));
    await userEvent.keyboard("{Escape}");
    await waitFor(() => expect(location.hash).toBe(""));
    expect(JSON.stringify(history.state)).toBe(before);
  });

  it("collapses groups, scrolls to sections, shows Admin only to admins", async () => {
    await renderAt("/library");
    await userEvent.keyboard("?");
    const d = await dlg();
    await within(d).findByRole("heading", { level: 1 });
    const group = [...d.querySelectorAll("summary")].find((x) => x.textContent?.includes("Using rOracle"))!;
    const det = group.closest("details")!;
    expect(det.open).toBe(true);
    await userEvent.click(group);
    expect(det.open).toBe(false);
    expect(within(d).queryByText("Admin")).toBeNull();
    const spy = vi.spyOn(Element.prototype, "scrollIntoView");
    const sec = d.querySelector<HTMLButtonElement>(".secs button")!;
    await userEvent.click(sec);
    expect(spy).toHaveBeenCalled();
  });

  it("shows the Admin group to an admin", async () => {
    state.admin = true;
    await renderAt("/library");
    await userEvent.keyboard("?");
    expect(await within(await dlg()).findByText("Admin")).toBeInTheDocument();
  });

  it("filters topics by search text and says so when nothing matches", async () => {
    await renderAt("/library");
    await userEvent.keyboard("?");
    const d = await dlg();
    const box = within(d).getByRole("searchbox", { name: "Search help" });
    await userEvent.type(box, "troubleshoot");
    await waitFor(() => expect(within(d).getAllByRole("button", { name: /Troubleshooting agents/ }).length).toBeGreaterThan(0));
    expect(within(d.querySelector(".snav")!).queryByRole("button", { name: /Keyboard shortcuts/ })).toBeNull();
    await userEvent.clear(box);
    await userEvent.type(box, "zzzqqq");
    expect(await within(d).findByText(/No topic matches/)).toBeInTheDocument();
  });

  it("copies a topic link", async () => {
    const write = vi.fn(async () => {});
    await renderAt("/library");
    Object.defineProperty(navigator, "clipboard", { value: { writeText: write }, configurable: true });
    await userEvent.keyboard("?");
    await userEvent.click(await within(await dlg()).findByRole("button", { name: /Copy link to this topic/ }));
    expect(write).toHaveBeenCalledWith(`${location.origin}/library#help-library-search`);
  });

  it("Try-it navigates and closes help", async () => {
    const router = await renderAt("/uploads");
    await userEvent.keyboard("?");
    const d = await dlg();
    await userEvent.click(await within(d).findByRole("button", { name: "Open My uploads" }));
    await waitFor(() => expect(screen.queryByRole("dialog", { name: "Help" })).toBeNull());
    expect(router.state.location.pathname).toBe("/uploads");
  });

  it("disables the handoff Try-it without an open paper", async () => {
    await renderAt("/library");
    await userEvent.keyboard("?");
    const d = await dlg();
    await userEvent.click(await within(d).findByRole("button", { name: /Hand work to an agent/ }));
    expect(await within(d).findByRole("button", { name: "Hand the open paper to an agent" })).toBeDisabled();
  });

  it("hides PAT topics when tokens are disabled", async () => {
    state.onboarding.pats_enabled = false;
    await renderAt("/library");
    await userEvent.keyboard("?");
    const d = await dlg();
    await within(d).findByRole("heading", { level: 1 });
    expect(within(d.querySelector(".snav")!).queryByRole("button", { name: /Troubleshooting agents/ })).toBeNull();
  });
});
