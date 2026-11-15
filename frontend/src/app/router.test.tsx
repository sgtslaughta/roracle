import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider } from "@tanstack/react-router";
import { createAppRouter } from "./router";

async function renderAt(path: string) {
  const router = createAppRouter([path]);
  await router.load();
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
  return router;
}

describe("routes", () => {
  it("redirects / to /library", async () => {
    const r = await renderAt("/");
    expect(r.state.location.pathname).toBe("/library");
    expect(await screen.findByRole("heading", { name: "Library" })).toBeInTheDocument();
  });

  it.each([
    ["/ask", "Ask"],
    ["/uploads", "My uploads"],
    ["/proposals", "Agent proposals"],
    ["/drafts", "Drafts"],
  ])("renders %s", async (path, title) => {
    await renderAt(path);
    expect(await screen.findByRole("heading", { level: 1, name: title })).toBeInTheDocument();
  });

  it("renders doc routes inside the shell", async () => {
    await renderAt("/d/d1");
    expect(await screen.findByRole("navigation", { name: "Main" })).toBeInTheDocument();
    await renderAt("/d/d1/v/2");
    expect((await screen.findAllByRole("navigation", { name: "Main" })).length).toBeGreaterThan(0);
  });

  it("marks the current page in the sidebar", async () => {
    await renderAt("/uploads");
    const link = await screen.findByRole("link", { name: /My uploads/ });
    expect(link).toHaveAttribute("aria-current", "page");
  });
});
