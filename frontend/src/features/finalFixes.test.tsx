import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider } from "@tanstack/react-router";
import { http, HttpResponse } from "msw";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { toast } from "sonner";
import { API_BASE } from "@/api/client";
import { createAppRouter } from "@/app/router";
import { docTitle } from "@/lib/docTitle";
import { server } from "@/mocks/server";
import { resetReaderStore } from "@/features/reader/useReaderStore";

vi.mock("sonner", async (orig) => ({ ...(await orig<typeof import("sonner")>()), toast: Object.assign(vi.fn(), { error: vi.fn() }) }));
vi.mock("@/features/reader/pdfjs", () => ({ loadPdf: async () => ({ numPages: 1, getPage: async () => ({ getViewport: () => ({ width: 1, height: 1, scale: 1 }), render: () => ({ promise: Promise.resolve() }) }) }) }));
beforeEach(() => { resetReaderStore(); vi.mocked(toast).mockClear(); });

async function go(path: string) {
  const router = createAppRouter([path]);
  await router.load();
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><RouterProvider router={router} /></QueryClientProvider>);
}

describe("docTitle", () => {
  it("falls back to filename then a placeholder, never empty", () => {
    expect(docTitle({ title: "  A  ", filename: "a.pdf" })).toBe("A");
    expect(docTitle({ title: "", filename: "a.pdf" })).toBe("a.pdf");
    expect(docTitle({ title: " ", filename: null })).toBe("Untitled document");
  });
});

describe("untitled document in the reader", () => {
  it("shows the filename and lets the owner add a title, refreshing the doc", async () => {
    let patched: unknown;
    let title = "";
    server.use(
      http.get(`${API_BASE}/documents/:id`, ({ params }) => HttpResponse.json({
        id: params.id, title, authors: [], year: 2021, venue: null, org_unit: "/A", visibility: "everyone", owner: { id: "u-me", username: "researcher" },
        summary: null, stage: "searchable", tags: [], votes: { up: 0, down: 0, mine: 0 }, file_type: "PDF", page_count: 1, flag: null,
        versions: [{ id: "v1", version: 1, filename: "scan.pdf", uploaded_at: "2026-10-01T10:00:00Z", is_latest: true }],
      })),
      http.patch(`${API_BASE}/documents/:id`, async ({ request }) => { patched = await request.json(); title = (patched as { title: string }).title; return HttpResponse.json({}); }),
    );
    await go("/d/00000000-0000-4000-8000-000000000001");
    expect(await screen.findByRole("heading", { name: "scan.pdf" })).toBeInTheDocument();
    await userEvent.click(await screen.findByRole("button", { name: "Add title" }));
    await userEvent.type(screen.getByLabelText(/^Title for/), "Scanned notes");
    await userEvent.click(screen.getByRole("button", { name: "Save title" }));
    await waitFor(() => expect(patched).toEqual({ title: "Scanned notes" }));
    expect(await screen.findByRole("heading", { name: "Scanned notes" })).toBeInTheDocument();
  });
});

describe("uploads beyond the first page", () => {
  it("offers Load more when the server has more than it returned and asks for a larger limit", async () => {
    const limits: string[] = [];
    const item = (i: number) => ({ document_id: `d${i}`, version_id: `v${i}`, filename: `f${i}.pdf`, title: `T${i}`, size: 1, uploaded_at: "2026-10-01T10:00:00Z", stage: "searchable", status: "done", visibility: "everyone" });
    server.use(http.get(`${API_BASE}/me/uploads`, ({ request }) => {
      const limit = Number(new URL(request.url).searchParams.get("limit"));
      limits.push(String(limit));
      return HttpResponse.json({ items: Array.from({ length: Math.min(limit, 300) }, (_, i) => item(i)), total: 300 });
    }));
    await go("/uploads");
    await userEvent.click(await screen.findByRole("button", { name: /Load more \(100 not loaded\)/ }));
    await waitFor(() => expect(limits).toContain("400"));
    await waitFor(() => expect(screen.queryByRole("button", { name: /Load more/ })).toBeNull());
  });
});

describe("remove from collection", () => {
  it("toasts with Undo that PUTs the document back", async () => {
    const puts: string[] = [];
    server.use(http.put(`${API_BASE}/collections/:cid/items/:doc`, ({ params }) => { puts.push(String(params.doc)); return HttpResponse.json({ id: "c1", count: 1 }); }));
    await go("/library?collection=c1");
    await screen.findByText(/of \d+ documents/);
    const card = screen.getAllByRole("listitem").find((li) => li.classList.contains("result"))!;
    await userEvent.click(within(card).getByRole("button", { name: /^Actions for/ }));
    await userEvent.click(await screen.findByRole("menuitem", { name: "Remove from collection" }));
    await waitFor(() => expect(toast).toHaveBeenCalledWith("Removed from collection.", expect.objectContaining({ action: expect.objectContaining({ label: "Undo" }) })));
    const opts = vi.mocked(toast).mock.calls.find((c) => c[0] === "Removed from collection.")![1] as unknown as { action: { onClick: () => void } };
    opts.action.onClick();
    await waitFor(() => expect(puts).toEqual([card.getAttribute("data-doc")]));
  });
});
