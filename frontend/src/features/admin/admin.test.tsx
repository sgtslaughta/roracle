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
import { LOG_CAP } from "./ConnectorRuns";

const C1 = "00000000-0000-4000-8000-000000000001"; // s3, one run still running
const C2 = "00000000-0000-4000-8000-000000000002"; // fs, no run running

async function renderAdmin(path: string, ready = "Admin") {
  const router = createAppRouter([path]);
  await router.load();
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><RouterProvider router={router} /></QueryClientProvider>);
  await screen.findByRole("navigation", { name: "Main" });
  await screen.findByText(ready);
  return router;
}

const seen: Request[] = [];
beforeEach(() => {
  seen.length = 0;
  state.admin = true;
  server.events.on("request:start", ({ request }) => { seen.push(request.clone()); });
});
afterEach(() => { server.events.removeAllListeners(); });
const sent = (method: string, pathEnd: string) => seen.filter((r) => r.method === method && new URL(r.url).pathname.endsWith(pathEnd));
const body = async (method: string, pathEnd: string) => {
  await waitFor(() => expect(sent(method, pathEnd)).toHaveLength(1));
  return sent(method, pathEnd)[0].json();
};
/** Labels may carry help text, so match the start of the accessible name. */
const field = (name: string | RegExp) => screen.getByLabelText(typeof name === "string" ? new RegExp(`^${name}`) : name);

describe("admin shell", () => {
  it("non-admins see Admins only, no sidebar entry, and no admin request is sent", async () => {
    state.admin = false;
    await renderAdmin("/admin/connectors", "Admins only");
    expect(within(screen.getByRole("navigation", { name: "Main" })).queryByRole("link", { name: "Admin" })).toBeNull();
    expect(screen.queryByRole("navigation", { name: "Admin sections" })).toBeNull();
    expect(seen.some((r) => new URL(r.url).pathname.includes("/admin/"))).toBe(false);
  });

  it("admins get the sidebar entry; /admin redirects to connectors; not-yet-built sections are disabled", async () => {
    const router = await renderAdmin("/admin", "Group share");
    expect(router.state.location.pathname).toBe("/admin/connectors");
    expect(within(screen.getByRole("navigation", { name: "Main" })).getByRole("link", { name: "Admin" })).toBeInTheDocument();
    const sub = screen.getByRole("navigation", { name: "Admin sections" });
    expect(within(sub).getAllByText(/./).map((e) => e.textContent)).toEqual(["Connectors", "Jobs", "Review", "Needs title", "Settings", "Tags", "Audit", "Users", "Stats", "Health"]);
    expect(within(sub).queryByRole("link", { name: "Health" })).toBeNull();
  });
});

