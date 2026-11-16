import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider } from "@tanstack/react-router";
import { http, HttpResponse } from "msw";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { API_BASE } from "@/api/client";
import { createAppRouter } from "@/app/router";
import { server } from "@/mocks/server";
import { resetReaderStore } from "./useReaderStore";

const loadPdf = vi.fn(async () => ({
  numPages: 1,
  getPage: async () => ({ getViewport: ({ scale }: { scale: number }) => ({ width: 612 * scale, height: 792 * scale, scale }), render: () => ({ promise: Promise.resolve() }) }),
}));
vi.mock("./pdfjs", () => ({ loadPdf: (...a: []) => loadPdf(...a) }));

const ID = "00000000-0000-4000-8000-000000000001";
beforeEach(() => { resetReaderStore(); loadPdf.mockClear(); try { localStorage.clear(); } catch { /* */ } });

async function open() {
  const router = createAppRouter([`/d/${ID}`]);
  await router.load();
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><RouterProvider router={router} /></QueryClientProvider>);
  await screen.findByRole("heading", { name: /Lower bounds/ });
}
const noPdf = () => server.use(http.get(`${API_BASE}/documents/:id/versions/:n/pdf`, () => HttpResponse.json({ detail: "not found" }, { status: 404 })));
const named = (filename: string) => server.use(http.get(`${API_BASE}/documents/:id`, ({ params }) => HttpResponse.json({
  id: params.id, title: "Lower bounds on the spectral gap", authors: [], year: 2021, venue: null, org_unit: "/A", visibility: "everyone", owner: { id: "u-me", username: "researcher" },
  summary: null, stage: "searchable", tags: [], votes: { up: 0, down: 0, mine: 0 }, file_type: "md", page_count: 1, flag: null,
  versions: [{ id: "v1", version: 1, filename, uploaded_at: "2026-10-01T10:00:00Z", is_latest: true }],
})));

describe("reader rendition", () => {
  it("loads the /pdf rendition, not /file", async () => {
    const hits: string[] = [];
    server.use(http.get(`${API_BASE}/documents/:id/versions/:n/pdf`, () => { hits.push("pdf"); return HttpResponse.arrayBuffer(new ArrayBuffer(8), { headers: { "Content-Type": "application/pdf" } }); }),
      http.get(`${API_BASE}/documents/:id/versions/:n/file`, () => { hits.push("file"); return HttpResponse.arrayBuffer(new ArrayBuffer(8)); }));
    await open();
    await waitFor(() => expect(loadPdf).toHaveBeenCalled());
    expect(hits).toEqual(["pdf"]);
  });

  it("404 on a Markdown doc renders the file as sanitized markdown", async () => {
    noPdf(); named("notes.md");
    server.use(http.get(`${API_BASE}/documents/:id/versions/:n/file`, () => new HttpResponse("# Hello\n\n<img src=x onerror=alert(1)> **bold**", { headers: { "Content-Type": "text/markdown" } })));
    await open();
    expect(await screen.findByRole("heading", { name: "Hello" })).toBeInTheDocument();
    expect(document.querySelector(".doctext img")).toBeNull();
  });

  it("404 on a .txt doc renders a <pre>, never HTML", async () => {
    noPdf(); named("a.txt");
    server.use(http.get(`${API_BASE}/documents/:id/versions/:n/file`, () => new HttpResponse("<b>plain</b>", { headers: { "Content-Type": "text/plain" } })));
    await open();
    const pre = await waitFor(() => { const p = document.querySelector(".doctext pre"); expect(p).not.toBeNull(); return p!; });
    expect(pre.textContent).toBe("<b>plain</b>");
    expect(pre.querySelector("b")).toBeNull();
  });

  it("a text file over 2 MB offers the download instead", async () => {
    noPdf(); named("big.md");
    server.use(http.get(`${API_BASE}/documents/:id/versions/:n/file`, () => new HttpResponse("x".repeat(2 * 1024 * 1024 + 1), { headers: { "Content-Type": "text/markdown" } })));
    await open();
    expect(await screen.findByText(/Preview not available/)).toBeInTheDocument();
  });

  it("404 on anything else shows Preview not available and downloads on demand", async () => {
    noPdf(); named("data.bin");
    let fetched = 0;
    server.use(http.get(`${API_BASE}/documents/:id/versions/:n/file`, () => { fetched++; return HttpResponse.arrayBuffer(new ArrayBuffer(4)); }));
    await open();
    expect(await screen.findByText(/Preview not available/)).toBeInTheDocument();
    expect(fetched).toBe(0);
    await userEvent.click(within(screen.getByRole("alert")).getByRole("button", { name: /Download/ }));
    await waitFor(() => expect(fetched).toBe(1));
  });
});

