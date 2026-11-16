import { readFileSync } from "node:fs";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider } from "@tanstack/react-router";
import { http, HttpResponse } from "msw";
import { describe, expect, it, vi } from "vitest";
import { API_BASE } from "@/api/client";
import { createAppRouter } from "@/app/router";
import { server } from "@/mocks/server";
import { state } from "@/mocks/state";
import { isTerminal } from "./api";
import { pipelineDone, viewStatus } from "./status";

async function renderUploads(wait = "chen-aldana-preprint.pdf") {
  const router = createAppRouter(["/uploads"]);
  await router.load();
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
  await screen.findByText(wait);
}
const file = (name: string, bytes = 10, type = "application/pdf") => {
  const f = new File(["x".repeat(Math.min(bytes, 10))], name, { type });
  if (bytes > 10) Object.defineProperty(f, "size", { value: bytes });
  return f;
};
const rows = () => document.querySelectorAll(".trow:not(.h)");

describe("stage mapping", () => {
  it("labels: summarized and deduped report as Parsed; review is its own state", () => {
    expect(pipelineDone("uploaded")).toBe(0);
    expect(pipelineDone("scanned")).toBe(1);
    expect(pipelineDone("parsed")).toBe(2);
    expect(pipelineDone("summarized")).toBe(2);
    expect(pipelineDone("deduped")).toBe(2);
    expect(pipelineDone("chunked")).toBe(3);
    expect(pipelineDone("embedded")).toBe(4);
    expect(pipelineDone("searchable")).toBe(5);
    expect(viewStatus({ stage: "review", status: "done" })).toBe("review");
    expect(viewStatus({ stage: "rejected", status: "failed" })).toBe("failed");
    expect(viewStatus({ stage: "searchable", status: "done" })).toBe("done");
    expect(viewStatus({ stage: "chunked", status: "queued" })).toBe("running");
    expect(viewStatus({ stage: "needs_title", status: "running" })).toBe("title");
    expect(pipelineDone("needs_title")).toBe(2);
  });

  it("terminal stages stop polling", () => {
    for (const stage of ["searchable", "review", "needs_title", "rejected", "cancelled"]) expect(isTerminal({ status: "running", stage })).toBe(true);
    expect(isTerminal({ status: "failed", stage: "scanned" })).toBe(true);
    expect(isTerminal({ status: "running", stage: "chunked" })).toBe(false);
  });
});

