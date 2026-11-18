import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider } from "@tanstack/react-router";
import { delay, http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";
import { API_BASE } from "@/api/client";
import { createAppRouter } from "@/app/router";
import { D1 } from "@/mocks/drafts";
import { server } from "@/mocks/server";
import { citedDocIds, docCitation, withCiteTitles } from "./cite";

const A = "00000000-0000-4000-8000-000000000001";
const B = "00000000-0000-4000-8000-000000000002";
const TITLE = "Lower bounds on the spectral gap of random regular graphs";

async function open() {
  const router = createAppRouter([`/drafts/${D1}`]);
  await router.load();
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><RouterProvider router={router} /></QueryClientProvider>);
  return (await screen.findByLabelText("Draft content")) as HTMLTextAreaElement;
}

describe("citation syntax helpers", () => {
  it("matches the backend syntax and finds cited papers", () => {
    expect(docCitation(A)).toBe(`[@doc:${A}]`);
    expect(citedDocIds(`x [@doc:${A}; @doc:${B}] y [@doc:${A}] @doc:ffffffff-0000-4000-8000-000000000003 [@psg:${B}:4]`)).toEqual([A, B]);
  });
  it("shows titles for known papers and keeps the rest as written", () => {
    const out = withCiteTitles(`See [@doc:${A}; @psg:${B}:2] and [@doc:${B}].`, { [A]: "Graphs [and] *more*" });
    expect(out.startsWith("See \\[*Graphs \\[and\\] \\*more\\**; @psg:")).toBe(true);
    expect(out.endsWith(`and \\[@doc:${B.replace(/-/g, "\\-")}\\].`)).toBe(true);
    expect(withCiteTitles("no citations [here]", {})).toBe("no citations [here]");
  });
});

describe("Insert citation", () => {
  it("searches the visible papers and inserts [@doc:<id>] at the cursor; the preview shows the title", async () => {
    const user = userEvent.setup();
    const ta = await open();
    await user.clear(ta);
    await user.type(ta, "Before after");
    ta.setSelectionRange(7, 7);
    await user.click(screen.getByRole("button", { name: "Insert citation" }));
    const dialog = await screen.findByRole("dialog", { name: "Insert citation" });
    await user.type(within(dialog).getByLabelText("Paper title"), "spectral gap");
    await user.click(await within(dialog).findByRole("button", { name: new RegExp(TITLE) }));
    await waitFor(() => expect(ta.value).toBe(`Before [@doc:${A}]after`));
    expect(screen.queryByRole("dialog")).toBeNull();
    await user.click(screen.getByRole("button", { name: "Preview" }));
    const prev = await screen.findByLabelText("Preview");
    await waitFor(() => expect(prev).toHaveTextContent(`Before [${TITLE}]after`));
  });

  it("typing [@ opens the search and the pick replaces those two characters", async () => {
    const user = userEvent.setup();
    const ta = await open();
    await user.clear(ta);
    await user.type(ta, "Claim [[@");
    const dialog = await screen.findByRole("dialog", { name: "Insert citation" });
    await user.type(within(dialog).getByLabelText("Paper title"), "spectral gap");
    await within(dialog).findByRole("button", { name: new RegExp(TITLE) });
    await user.keyboard("{Enter}");
    await waitFor(() => expect(ta.value).toBe(`Claim [@doc:${A}]`));
  });

  it("Escape keeps what was typed", async () => {
    const user = userEvent.setup();
    const ta = await open();
    await user.clear(ta);
    await user.type(ta, "x [[@");
    await screen.findByRole("dialog", { name: "Insert citation" });
    await user.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(ta.value).toBe("x [@");
  });
});

describe("slow checks", () => {
  it("say what is happening while the language model works", async () => {
    const user = userEvent.setup();
    server.use(
      http.post(`${API_BASE}/drafts/:id/validate`, async () => { await delay("infinite"); return HttpResponse.json({}); }),
      http.post(`${API_BASE}/drafts/:id/check-citations`, async () => { await delay("infinite"); return HttpResponse.json({}); }),
    );
    await open();
    await user.click(screen.getByRole("button", { name: "Run validation" }));
    expect(await screen.findByText(/style review asks the language model/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Check citations" }));
    expect(await screen.findByText(/Asking the language model about each citation/)).toBeInTheDocument();
    expect(screen.getByLabelText("Draft content")).not.toHaveAttribute("readonly");
  });
});