describe("sharing dialog", () => {
  it("owner sets visibility shared with users and groups via PATCH", async () => {
    let body: unknown;
    server.use(http.patch(`${API_BASE}/documents/:id`, async ({ request }) => { body = await request.json(); return HttpResponse.json({}); }));
    await open();
    await userEvent.click(await screen.findByRole("button", { name: "Sharing" }));
    expect(screen.getByText(/Shares only apply when visibility is “Shared”/)).toBeInTheDocument();
    await userEvent.selectOptions(screen.getByLabelText("Visible to"), "shared");
    await userEvent.type(screen.getByLabelText("Share with groups"), "Math");
    await userEvent.click(await screen.findByRole("button", { name: "/Mathematics" }));
    await userEvent.type(screen.getByLabelText("Share with users"), "an");
    await userEvent.click(await screen.findByRole("button", { name: "ana" }));
    expect(screen.getByRole("button", { name: "Remove ana" })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(body).toEqual({ visibility: "shared", share_users: ["11111111-1111-4111-8111-111111111111"], share_groups: ["/Mathematics"] }));
  });

  it("private sends no share lists; hidden for non-owners", async () => {
    let body: unknown;
    server.use(http.patch(`${API_BASE}/documents/:id`, async ({ request }) => { body = await request.json(); return HttpResponse.json({}); }));
    await open();
    await userEvent.click(await screen.findByRole("button", { name: "Sharing" }));
    await userEvent.selectOptions(screen.getByLabelText("Visible to"), "private");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(body).toEqual({ visibility: "private", share_users: [], share_groups: [] }));
  });

  it("sends only the edited list", async () => {
    let body: unknown;
    server.use(http.patch(`${API_BASE}/documents/:id`, async ({ request }) => { body = await request.json(); return HttpResponse.json({}); }));
    await open();
    await userEvent.click(await screen.findByRole("button", { name: "Sharing" }));
    await userEvent.selectOptions(screen.getByLabelText("Visible to"), "shared");
    await userEvent.type(screen.getByLabelText("Share with groups"), "Phys");
    await userEvent.click(await screen.findByRole("button", { name: "/Physics" }));
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(body).toEqual({ visibility: "shared", share_groups: ["/Physics"] }));
  });

  it("emptying a list on an already shared doc needs a second Save", async () => {
    const bodies: unknown[] = [];
    server.use(http.patch(`${API_BASE}/documents/:id`, async ({ request }) => { bodies.push(await request.json()); return HttpResponse.json({}); }));
    server.use(http.get(`${API_BASE}/documents/:id`, ({ params }) => HttpResponse.json({
      id: params.id, title: "Lower bounds on the spectral gap", authors: [], year: 2021, venue: null, org_unit: "/A", visibility: "shared", owner: { id: "u-me", username: "researcher" },
      summary: null, stage: "searchable", tags: [], votes: { up: 0, down: 0, mine: 0 }, file_type: "PDF", page_count: 1, flag: null, share_groups: ["/Math"],
      versions: [{ id: "v1", version: 1, filename: "a.pdf", uploaded_at: "2026-10-01T10:00:00Z", is_latest: true }],
    })));
    await open();
    await userEvent.click(await screen.findByRole("button", { name: "Sharing" }));
    await userEvent.click(screen.getByRole("button", { name: "Remove /Math" }));
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByText(/Press Save again/)).toBeInTheDocument();
    expect(bodies).toEqual([]);
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(bodies).toEqual([{ visibility: "shared", share_groups: [] }]));
  });

  it("switching visibility away and back drops a pending empty-list confirmation", async () => {
    const bodies: unknown[] = [];
    server.use(http.patch(`${API_BASE}/documents/:id`, async ({ request }) => { bodies.push(await request.json()); return HttpResponse.json({}); }),
      http.get(`${API_BASE}/documents/:id`, ({ params }) => HttpResponse.json({
        id: params.id, title: "Lower bounds on the spectral gap", authors: [], year: 2021, venue: null, org_unit: "/A", visibility: "shared", owner: { id: "u-me", username: "researcher" },
        summary: null, stage: "searchable", tags: [], votes: { up: 0, down: 0, mine: 0 }, file_type: "PDF", page_count: 1, flag: null, share_groups: ["/Math"],
        versions: [{ id: "v1", version: 1, filename: "a.pdf", uploaded_at: "2026-10-01T10:00:00Z", is_latest: true }],
      })));
    await open();
    await userEvent.click(await screen.findByRole("button", { name: "Sharing" }));
    await userEvent.click(screen.getByRole("button", { name: "Remove /Math" }));
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByText(/Press Save again/)).toBeInTheDocument();
    await userEvent.selectOptions(screen.getByLabelText("Visible to"), "org_unit");
    await userEvent.selectOptions(screen.getByLabelText("Visible to"), "shared");
    expect(screen.queryByText(/Press Save again/)).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByText(/Press Save again/)).toBeInTheDocument();
    expect(bodies).toEqual([]);
  });

  it("non-owner sees no Sharing button", async () => {
    server.use(http.get(`${API_BASE}/documents/:id`, ({ params }) => HttpResponse.json({
      id: params.id, title: "Lower bounds on the spectral gap", authors: [], year: 2021, venue: null, org_unit: "/A", visibility: "everyone", owner: { id: "other", username: "k" },
      summary: null, stage: "searchable", tags: [], votes: { up: 0, down: 0, mine: 0 }, file_type: "PDF", page_count: 1, flag: null,
      versions: [{ id: "v1", version: 1, filename: "a.pdf", uploaded_at: "2026-10-01T10:00:00Z", is_latest: true }],
    })));
    await open();
    await screen.findByRole("button", { name: /Add to collection/ });
    expect(screen.queryByRole("button", { name: "Sharing" })).toBeNull();
  });
});