describe("my uploads", () => {
  it("shows each upload with its pipeline stages and status pill", async () => {
    await renderUploads();
    expect(rows()).toHaveLength(5);
    const u2 = screen.getByText("hypergraph-cheeger-draft.docx").closest(".trow") as HTMLElement;
    expect(within(u2).getByText("Processing")).toBeInTheDocument();
    for (const l of ["Scanned", "Parsed", "Chunked", "Embedded", "Searchable"]) expect(within(u2).getByText(l)).toBeInTheDocument();
    const cells = [...u2.querySelectorAll(".pipe div")].map((d) => d.className);
    expect(cells).toEqual(["ok", "ok", "ok", "run", ""]); // chunked done, embedding in progress
    const u3 = screen.getByText("scan_1998_lecture-notes.pdf").closest(".trow") as HTMLElement;
    expect(within(u3).getByText("Failed")).toBeInTheDocument();
    expect(within(u3).getByText(/Text recognition timed out/)).toBeInTheDocument();
    const u4 = screen.getByText("chen-aldana-preprint.pdf").closest(".trow") as HTMLElement;
    expect(within(u4).getByText("Needs your decision")).toBeInTheDocument();
  });

  it("filter segments show counts and filter the table", async () => {
    await renderUploads();
    const seg = screen.getByRole("group", { name: "Filter uploads" });
    expect(within(seg).getByRole("button", { name: /^All\s*5$/ })).toBeInTheDocument();
    expect(within(seg).getByRole("button", { name: /^Processing\s*1$/ })).toBeInTheDocument();
    expect(within(seg).getByRole("button", { name: /^Needs you\s*2$/ })).toBeInTheDocument();
    expect(within(seg).getByRole("button", { name: /^Failed\s*1$/ })).toBeInTheDocument();
    expect(within(seg).getByRole("button", { name: /^Searchable\s*1$/ })).toBeInTheDocument();
    await userEvent.click(within(seg).getByRole("button", { name: /^Failed/ }));
    expect(rows()).toHaveLength(1);
    expect(screen.getByText("scan_1998_lecture-notes.pdf")).toBeInTheDocument();
  });

  it("rejects a 600 MB file client-side, naming the 500 MB limit, before any request", async () => {
    let posts = 0;
    server.use(http.post(`${API_BASE}/documents`, () => { posts++; return HttpResponse.json({}, { status: 201 }); }));
    await renderUploads();
    await userEvent.upload(screen.getByLabelText(/Drop files here or choose files/), file("huge.pdf", 600 * 1024 * 1024));
    expect(await screen.findByText(/huge\.pdf is over the 500 MB limit/)).toBeInTheDocument();
    expect(posts).toBe(0);
  });

  it("uploads a file and lists it", async () => {
    await renderUploads();
    await userEvent.upload(screen.getByLabelText(/Drop files here or choose files/), file("fresh-paper.pdf"));
    expect(await screen.findByText("fresh-paper.pdf")).toBeInTheDocument();
    expect(rows()).toHaveLength(6);
  });

  it("409 duplicate asks, then retries with force=true on confirm", async () => {
    const seen: (string | null)[] = [];
    server.use(http.post(`${API_BASE}/documents`, async ({ request }) => {
      const form = await request.formData();
      seen.push(form.get("force") as string | null);
      if (form.get("force") !== "true") return HttpResponse.json({ detail: { code: "duplicate", document_id: "00000000-0000-4000-8000-000000000001" } }, { status: 409 });
      return HttpResponse.json({ document_id: "x", version_id: "y", sha256: "0" }, { status: 201 });
    }));
    await renderUploads();
    await userEvent.upload(screen.getByLabelText(/Drop files here or choose files/), file("dup.pdf"));
    const dlg = await screen.findByRole("dialog");
    expect(within(dlg).getByText("You already have access to this document. Add it anyway?")).toBeInTheDocument();
    await userEvent.click(within(dlg).getByRole("button", { name: "Add anyway" }));
    await waitFor(() => expect(seen).toEqual([null, "true"]));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  });

  it("409 duplicate then Cancel makes no second request", async () => {
    let n = 0;
    server.use(http.post(`${API_BASE}/documents`, () => { n++; return HttpResponse.json({ detail: { code: "duplicate", document_id: "00000000-0000-4000-8000-000000000001" } }, { status: 409 }); }));
    await renderUploads();
    await userEvent.upload(screen.getByLabelText(/Drop files here or choose files/), file("dup.pdf"));
    await userEvent.click(await screen.findByRole("button", { name: "Cancel" }));
    expect(n).toBe(1);
  });

  it("shows server errors such as 413 as a message", async () => {
    server.use(http.post(`${API_BASE}/documents`, () => HttpResponse.json({ detail: "file exceeds 200 MB limit" }, { status: 413 })));
    await renderUploads();
    await userEvent.upload(screen.getByLabelText(/Drop files here or choose files/), file("big.pdf"));
    expect(await screen.findByText(/file exceeds 200 MB limit/)).toBeInTheDocument();
  });

  it("near-duplicate decision posts the chosen action", async () => {
    const bodies: unknown[] = [];
    server.use(http.post(`${API_BASE}/documents/:id/versions/:vid/near-dup`, async ({ request }) => { bodies.push(await request.json()); return HttpResponse.json({ version_id: "uv-4", document_id: "u-4", stage: "deduped" }); }));
    await renderUploads();
    const u4 = screen.getByText("chen-aldana-preprint.pdf").closest(".trow") as HTMLElement;
    expect(within(u4).getByText(/Very close to/)).toHaveTextContent("Very close to Lower bounds on the spectral gap of random regular graphs. Is it the same document?");
    expect(u4.textContent).not.toMatch(/similar|%/);
    expect(within(u4).getByRole("button", { name: "Compare" })).toBeInTheDocument();
    await userEvent.click(within(u4).getByRole("button", { name: "Yes, add as new version" }));
    await waitFor(() => expect(bodies).toEqual([{ action: "new_version" }]));
    await userEvent.click(within(u4).getByRole("button", { name: "No, keep separate" }));
    await userEvent.click(within(u4).getByRole("button", { name: "Cancel upload" }));
    await waitFor(() => expect(bodies).toEqual([{ action: "new_version" }, { action: "separate" }, { action: "cancel" }]));
  });

  it("near-duplicate prompt without a match title still works", async () => {
    server.use(http.get(`${API_BASE}/me/uploads`, () => HttpResponse.json({ items: [{ document_id: "a", version_id: "b", title: null, filename: "n.pdf", size: 1, visibility: "everyone", status: "done", error: null, uploaded_at: "2026-11-15T00:00:00Z", stage: "review", stage_label: "Needs your decision", page_count: null, near_dup_of: { document_id: "d", title: null } }], total: 1 })));
    await renderUploads("n.pdf");
    expect(screen.getByText("a document in the library")).toBeInTheDocument();
  });

  it("polls while something is processing", async () => {
    const spy = vi.fn();
    server.use(http.get(`${API_BASE}/me/uploads`, () => { spy(); return HttpResponse.json({ items: [{ document_id: "a", version_id: "b", title: "t", filename: "t.pdf", size: 1, visibility: "everyone", status: "running", error: null, uploaded_at: "2026-11-15T00:00:00Z", stage: "parsed", stage_label: "Parsed", page_count: null, near_dup_of: null }], total: 1 }); }));
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      const router = createAppRouter(["/uploads"]);
      await router.load();
      render(<QueryClientProvider client={new QueryClient()}><RouterProvider router={router} /></QueryClientProvider>);
      await screen.findByText("t.pdf");
      const before = spy.mock.calls.length;
      await vi.advanceTimersByTimeAsync(3100);
      await waitFor(() => expect(spy.mock.calls.length).toBeGreaterThan(before));
    } finally { vi.useRealTimers(); }
  });
});

