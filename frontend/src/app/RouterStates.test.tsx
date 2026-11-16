import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider } from "@tanstack/react-router";
import { createAppRouter } from "./router";
import { RouteError } from "./RouterStates";

describe("router states", () => {
  it("renders a styled not-found page with a Library link", async () => {
    const router = createAppRouter(["/nope/nothing"]);
    await router.load();
    render(<QueryClientProvider client={new QueryClient()}><RouterProvider router={router} /></QueryClientProvider>);
    expect(await screen.findByRole("heading", { name: "Page not found" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Back to Library" })).toHaveAttribute("href", "/library");
  });

  it("offers Retry for ordinary errors", async () => {
    const reset = vi.fn();
    render(<RouteError error={new Error("boom")} reset={reset} />);
    expect(screen.getByText("boom")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(reset).toHaveBeenCalled();
  });

  it("explains chunk-load failures and offers Reload", () => {
    render(<RouteError error={new Error("Failed to fetch dynamically imported module: /assets/x.js")} reset={vi.fn()} />);
    expect(screen.getByRole("heading", { name: "Could not load this page" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Reload" })).toBeInTheDocument();
  });
});
