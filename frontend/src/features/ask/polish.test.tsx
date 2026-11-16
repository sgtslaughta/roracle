import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider } from "@tanstack/react-router";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";
import { API_BASE } from "@/api/client";
import { createAppRouter } from "@/app/router";
import { server } from "@/mocks/server";

async function renderApp(path: string) {
  const router = createAppRouter([path]);
  await router.load();
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><RouterProvider router={router} /></QueryClientProvider>);
  await screen.findByRole("navigation", { name: "Main" });
}

describe("ask polish", () => {
  it("community-note passages say so instead of a number", async () => {
    await renderApp("/ask/c1");
    await userEvent.click(await screen.findByRole("button", { name: /Show the 9 passages read/ }));
    const list = await screen.findByRole("list", { name: "Passages read" });
    expect(within(list).getByText(/Community note\./)).toBeInTheDocument();
  });

  it("a blank chat title reads New chat in the heading and the sidebar", async () => {
    server.use(http.get(`${API_BASE}/chats/c1`, () => HttpResponse.json({ id: "c1", title: "", scope: "all", model: null, pins: [], excludes: [], created_at: "2026-11-15T00:00:00Z", updated_at: "2026-11-15T00:00:00Z", messages: [], is_owner: true })));
    await renderApp("/ask/c1");
    expect(await screen.findByRole("heading", { level: 1, name: "New chat" })).toBeInTheDocument();
  });
});