describe("needs a title", () => {
  const input = () => within(screen.getByText("untitled-scan-0412.pdf").closest(".trow") as HTMLElement).getByLabelText("Document title");

  it("the row asks for a title, submits with Enter, and moves on", async () => {
    const puts: { url: string; body: unknown }[] = [];
    server.use(http.put(`${API_BASE}/documents/:id/versions/:vid/title`, async ({ request }) => {
      puts.push({ url: new URL(request.url).pathname, body: await request.json() });
      return HttpResponse.json({ version_id: "uv-5", document_id: "u-5", stage: "parsed" });
    }));
    await renderUploads();
    const row = screen.getByText("untitled-scan-0412.pdf").closest(".trow") as HTMLElement;
    expect(within(row).getByText("Needs a title")).toBeInTheDocument();
    expect(within(row).getByText("We couldn't find a title in this file. Add one so people can find it.")).toBeInTheDocument();
    expect(input()).toHaveAttribute("maxlength", "500");
    expect(within(row).getByRole("button", { name: "Save title" })).toBeDisabled(); // blank
    await userEvent.type(input(), "   ");
    expect(within(row).getByRole("button", { name: "Save title" })).toBeDisabled(); // whitespace only
    await userEvent.type(input(), "Lecture notes 1998{Enter}");
    await waitFor(() => expect(puts).toEqual([{ url: "/api/documents/u-5/versions/uv-5/title", body: { title: "Lecture notes 1998" } }]));
    expect(await screen.findByText("Title saved. Processing continues.")).toBeInTheDocument();
  });

  it("with the real mock the row leaves needs_title after saving", async () => {
    await renderUploads();
    await userEvent.type(input(), "A real title");
    await userEvent.click(screen.getByRole("button", { name: "Save title" }));
    await waitFor(() => expect(screen.queryByLabelText("Document title")).toBeNull());
    expect(screen.getByText("A real title")).toBeInTheDocument();
  });

  it("a server error is shown as a message", async () => {
    server.use(http.put(`${API_BASE}/documents/:id/versions/:vid/title`, () => HttpResponse.json({ detail: "not awaiting a title" }, { status: 409 })));
    await renderUploads();
    await userEvent.type(input(), "x{Enter}");
    expect(await screen.findByText("not awaiting a title")).toBeInTheDocument();
  });

  it("the needs-title queue lives under /admin/needs-title, not on the uploads page", async () => {
    state.admin = true;
    let asked = 0;
    server.use(http.get(`${API_BASE}/admin/needs-title`, () => { asked++; return HttpResponse.json([]); }));
    await renderUploads();
    await screen.findByLabelText("Document title");
    expect(screen.queryByRole("region", { name: "Waiting for a title" })).toBeNull();
    expect(asked).toBe(0);
  });
});

describe("keyboard access", () => {
  it("the file input is focusable (not hidden) so the drop zone works with Tab and Enter/Space", async () => {
    await renderUploads();
    const input = screen.getByLabelText(/Drop files here or choose files/) as HTMLInputElement;
    expect(input).not.toHaveAttribute("hidden");
    await userEvent.tab(); // walks the page; the input must be reachable
    input.focus();
    expect(input).toHaveFocus();
    expect(readFileSync("src/styles/uploads.css", "utf8")).toMatch(/\.drop:focus-within/);
  });
});
