import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider } from "@tanstack/react-router";
import { http, HttpResponse } from "msw";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { API_BASE } from "@/api/client";
import { createAppRouter } from "@/app/router";
import { server } from "@/mocks/server";
import { bboxToViewport } from "./bbox";
import { getReaderState, openDoc, resetReaderStore, stepBack, cycleFocus, closeReader } from "./useReaderStore";

// pdf.js needs canvas/workers: replace the loader with a fake 3-page document (612x792pt)
vi.mock("./pdfjs", () => ({
  loadPdf: async () => ({
    numPages: 3,
    getPage: async () => ({
      getViewport: ({ scale }: { scale: number }) => ({ width: 612 * scale, height: 792 * scale, scale }),
      render: () => ({ promise: Promise.resolve() }),
    }),
  }),
}));

beforeEach(() => { resetReaderStore(); try { localStorage.clear(); } catch { /* */ } });

describe("bboxToViewport", () => {
  const H = 792;
  it("BOTTOMLEFT origin flips y against the page height (scale 1)", () => {
    expect(bboxToViewport({ l: 72, r: 172, t: 700, b: 650, coord_origin: "BOTTOMLEFT" }, H, 1)).toEqual({ x: 72, y: 92, w: 100, h: 50 });
  });
  it("BOTTOMLEFT at scale 2", () => {
    expect(bboxToViewport({ l: 72, r: 172, t: 700, b: 650, coord_origin: "BOTTOMLEFT" }, H, 2)).toEqual({ x: 144, y: 184, w: 200, h: 100 });
  });
  it("TOPLEFT origin keeps y (scale 1)", () => {
    expect(bboxToViewport({ l: 72, r: 172, t: 92, b: 142, coord_origin: "TOPLEFT" }, H, 1)).toEqual({ x: 72, y: 92, w: 100, h: 50 });
  });
  it("TOPLEFT at scale 2", () => {
    expect(bboxToViewport({ l: 72, r: 172, t: 92, b: 142, coord_origin: "TOPLEFT" }, H, 2)).toEqual({ x: 144, y: 184, w: 200, h: 100 });
  });
  it("both origins agree for the same physical box", () => {
    const a = bboxToViewport({ l: 10, r: 60, t: 700, b: 600, coord_origin: "BOTTOMLEFT" }, H, 1.5);
    const b = bboxToViewport({ l: 10, r: 60, t: 92, b: 192, coord_origin: "TOPLEFT" }, H, 1.5);
    expect(a).toEqual(b);
  });
});

describe("reader store", () => {
  it("opens tabs, max 3: a 4th evicts the oldest", () => {
    ["a", "b", "c", "d"].forEach((id) => openDoc(id));
    const s = getReaderState();
    expect(s.tabs.map((t) => t.docId)).toEqual(["b", "c", "d"]);
    expect(s.active).toBe("d");
  });

  it("re-opening an open doc activates it without duplicating", () => {
    openDoc("a"); openDoc("b"); openDoc("a", { page: 4 });
    expect(getReaderState().tabs.map((t) => t.docId)).toEqual(["a", "b"]);
    expect(getReaderState().tabs[0].page).toBe(4);
    expect(getReaderState().active).toBe("a");
  });

  it("focus cycles split -> three-quarter -> full -> split and persists", () => {
    openDoc("a");
    expect(getReaderState().focus).toBe("split");
    cycleFocus(); expect(getReaderState().focus).toBe("three-quarter");
    cycleFocus(); expect(getReaderState().focus).toBe("full");
    expect(localStorage.getItem("roracle-reader-focus")).toBe("full");
    cycleFocus(); expect(getReaderState().focus).toBe("split");
  });

  it("Esc steps back: full -> three-quarter -> split -> closed; closing keeps the focus level", () => {
    openDoc("a"); cycleFocus(); cycleFocus();
    stepBack(); expect(getReaderState().focus).toBe("three-quarter");
    stepBack(); expect(getReaderState().focus).toBe("split");
    expect(getReaderState().tabs).toHaveLength(1);
    stepBack(); expect(getReaderState().tabs).toHaveLength(0);
    cycleFocus(); closeReader(); expect(getReaderState().focus).toBe("three-quarter");
  });

  it("restores the persisted focus level on reset", () => {
    localStorage.setItem("roracle-reader-focus", "three-quarter");
    resetReaderStore();
    expect(getReaderState().focus).toBe("three-quarter");
  });
});

