import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider } from "@tanstack/react-router";
import { http, HttpResponse } from "msw";
import { beforeEach, describe, expect, it } from "vitest";
import { API_BASE } from "@/api/client";
import { server } from "@/mocks/server";
import { state } from "@/mocks/state";
import { createAppRouter } from "@/app/router";
import { getReaderState, resetReaderStore } from "@/features/reader/useReaderStore";
import { isTyping } from "@/lib/keys";
import { closePalette } from "./usePalette";

const D2 = "00000000-0000-4000-8000-000000000002";
async function renderApp(path = "/library") {
  const router = createAppRouter([path]);
  await router.load();
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><RouterProvider router={router} /></QueryClientProvider>);
  await screen.findByRole("navigation", { name: "Main" });
  return router;
}
const results = () => screen.getAllByRole("listitem").filter((li) => li.classList.contains("result"));

beforeEach(() => {
  resetReaderStore(); closePalette();
  localStorage.setItem("roracle-recent", JSON.stringify([{ id: D2, title: "Cheeger-type inequalities for weighted hypergraphs" }]));
});

describe("isTyping", () => {
  it("is true for inputs, textareas, selects and contenteditable", () => {
    for (const tag of ["input", "textarea", "select"]) expect(isTyping(document.createElement(tag))).toBe(true);
    const d = document.createElement("div"); d.contentEditable = "true"; Object.defineProperty(d, "isContentEditable", { value: true });
    expect(isTyping(d)).toBe(true);
    expect(isTyping(document.createElement("button"))).toBe(false);
    expect(isTyping(null)).toBe(false);
  });
});

describe("command palette", () => {
  it("Ctrl+K opens it, Esc closes it", async () => {
    await renderApp();
    await userEvent.keyboard("{Control>}k{/Control}");
    expect(await screen.findByRole("dialog")).toBeInTheDocument();
    expect(screen.getByPlaceholderText("Jump to a page or document")).toHaveFocus();
    await userEvent.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  });

  it("Meta+K opens it too, even while typing in the search box", async () => {
    await renderApp();
    await userEvent.click(screen.getByLabelText("Search documents"));
    await userEvent.keyboard("{Meta>}k{/Meta}");
    expect(await screen.findByRole("dialog")).toBeInTheDocument();
  });

  it("typing filters pages and recent documents", async () => {
    await renderApp();
    await userEvent.keyboard("{Control>}k{/Control}");
    const dlg = await screen.findByRole("dialog");
    expect(within(dlg).getByRole("option", { name: /Library/ })).toBeInTheDocument();
    expect(within(dlg).getByRole("option", { name: /Cheeger-type/ })).toBeInTheDocument();
    await userEvent.type(within(dlg).getByRole("combobox"), "upl");
    expect(within(dlg).getByRole("option", { name: /My uploads/ })).toBeInTheDocument();
    expect(within(dlg).queryByRole("option", { name: /Library/ })).toBeNull();
    expect(within(dlg).queryByRole("option", { name: /Cheeger-type/ })).toBeNull();
  });

  it("lists Settings and Notifications, and Admin only for admins", async () => {
    await renderApp();
    await userEvent.keyboard("{Control>}k{/Control}");
    const dlg = await screen.findByRole("dialog");
    expect(within(dlg).getByRole("option", { name: /Settings/ })).toBeInTheDocument();
    expect(within(dlg).getByRole("option", { name: /Notifications/ })).toBeInTheDocument();
    expect(within(dlg).queryByRole("option", { name: /Admin/ })).toBeNull();
  });

  it("shows the Admin page to admins", async () => {
    state.admin = true;
    try {
      await renderApp();
      await userEvent.keyboard("{Control>}k{/Control}");
      expect(await within(await screen.findByRole("dialog")).findByRole("option", { name: /Admin/ })).toBeInTheDocument();
    } finally { state.admin = false; }
  });

  it("Enter on a page navigates; Enter on a document opens it in the reader", async () => {
    const router = await renderApp();
    await userEvent.keyboard("{Control>}k{/Control}");
    await userEvent.type(await screen.findByRole("combobox"), "uploads{Enter}");
    await waitFor(() => expect(router.state.location.pathname).toBe("/uploads"));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    await userEvent.keyboard("{Control>}k{/Control}");
    await userEvent.type(await screen.findByRole("combobox"), "cheeger{ArrowDown}{Enter}");
    await waitFor(() => expect(getReaderState().active).toBe(D2));
  });

  it("finds library documents by searching the API", async () => {
    await renderApp();
    await userEvent.keyboard("{Control>}k{/Control}");
    await userEvent.type(await screen.findByRole("combobox"), "preconditioning");
    expect(await screen.findByRole("option", { name: /Preconditioning sparse Laplacian/ })).toBeInTheDocument();
  });
});

describe("palette search size", () => {
  it("asks the API for 5 documents", async () => {
    let size: string | null = null;
    server.use(http.get(`${API_BASE}/search`, ({ request }) => { const u = new URL(request.url); if (u.searchParams.get("q") === "spectral") size = u.searchParams.get("size"); return HttpResponse.json({ total: 0, page: 1, results: [], facets: { tags: [], year: [], file_type: [], org_unit: [], collection: [], author: [] } }); }));
    await renderApp("/uploads");
    await userEvent.keyboard("{Control>}k{/Control}");
    await userEvent.type(await screen.findByRole("combobox"), "spectral");
    await waitFor(() => expect(size).toBe("5"));
  });
});

describe("theme from the palette", () => {
  it("updates the top-bar theme button", async () => {
    await renderApp();
    expect(screen.getByRole("button", { name: "Switch theme" })).toHaveAttribute("title", "Theme: system");
    await userEvent.keyboard("{Control>}k{/Control}");
    await userEvent.type(await screen.findByRole("combobox"), "theme{Enter}");
    await waitFor(() => expect(screen.getByRole("button", { name: "Switch theme" })).toHaveAttribute("title", "Theme: light"));
  });
});

describe("keyboard", () => {
  it("/ focuses the search box", async () => {
    await renderApp();
    await userEvent.keyboard("/");
    expect(screen.getByLabelText("Search documents")).toHaveFocus();
  });

  it("keys are ignored while typing in inputs", async () => {
    await renderApp();
    await screen.findByText(/of 7 documents/);
    const box = screen.getByLabelText("Search documents");
    await userEvent.click(box);
    await userEvent.keyboard("j/k");
    expect(box).toHaveValue("j/k");
    expect(box).toHaveFocus();
  });

  it("j/k move through results and Enter opens the focused one", async () => {
    await renderApp();
    await screen.findByText(/of 7 documents/);
    await userEvent.keyboard("j");
    expect(results()[0]).toHaveFocus();
    await userEvent.keyboard("j");
    expect(results()[1]).toHaveFocus();
    await userEvent.keyboard("k");
    expect(results()[0]).toHaveFocus();
    await userEvent.keyboard("k");
    expect(results()[0]).toHaveFocus();
    await userEvent.keyboard("{Enter}");
    expect(getReaderState().active).toBe(results()[0].getAttribute("data-doc"));
  });
});
