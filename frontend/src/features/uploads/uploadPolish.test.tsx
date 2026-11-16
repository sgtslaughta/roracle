import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider } from "@tanstack/react-router";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";
import { API_BASE, apiFetch } from "@/api/client";
import { createAppRouter } from "@/app/router";
import { server } from "@/mocks/server";
import { state } from "@/mocks/state";

async function renderUploads() {
  const router = createAppRouter(["/uploads"]);
  await router.load();
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><RouterProvider router={router} /></QueryClientProvider>);
  await screen.findByText("chen-aldana-preprint.pdf");
}
const input = () => screen.getByLabelText(/Drop files here or choose files/);

describe("upload visibility and title", () => {
  it("defaults to everyone, sends visibility and the optional title", async () => {
    const seen: Record<string, unknown>[] = [];
    server.use(http.post(`${API_BASE}/documents`, async ({ request }) => {
      const f = await request.formData();
      seen.push({ visibility: f.get("visibility"), title: f.get("title") });
      return HttpResponse.json({ document_id: "x", version_id: "y", sha256: "0" }, { status: 201 });
    }));
    await renderUploads();
    expect(screen.getByLabelText("Visible to")).toHaveValue("everyone");
    expect([...screen.getByLabelText("Visible to").querySelectorAll("option")].map((o) => o.value)).toEqual(["everyone", "org_unit", "private", "shared"]);
    await userEvent.upload(input(), new File(["x"], "a.pdf", { type: "application/pdf" }));
    await waitFor(() => expect(seen).toEqual([{ visibility: "everyone", title: null }]));
    await userEvent.selectOptions(screen.getByLabelText("Visible to"), "private");
    await userEvent.type(screen.getByLabelText(/^Title \(optional/), "My paper");
    await userEvent.upload(input(), new File(["x"], "b.pdf", { type: "application/pdf" }));
    await waitFor(() => expect(seen[1]).toEqual({ visibility: "private", title: "My paper" }));
  });

  it("explains that sharing is set after upload", async () => {
    await renderUploads();
    await userEvent.selectOptions(screen.getByLabelText("Visible to"), "shared");
    expect(screen.getByText(/Choose who to share with after the upload finishes/)).toBeInTheDocument();
  });
});

describe("untitled documents", () => {
  it("a finished upload with no title offers Add title, saved via PATCH", async () => {
    state.uploads.find((u) => u.document_id === "u-1")!.title = null;
    const bodies: unknown[] = [];
    server.use(http.patch(`${API_BASE}/documents/:id`, async ({ request, params }) => { bodies.push([params.id, await request.json()]); return HttpResponse.json({}); }));
    await renderUploads();
    const row = screen.getByText("spectral-gap-v3.pdf").closest(".trow") as HTMLElement;
    await userEvent.click(within(row).getByRole("button", { name: "Add title" }));
    await userEvent.type(within(row).getByLabelText(/Title for spectral-gap-v3.pdf/), "Spectral gap v3");
    await userEvent.click(within(row).getByRole("button", { name: "Save title" }));
    await waitFor(() => expect(bodies).toEqual([["u-1", { title: "Spectral gap v3" }]]));
  });
});

describe("mock /me/uploads", () => {
  it("drops cancelled uploads and honors limit/offset", async () => {
    state.uploads[0].stage = "cancelled";
    const all = await apiFetch<{ items: unknown[]; total: number }>("/me/uploads?limit=200");
    expect(all.total).toBe(4);
    const page = await apiFetch<{ items: { document_id: string }[]; total: number }>("/me/uploads?limit=2&offset=1");
    expect(page.items).toHaveLength(2);
    expect(page.total).toBe(4);
    expect(page.items[0].document_id).toBe(state.uploads[2].document_id);
  });
});
