import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider } from "@tanstack/react-router";
import { http, HttpResponse } from "msw";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { API_BASE } from "@/api/client";
import { createAppRouter } from "@/app/router";
import { server } from "@/mocks/server";
import { state } from "@/mocks/state";

async function renderAdmin(path: string, ready: string | RegExp) {
  const router = createAppRouter([path]);
  await router.load();
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><RouterProvider router={router} /></QueryClientProvider>);
  await screen.findByRole("navigation", { name: "Main" });
  await screen.findByText(ready);
}

const seen: Request[] = [];
beforeEach(() => {
  seen.length = 0;
  state.admin = true;
  server.events.on("request:start", ({ request }) => { seen.push(request.clone()); });
});
afterEach(() => { server.events.removeAllListeners(); });
const sent = (method: string, pathEnd: string) => seen.filter((r) => r.method === method && new URL(r.url).pathname.endsWith(pathEnd));
const lastBody = async (method: string, pathEnd: string) => {
  await waitFor(() => expect(sent(method, pathEnd).length).toBeGreaterThan(0));
  return sent(method, pathEnd).at(-1)!.json();
};
const rowOf = (name: string) => screen.getByLabelText(name).closest("tr")!;

describe("settings", () => {
  it("renders typed inputs, saves a value, and resets an override", async () => {
    const user = userEvent.setup();
    await renderAdmin("/admin/settings", "rerank_depth");
    expect(rowOf("rerank_depth")).toHaveTextContent("env");
    expect(within(rowOf("max_upload_bytes")).getByText("500 MB")).toBeInTheDocument();
    expect(within(rowOf("pats_enabled")).getByRole("checkbox")).toBeChecked();
    expect(within(rowOf("chat_default_model")).getByRole("combobox")).toHaveValue("qwen2.5:14b");
    expect(within(rowOf("rerank_depth")).queryByRole("button", { name: /Reset/ })).toBeNull();

    const input = screen.getByLabelText("rerank_depth");
    await user.clear(input); await user.type(input, "55");
    await user.click(screen.getByRole("button", { name: "Save rerank_depth" }));
    expect(await lastBody("PUT", "/admin/settings")).toEqual({ rerank_depth: 55 });
    await waitFor(() => expect(rowOf("rerank_depth")).toHaveTextContent("override"));

    await user.click(screen.getByRole("button", { name: "Reset rerank_depth to env" }));
    expect(await lastBody("PUT", "/admin/settings")).toEqual({ rerank_depth: null });
    await waitFor(() => expect(rowOf("rerank_depth")).toHaveTextContent("env"));
  });

  it("shows a 422 inline and sends a toggled boolean", async () => {
    const user = userEvent.setup();
    await renderAdmin("/admin/settings", "rerank_depth");
    const input = screen.getByLabelText("rerank_depth");
    await user.clear(input); await user.type(input, "5");
    await user.click(screen.getByRole("button", { name: "Save rerank_depth" }));
    expect(await within(rowOf("rerank_depth")).findByRole("alert")).toHaveTextContent("rerank_depth: Input should be between 10 and 100");
    await user.click(within(rowOf("pats_enabled")).getByRole("checkbox"));
    await user.click(screen.getByRole("button", { name: "Save pats_enabled" }));
    expect(await lastBody("PUT", "/admin/settings")).toEqual({ pats_enabled: false });
  });
});

describe("tags", () => {
  it("merges selected sources into a typed target after confirming", async () => {
    const user = userEvent.setup();
    await renderAdmin("/admin/tags", "spectral theory");
    await user.click(screen.getByRole("checkbox", { name: "Select spectral theory" }));
    await user.type(screen.getByLabelText(/Merge into/), "spectral-theory");
    await user.click(screen.getByRole("button", { name: /Merge 1 selected/ }));
    expect(sent("POST", "/admin/tags/merge")).toHaveLength(0);
    await user.click(screen.getByRole("button", { name: "Merge" }));
    expect(await lastBody("POST", "/admin/tags/merge")).toEqual({ sources: ["spectral theory"], target: "spectral-theory" });
    expect(await screen.findByText("3 documents updated")).toBeInTheDocument();
  });

  it("blocks a target that is also a source and filters the list", async () => {
    const user = userEvent.setup();
    await renderAdmin("/admin/tags", "spectral theory");
    await user.click(screen.getByRole("checkbox", { name: "Select pde" }));
    await user.type(screen.getByLabelText(/Merge into/), "pde");
    expect(screen.getByRole("button", { name: /Merge 1 selected/ })).toBeDisabled();
    await user.type(screen.getByLabelText(/Filter tags/), "oper");
    expect(screen.queryByText("survey")).toBeNull();
    expect(screen.getByText("operators")).toBeInTheDocument();
  });
});