async function renderApp(path: string) {
  const router = createAppRouter([path]);
  await router.load();
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
  return router;
}

describe("reader pane", () => {
  it("deep link /d/00000000-0000-4000-8000-000000000001#p=3&c=v:2 opens page 3 with the passage highlighted", async () => {
    server.use(http.get(`${API_BASE}/documents/:id/versions/:n/docling`, () =>
      HttpResponse.json({ pages: { "3": { size: { width: 612, height: 792 } } }, chunks: [{ chunk_id: "v:2", page_no: 3, bbox: { l: 60, r: 552, t: 700, b: 640, coord_origin: "BOTTOMLEFT" } }] })));
    await renderApp("/d/00000000-0000-4000-8000-000000000001#p=3&c=v:2");
    expect(await screen.findByRole("heading", { name: "Lower bounds on the spectral gap of random regular graphs" })).toBeInTheDocument();
    expect(getReaderState().tabs[0]).toMatchObject({ docId: "00000000-0000-4000-8000-000000000001", page: 3, chunkId: "v:2" });
    const hl = await screen.findByTestId("highlight-v:2");
    expect(hl.closest("[data-page]")).toHaveAttribute("data-page", "3");
    expect(hl.style.top).not.toBe("");
  });

  it("Summary and Details tabs show the AI summary and the metadata", async () => {
    await renderApp("/d/00000000-0000-4000-8000-000000000001");
    await screen.findByRole("heading", { name: /Lower bounds/ });
    await userEvent.click(screen.getByRole("tab", { name: /Summary/ }));
    expect(await screen.findByRole("heading", { name: "AI summary" })).toBeInTheDocument();
    expect(screen.getByText("No abstract was found in this document.")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("tab", { name: /Details/ }));
    expect(await screen.findByText("Visibility")).toBeInTheDocument();
    expect(screen.getAllByText(/^Version \d+ \(latest\)$/).some((e) => e.tagName === "DT")).toBe(true);
  });

  it("deep link with a version opens that version", async () => {
    await renderApp("/d/00000000-0000-4000-8000-000000000001/v/1");
    await screen.findByRole("heading", { name: /Lower bounds/ });
    expect(getReaderState().tabs[0].version).toBe(1);
  });

  it("focus toggle cycles the pane and Esc steps back", async () => {
    await renderApp("/library");
    await screen.findByText(/of 7 papers/);
    act(() => openDoc("00000000-0000-4000-8000-000000000002"));
    const app = document.getElementById("app")!;
    expect(app).toHaveAttribute("data-focus", "split");
    const toggle = await screen.findByRole("button", { name: /Focus mode/ });
    await userEvent.click(toggle);
    expect(app).toHaveAttribute("data-focus", "three-quarter");
    await userEvent.click(toggle);
    expect(app).toHaveAttribute("data-focus", "full");
    await userEvent.keyboard("{Escape}");
    expect(app).toHaveAttribute("data-focus", "three-quarter");
    await userEvent.keyboard("{Escape}");
    expect(app).toHaveAttribute("data-focus", "split");
    await userEvent.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByRole("complementary", { name: "Reader" })).toBeNull());
  });

  it("References tab links in-library references", async () => {
    await renderApp("/d/00000000-0000-4000-8000-000000000001");
    await screen.findByRole("heading", { name: /Lower bounds/ });
    await userEvent.click(screen.getByRole("tab", { name: /References/ }));
    const reader = screen.getByRole("complementary", { name: "Reader" });
    expect((await within(reader).findAllByText(/Not in the library/, undefined, { timeout: 2000 })).length).toBe(2);
    expect(within(reader).getByText("In the library")).toBeInTheDocument();
  });

  it("Cited by tab lists citing documents", async () => {
    await renderApp("/d/00000000-0000-4000-8000-000000000001");
    await screen.findByRole("heading", { name: /Lower bounds/ });
    await userEvent.click(screen.getByRole("tab", { name: /Cited by/ }));
    expect(await screen.findByText(/Cheeger-type inequalities/, { selector: ".rt" })).toBeInTheDocument();
  });
});
