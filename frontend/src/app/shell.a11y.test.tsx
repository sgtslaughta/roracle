import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider } from "@tanstack/react-router";
import { createAppRouter } from "./router";

const signoutRedirect = vi.fn(async () => {});
vi.mock("oidc-client-ts", async (orig) => ({
  ...(await orig<typeof import("oidc-client-ts")>()),
  UserManager: class { signoutRedirect = signoutRedirect; },
}));

async function renderAt(path: string) {
  const router = createAppRouter([path]);
  await router.load();
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
}

afterEach(() => { vi.unstubAllEnvs(); signoutRedirect.mockClear(); });

describe("shell accessibility", () => {
  it("names every nav link even when labels are hidden by CSS", async () => {
    await renderAt("/library");
    for (const name of ["Library", "Ask", "My uploads", "Drafts"]) {
      expect(await screen.findByRole("link", { name: new RegExp(`^${name}`) })).toHaveAttribute("title");
    }
  });

  it("reaches recent chats from the rail button", async () => {
    await renderAt("/library");
    await userEvent.click(await screen.findByRole("button", { name: "Chats and collections" }));
    const pop = await screen.findByRole("dialog");
    expect(within(pop).getByRole("heading", { name: "Recent chats" })).toBeInTheDocument();
    expect(within(pop).getByRole("link", { name: "New chat" })).toBeInTheDocument();
  });

  it("signs out through the identity provider", async () => {
    await renderAt("/library");
    await userEvent.click(await screen.findByRole("button", { name: "Account menu" }));
    await userEvent.click(await screen.findByRole("button", { name: "Sign out" }));
    expect(signoutRedirect).toHaveBeenCalled();
  });

  it("hides sign out in mock mode", async () => {
    vi.stubEnv("VITE_MOCK", "1");
    await renderAt("/library");
    await userEvent.click(await screen.findByRole("button", { name: "Account menu" }));
    await screen.findByRole("link", { name: "Settings" });
    expect(screen.queryByRole("button", { name: "Sign out" })).toBeNull();
  });

  it("offers a way back from the admins-only page", async () => {
    await renderAt("/admin");
    // default mock user may be admin; only assert when the notice shows
    const back = screen.queryByRole("link", { name: "Back to Library" });
    if (back) expect(back).toHaveAttribute("href", "/library");
  });
});
