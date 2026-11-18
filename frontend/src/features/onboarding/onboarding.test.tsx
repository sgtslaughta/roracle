import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { state } from "@/mocks/state";
import { closeHelp, helpStore } from "@/features/help/helpStore";
import { GetStarted } from "./GetStarted";

const BASICS = ["search", "ask", "upload", "collect"];
const show = () => render(
  <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><GetStarted /></QueryClientProvider>,
);
afterEach(closeHelp);

describe("GetStarted", () => {
  it("shows basics with progress", async () => {
    show();
    expect(await screen.findByRole("heading", { name: "Get started" })).toBeTruthy();
    expect(screen.getByText("1 of 4")).toBeTruthy();
    expect(screen.getByText(/Next: connect your agents/)).toBeTruthy();
  });

  it("moves on to agents after basics", async () => {
    state.onboarding.steps = BASICS;
    show();
    expect(await screen.findByRole("heading", { name: "Connect your agents" })).toBeTruthy();
    expect(screen.getByText("0 of 3")).toBeTruthy();
    expect(screen.queryByText(/Next: connect/)).toBeNull();
  });

  it("drops token and mcp without PATs", async () => {
    state.onboarding.steps = BASICS;
    state.onboarding.pats_enabled = false;
    show();
    await screen.findByRole("heading", { name: "Connect your agents" });
    expect(screen.getByText("0 of 1")).toBeTruthy();
    expect(screen.queryByText("Create an access token")).toBeNull();
  });

  it("Show me opens help on the token section", async () => {
    state.onboarding.steps = BASICS;
    show();
    await screen.findByText("Create an access token");
    await userEvent.click(screen.getAllByRole("button", { name: /Show me/ })[0]);
    expect(helpStore.get()).toMatchObject({ open: true, topic: "agents-mcp-setup", section: "create-a-token" });
  });

  it("hide sends dismissed and removes the card", async () => {
    show();
    await userEvent.click(await screen.findByRole("button", { name: "Hide Get started. Reopen it from Help." }));
    await waitFor(() => expect(screen.queryByRole("heading", { name: "Get started" })).toBeNull());
    expect(state.onboarding.dismissed).toBe(true);
  });

  it("renders nothing when dismissed", async () => {
    state.onboarding.dismissed = true;
    show();
    await waitFor(() => expect(screen.queryByRole("heading")).toBeNull());
  });
});