describe("connectors", () => {
  it("lists connectors with kind, schedule and last run status", async () => {
    await renderAdmin("/admin/connectors", "Group share");
    const row = screen.getByText("Group share").closest("tr")!;
    expect(within(row).getByText("s3")).toBeInTheDocument();
    expect(within(row).getByText("Every 60 min")).toBeInTheDocument();
    expect(within(row).getByText("running")).toBeInTheDocument();
    expect(within(screen.getByText("Server folder").closest("tr")!).getByText("Manual")).toBeInTheDocument();
  });

  it("creates an s3 connector (secret sent, empty optional keys omitted)", async () => {
    const user = userEvent.setup();
    await renderAdmin("/admin/connectors", "Group share");
    await user.click(screen.getByRole("button", { name: "New connector" }));
    await user.type(field("Name"), "Lab bucket");
    await user.type(field("Endpoint"), "https://s3.lab.example");
    await user.type(field("Bucket"), "lab");
    await user.type(field("Secret key"), "s3cr3t");
    await user.type(field(/Run every/), "30");
    await user.click(screen.getByRole("button", { name: "Create" }));
    expect(await body("POST", "/admin/connectors")).toEqual({
      name: "Lab bucket", kind: "s3", visibility: "private", org_unit: "", schedule_minutes: 30, enabled: true,
      config: { endpoint: "https://s3.lab.example", bucket: "lab", prefix: "", region: "us-east-1", secret_key: "s3cr3t" },
    });
    expect(await screen.findByText("Lab bucket")).toBeInTheDocument();
  });

  it("creates an fs connector with include chips and an org unit", async () => {
    const user = userEvent.setup();
    await renderAdmin("/admin/connectors", "Group share");
    await user.click(screen.getByRole("button", { name: "New connector" }));
    await user.type(field("Name"), "Shelf");
    await user.selectOptions(field("Kind"), "fs");
    await user.type(field("Path"), "/data/shelf");
    await user.type(field("Include patterns"), "*.pdf{Enter}");
    await user.click(screen.getByRole("button", { name: "Remove *" }));
    await user.selectOptions(field("Visible to"), "org_unit");
    await user.type(field("Org unit"), "/Mathematics");
    await user.click(screen.getByRole("button", { name: "Create" }));
    expect(await body("POST", "/admin/connectors")).toEqual({
      name: "Shelf", kind: "fs", visibility: "org_unit", org_unit: "/Mathematics", schedule_minutes: null, enabled: true,
      config: { path: "/data/shelf", include: ["*.pdf"] },
    });
  });

  it("creates an http connector; a pending start URL is committed on submit and bad ranges are refused", async () => {
    const user = userEvent.setup();
    await renderAdmin("/admin/connectors", "Group share");
    await user.click(screen.getByRole("button", { name: "New connector" }));
    await user.type(field("Name"), "Site");
    await user.selectOptions(field("Kind"), "http");
    await user.type(field("Start URLs"), "https://site.example/papers");
    await user.clear(field(/Max depth/)); await user.type(field(/Max depth/), "5");
    await user.click(screen.getByRole("button", { name: "Create" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Max depth must be 0 to 3");
    expect(sent("POST", "/admin/connectors")).toHaveLength(0);
    await user.clear(field(/Max depth/)); await user.type(field(/Max depth/), "2");
    await user.click(screen.getByLabelText("Ingest HTML pages too"));
    await user.click(screen.getByRole("button", { name: "Create" }));
    expect(await body("POST", "/admin/connectors")).toMatchObject({
      kind: "http", config: { start_urls: ["https://site.example/papers"], max_depth: 2, max_pages: 100, respect_robots: true, ingest_html: true },
    });
  });

  it("shows a server 422 inline", async () => {
    server.use(http.post(`${API_BASE}/admin/connectors`, () => HttpResponse.json({ detail: "path is outside the allowed roots" }, { status: 422 })));
    const user = userEvent.setup();
    await renderAdmin("/admin/connectors", "Group share");
    await user.click(screen.getByRole("button", { name: "New connector" }));
    await user.type(field("Name"), "Bad");
    await user.selectOptions(field("Kind"), "fs");
    await user.type(field("Path"), "/etc");
    await user.click(screen.getByRole("button", { name: "Create" }));
    expect(await screen.findByText("path is outside the allowed roots")).toBeInTheDocument();
  });

  it("editing keeps the stored secret as *** and a cleared secret is sent as null", async () => {
    const user = userEvent.setup();
    await renderAdmin("/admin/connectors", "Group share");
    await user.click(screen.getByRole("button", { name: "Edit Group share" }));
    expect(field("Secret key")).toHaveValue("***");
    expect(field("Kind")).toBeDisabled();
    await user.clear(field("Name")); await user.type(field("Name"), "Group share 2");
    await user.click(screen.getByRole("button", { name: "Save" }));
    const b = await body("PATCH", `/admin/connectors/${C1}`);
    expect(b).toMatchObject({ name: "Group share 2", config: { secret_key: "***", bucket: "group-papers" }, schedule_minutes: 60 });
    expect(b).not.toHaveProperty("kind");
    await screen.findByText("Group share 2");
    await user.click(screen.getByRole("button", { name: "Edit Group share 2" }));
    await user.clear(field("Secret key"));
    await user.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(sent("PATCH", `/admin/connectors/${C1}`)).toHaveLength(2));
    expect((await sent("PATCH", `/admin/connectors/${C1}`)[1].json()).config.secret_key).toBeNull();
  });

  it("the enabled checkbox PATCHes only enabled", async () => {
    await renderAdmin("/admin/connectors", "Group share");
    await userEvent.click(screen.getByLabelText("Enabled: Group share"));
    expect(await body("PATCH", `/admin/connectors/${C1}`)).toEqual({ enabled: false });
  });

  it("Run now: 202 toasts a queue notice, 409 toasts the server's reason", async () => {
    const user = userEvent.setup();
    await renderAdmin("/admin/connectors", "Group share");
    await user.click(screen.getByRole("button", { name: "Run Server folder now" }));
    expect(await screen.findByText("Run queued for Server folder.")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Run Group share now" }));
    expect(await screen.findByText("a run is already queued or running")).toBeInTheDocument();
  });

  it("delete asks first, then sends DELETE", async () => {
    const user = userEvent.setup();
    await renderAdmin("/admin/connectors", "Group share");
    await user.click(screen.getByRole("button", { name: "Delete Server folder" }));
    expect(sent("DELETE", `/admin/connectors/${C2}`)).toHaveLength(0);
    await user.click(screen.getByRole("button", { name: "Delete" }));
    await waitFor(() => expect(sent("DELETE", `/admin/connectors/${C2}`)).toHaveLength(1));
    await waitFor(() => expect(screen.queryByText("Server folder")).toBeNull());
  });
});

describe("connector detail", () => {
  it("shows config, runs and a plain-text log", async () => {
    server.use(http.get(`${API_BASE}/admin/connectors/${C1}/runs/30`, () => HttpResponse.json({
      id: 30, status: "partial", started_at: "2026-11-15T01:00:00Z", finished_at: "2026-11-15T01:04:00Z", stats: { seen: 3 }, log: "<img src=x onerror=alert(1)> done",
    })));
    const user = userEvent.setup();
    await renderAdmin(`/admin/connectors/${C1}`, "Runs");
    expect(await screen.findByText("group-papers")).toBeInTheDocument();
    expect(screen.getByText("***")).toBeInTheDocument();
    const rows = screen.getAllByRole("row").slice(1);
    expect(rows).toHaveLength(3);
    await user.click(within(rows[1]).getByRole("button", { name: "View log" }));
    const log = await screen.findByText(/onerror/);
    expect(log.tagName).toBe("PRE");
    expect(log.querySelector("img")).toBeNull();
  });

  it("caps a huge log to its tail with a note", async () => {
    server.use(http.get(`${API_BASE}/admin/connectors/${C1}/runs/29`, () => HttpResponse.json({
      id: 29, status: "ok", started_at: "2026-11-15T01:00:00Z", finished_at: "2026-11-15T01:04:00Z", stats: {}, log: "head" + "x".repeat(LOG_CAP + 10) + "TAIL",
    })));
    const user = userEvent.setup();
    await renderAdmin(`/admin/connectors/${C1}`, "Runs");
    await user.click(within((await screen.findAllByRole("row"))[3]).getByRole("button", { name: "View log" }));
    const pre = await screen.findByText(/TAIL$/);
    expect(pre.textContent!.length).toBe(LOG_CAP);
    expect(screen.getByText(/Showing the last 200 KB/)).toBeInTheDocument();
  });

  it("an id that is not a uuid sends no request", async () => {
    await renderAdmin("/admin/connectors/not-an-id", "All connectors");
    expect(await screen.findByText(/doesn't exist/)).toBeInTheDocument();
    expect(seen.some((r) => new URL(r.url).pathname.includes("/admin/connectors/"))).toBe(false);
  });

  it("polls the runs list while a run is running", async () => {
    let n = 0;
    server.use(http.get(`${API_BASE}/admin/connectors/${C1}/runs`, () => {
      n++;
      return HttpResponse.json({ items: [{ id: 31, status: n > 1 ? "ok" : "running", started_at: "2026-11-15T01:00:00Z", finished_at: null, stats: {} }], next_before_id: null });
    }));
    await renderAdmin(`/admin/connectors/${C1}`, "Runs");
    expect(await screen.findByText("running")).toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole("row", { name: /ok/ })).toBeInTheDocument(), { timeout: 6000 });
  }, 10_000);
});

describe("jobs", () => {
  it("shows summary counts and filters by status", async () => {
    const user = userEvent.setup();
    await renderAdmin("/admin/jobs", "Queue");
    expect(await screen.findAllByText(/1 failed/)).toHaveLength(2);
    await user.selectOptions(field("Status"), "failed");
    await waitFor(() => expect(seen.some((r) => r.url.includes("/admin/jobs?") && r.url.includes("status=failed"))).toBe(true));
    await waitFor(() => expect(screen.getAllByRole("row")).toHaveLength(3));
  });

  it("retries one failed job; a 409 toasts the reason", async () => {
    const user = userEvent.setup();
    await renderAdmin("/admin/jobs", "Queue");
    await user.click(await screen.findByRole("button", { name: "Retry job 812" }));
    expect(await screen.findByText("Job 812 queued again.")).toBeInTheDocument();
    server.use(http.post(`${API_BASE}/admin/jobs/811/retry`, () => HttpResponse.json({ detail: "connector already has an active run" }, { status: 409 })));
    await user.click(await screen.findByRole("button", { name: "Retry job 811" }));
    expect(await screen.findByText("connector already has an active run")).toBeInTheDocument();
  });

  it("Retry all failed asks first and sends the kind filter", async () => {
    const user = userEvent.setup();
    await renderAdmin("/admin/jobs", "Queue");
    await screen.findAllByText(/1 failed/);
    await user.selectOptions(field("Kind"), "ingest");
    await user.click(screen.getByRole("button", { name: "Retry all failed" }));
    expect(sent("POST", "/admin/jobs/retry-failed")).toHaveLength(0);
    await user.click(screen.getByRole("button", { name: "Retry all" }));
    expect(await body("POST", "/admin/jobs/retry-failed")).toEqual({ kind: "ingest" });
    expect(await screen.findByText("Queued 1 failed job again.")).toBeInTheDocument();
  });

  it("shows error text as plain text", async () => {
    server.use(http.get(`${API_BASE}/admin/jobs`, () => HttpResponse.json({
      items: [{ id: 5, kind: "ingest", status: "failed", attempts: 3, last_error: "<b>boom</b>", payload: {}, created_at: "2026-11-15T08:00:00Z", updated_at: "2026-11-15T08:00:00Z" }], next_before_id: null,
    })));
    await renderAdmin("/admin/jobs", "Queue");
    expect(await screen.findByText("<b>boom</b>")).toBeInTheDocument();
  });
});

describe("review and needs-title", () => {
  it("lists near-duplicates and decides through the near-dup endpoint", async () => {
    const user = userEvent.setup();
    await renderAdmin("/admin/review", "Uploads that look like documents already in the library. Oldest first.");
    const row = (await screen.findByText("chen-aldana-preprint.pdf")).closest("tr")!;
    expect(within(row).getByRole("link", { name: /Lower bounds on the spectral gap/ })).toHaveAttribute("href", "/d/00000000-0000-4000-8000-000000000001");
    await user.click(within(row).getByRole("button", { name: /Keep chen-aldana-preprint.pdf separate/ }));
    expect(await body("POST", "/documents/u-4/versions/uv-4/near-dup")).toEqual({ action: "separate" });
    expect(await screen.findByText("Nothing to review.")).toBeInTheDocument();
  });

  it("source filter goes to the query string", async () => {
    await renderAdmin("/admin/review", "Uploads that look like documents already in the library. Oldest first.");
    await userEvent.click(screen.getByRole("button", { name: "Connectors" }));
    await waitFor(() => expect(seen.some((r) => r.url.includes("/admin/review") && r.url.includes("source=connector"))).toBe(true));
  });

  it("needs-title queue shows under /admin/needs-title for admins", async () => {
    await renderAdmin("/admin/needs-title", "Waiting for a title");
    expect(await screen.findByText("untitled-scan-0412.pdf")).toBeInTheDocument();
  });
});