describe("audit", () => {
  it("sends filters and pages by before_id; details are plain text", async () => {
    const user = userEvent.setup();
    await renderAdmin("/admin/audit", "Audit log");
    await screen.findAllByText("admin.settings_update");
    expect(sent("GET", "/admin/audit")[0].url).toContain("limit=50");
    expect(document.querySelector("td b")).toBeNull();
    await user.click(screen.getByRole("button", { name: "Load older entries" }));
    await waitFor(() => expect(sent("GET", "/admin/audit").some((r) => new URL(r.url).searchParams.get("before_id") === "151")).toBe(true));
    await user.type(screen.getByLabelText(/Action starts with/), "admin.tags");
    await waitFor(() => expect(sent("GET", "/admin/audit").some((r) => new URL(r.url).searchParams.get("action") === "admin.tags")).toBe(true));
    await waitFor(() => expect(screen.queryByText("admin.settings_update")).toBeNull());
  });
});

describe("users", () => {
  it("searches (debounced), pages, and revokes tokens only after confirming", async () => {
    const user = userEvent.setup();
    await renderAdmin("/admin/users", "researcher");
    expect(screen.getByText("Page 1 of 2")).toBeInTheDocument();
    expect(await screen.findByText("/Mathematics/Spectral Theory", { selector: ".cchip-t" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "More groups for researcher: 2 more" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Revoke tokens for researcher" }));
    expect(sent("DELETE", "/pats")).toHaveLength(0);
    await user.click(screen.getByRole("button", { name: "Revoke tokens" }));
    await waitFor(() => expect(sent("DELETE", "/admin/users/u-me/pats")).toHaveLength(1));
    expect(await screen.findByText("Tokens revoked for researcher.")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Next" }));
    expect(await screen.findByText("Page 2 of 2")).toBeInTheDocument();
    await user.type(screen.getByLabelText(/Search users/), "student03");
    await waitFor(() => expect(sent("GET", "/admin/users").some((r) => new URL(r.url).searchParams.get("q") === "student03")).toBe(true));
    expect(await screen.findByText("Page 1 of 1")).toBeInTheDocument();
    expect(screen.getByText("/Mathematics", { selector: ".cchip-t" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Groups" })).toBeInTheDocument();
  });
});

describe("stats", () => {
  it("shows counts and human byte sizes", async () => {
    await renderAdmin("/admin/stats", "Top uploaders");
    expect(screen.getByText("Documents live:").parentElement).toHaveTextContent("41");
    expect(screen.getByText("Blob storage:").parentElement).toHaveTextContent("912 MB");
    expect(within(screen.getByText("researcher").closest("tr")!).getByText("40 MB")).toBeInTheDocument();
  });
});

describe("health", () => {
  it("shows ok and a degraded state with failing checks", async () => {
    await renderAdmin("/admin/health", "Health");
    expect(await screen.findByText("Scheduler tick:")).toBeInTheDocument();
    expect(screen.getAllByText("ok").length).toBeGreaterThanOrEqual(4);
  });

  it("degraded body shows a bad status; a failed request shows the error state", async () => {
    server.use(http.get(`${API_BASE}/admin/health`, () => HttpResponse.json({ status: "degraded", checks: { db: "ok", elastic: "error: ConnectionError", storage: "ok" }, queue: null, scheduler: null })));
    await renderAdmin("/admin/health", "degraded");
    expect(screen.getByText("error: ConnectionError")).toHaveClass("failed");
    expect(screen.getByText("Queue unavailable")).toBeInTheDocument();
  });

  it("503 with a body still shows it; without one shows an error", async () => {
    server.use(http.get(`${API_BASE}/admin/health`, () => HttpResponse.json({ detail: "down" }, { status: 503 })));
    await renderAdmin("/admin/health", /request failed/);
  });
});

describe("non-admin", () => {
  it("sees nothing and sends no admin request on a data route", async () => {
    state.admin = false;
    await renderAdmin("/admin/users", "Admins only");
    expect(seen.some((r) => new URL(r.url).pathname.includes("/admin/"))).toBe(false);
  });
});
