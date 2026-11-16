import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider } from "@tanstack/react-router";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { createAppRouter } from "@/app/router";
import { server } from "@/mocks/server";
import { state } from "@/mocks/state";
import { ApiError } from "@/api/client";
import { invalidText } from "./api";

let qc: QueryClient;
async function renderApp(path: string) {
  const router = createAppRouter([path]);
  await router.load();
  qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={qc}><RouterProvider router={router} /></QueryClientProvider>);
  await screen.findByRole("navigation", { name: "Main" });
  return router;
}

const seen: Request[] = [];
beforeEach(() => {
  seen.length = 0;
  server.events.on("request:start", ({ request }) => { seen.push(request.clone()); });
});
afterEach(() => { server.events.removeAllListeners(); });
const sent = (method: string, re: RegExp) => seen.filter((r) => r.method === method && re.test(new URL(r.url).pathname));

describe("settings", () => {
  it("is reached from the avatar menu", async () => {
    const user = userEvent.setup();
    const router = await renderApp("/library");
    await user.click(screen.getByRole("button", { name: "Account menu" }));
    await user.click(await within(await screen.findByRole("navigation", { name: "Account" })).findByRole("link", { name: "Settings" }));
    expect(await screen.findByRole("heading", { level: 1, name: "Settings" })).toBeInTheDocument();
    expect(router.state.location.pathname).toBe("/settings");
  });
});

describe("access tokens", () => {
  it("lists tokens with prefix, scopes and state", async () => {
    await renderApp("/settings");
    const row = (await screen.findByText("Laptop agent")).closest("tr")!;
    expect(within(row).getByText("ror_k3Pq…")).toBeInTheDocument();
    expect(within(row).getByText("Read")).toBeInTheDocument();
    expect(within(row).getByText("Active")).toBeInTheDocument();
  });

  it("creates a token, shows the secret once, and forgets it on close", async () => {
    const user = userEvent.setup();
    await renderApp("/settings");
    await user.click(await screen.findByRole("button", { name: "New token" }));
    const dlg = await screen.findByRole("dialog");
    await user.type(within(dlg).getByLabelText("Name"), "CI agent");
    await user.click(within(dlg).getByLabelText(/Read and write/));
    const days = within(dlg).getByLabelText("Expires after (days)");
    await user.clear(days); await user.type(days, "400");
    expect(within(dlg).getByRole("button", { name: "Create token" })).toBeDisabled();
    expect(within(dlg).getByText(/1 to 365 days/)).toBeInTheDocument();
    await user.clear(days); await user.type(days, "90");
    await user.click(within(dlg).getByRole("button", { name: "Create token" }));

    const secret = await within(dlg).findByLabelText("Your new token");
    const value = (secret as HTMLInputElement).value;
    expect(value).toMatch(/^ror_/);
    expect(await sent("POST", /\/me\/tokens$/)[0].json()).toEqual({ name: "CI agent", scopes: ["read", "write"], expires_in_days: 90 });
    expect(within(dlg).getByRole("button", { name: "Copy token" })).toBeInTheDocument();
    // never cached
    const cached = JSON.stringify([qc.getQueryCache().getAll().map((q) => q.state.data), qc.getMutationCache().getAll().map((m) => m.state.data)]);
    expect(cached).not.toContain(value);

    await user.click(within(dlg).getByRole("button", { name: "Done" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(screen.queryByDisplayValue(value)).toBeNull();
    expect(await screen.findByText("CI agent")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "New token" }));
    const again = await screen.findByRole("dialog");
    expect(within(again).queryByLabelText("Your new token")).toBeNull();
    expect(within(again).getByLabelText("Name")).toHaveValue("");
  });

  it("defaults to read-only for 30 days", async () => {
    const user = userEvent.setup();
    await renderApp("/settings");
    await user.click(await screen.findByRole("button", { name: "New token" }));
    const dlg = await screen.findByRole("dialog");
    await user.type(within(dlg).getByLabelText("Name"), "x");
    await user.click(within(dlg).getByRole("button", { name: "Create token" }));
    await within(dlg).findByLabelText("Your new token");
    expect(await sent("POST", /\/me\/tokens$/)[0].json()).toEqual({ name: "x", scopes: ["read"], expires_in_days: 30 });
  });

  it("revokes after confirmation", async () => {
    const user = userEvent.setup();
    await renderApp("/settings");
    await user.click(await screen.findByRole("button", { name: "Revoke Laptop agent" }));
    const dlg = await screen.findByRole("dialog");
    expect(sent("DELETE", /\/me\/tokens\//)).toHaveLength(0);
    await user.click(within(dlg).getByRole("button", { name: "Revoke" }));
    await waitFor(() => expect(sent("DELETE", /\/me\/tokens\/tok-1$/)).toHaveLength(1));
    expect(state.tokens![0].revoked_at).not.toBeNull();
    const row = (await screen.findByText("Laptop agent")).closest("tr")!;
    expect(await within(row).findByText("Revoked")).toBeInTheDocument();
    expect(within(row).queryByRole("button", { name: /Revoke/ })).toBeNull();
  });
});

describe("invalidText", () => {
  it("reads string, pydantic-list and unknown 422 details", () => {
    expect(invalidText(new ApiError(422, "name must be 1-100 characters"))).toBe("name must be 1-100 characters");
    expect(invalidText(new ApiError(422, [{ loc: ["body", "expires_in_days"], msg: "Input should be less than or equal to 365" }]))).toBe("expires_in_days: Input should be less than or equal to 365");
    expect(invalidText(new ApiError(422, { odd: true }))).toBe("Check the fields and try again.");
  });
});
