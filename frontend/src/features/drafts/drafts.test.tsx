import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider } from "@tanstack/react-router";
import { http, HttpResponse } from "msw";
import { describe, expect, it, vi } from "vitest";
import { API_BASE } from "@/api/client";
import { createAppRouter } from "@/app/router";
import { D1, D2, D3 } from "@/mocks/drafts";
import { server } from "@/mocks/server";
import { readAi, withAi } from "./frontMatter";

vi.mock("@/features/reader/pdfjs", () => ({
  loadPdf: async () => ({ numPages: 2, getPage: async () => ({ getViewport: () => ({ width: 612, height: 792, scale: 1 }), render: () => ({ promise: Promise.resolve() }) }) }),
}));

const url = (p: string) => `${API_BASE}${p}`;
async function open(path: string) {
  const router = createAppRouter([path]);
  await router.load();
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={qc}><RouterProvider router={router} /></QueryClientProvider>);
  return qc;
}
const editor = () => screen.findByLabelText("Draft content") as Promise<HTMLTextAreaElement>;
const tab = (name: string) => userEvent.click(screen.getByRole("tab", { name }));

describe("front matter", () => {
  it("replaces ai_assistance, keeps other keys, and creates front matter when missing", () => {
    const ai = { used: true, tools: ["gpt"], description: "drafted \"intro\"" };
    const out = withAi("---\ndoc_type: paper\nai_assistance:\n  used: false\n---\n# T", ai);
    expect(out).toBe(`---\ndoc_type: paper\nai_assistance: ${JSON.stringify(ai)}\n---\n# T`);
    expect(readAi(out)).toEqual(ai);
    expect(withAi("# T", ai).startsWith("---\nai_assistance:")).toBe(true);
    expect(readAi("# T")).toBeNull();
  });
});

describe("front matter (backend parity)", () => {
  it("does not stack a second block for ---<space> delimiters or an empty block, and escapes C1 controls", () => {
    const ai = { used: true, tools: [], description: "a\u0085b" };
    const once = withAi("--- \ndoc_type: x\n---  \nbody", ai);
    expect(once.match(/^---/gm)).toHaveLength(2);
    expect(withAi("---\n---\nbody", ai).match(/^---/gm)).toHaveLength(2);
    expect(once).toContain("\\u0085");
    expect(readAi(once)?.description).toBe("a\u0085b");
  });
});

describe("drafts list", () => {
  it("lists drafts with status, and creates a draft with a profile", async () => {
    const user = userEvent.setup();
    await open("/drafts");
    expect(await screen.findByText("Spectral gaps of random hypergraphs")).toBeInTheDocument();
    expect(screen.getByText("In review")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /New draft/ }));
    await user.type(screen.getByLabelText("Title"), "Brand new paper");
    await user.selectOptions(screen.getByLabelText("Writing profile"), "dissertation");
    await user.click(screen.getByRole("button", { name: "Create draft" }));
    expect(await screen.findByRole("heading", { name: "Brand new paper" })).toBeInTheDocument();
    expect(screen.getByText(/dissertation/)).toBeInTheDocument();
  });

  it("deletes through the row menu with confirmation", async () => {
    const user = userEvent.setup();
    await open("/drafts");
    await screen.findByText("Trace methods survey");
    await user.click(screen.getByRole("button", { name: "Actions for Spectral gaps of random hypergraphs" }));
    await user.click(screen.getByRole("menuitem", { name: "Delete draft" }));
    await user.click(screen.getByRole("button", { name: "Delete" }));
    await waitFor(() => expect(screen.queryByText("Spectral gaps of random hypergraphs")).not.toBeInTheDocument());
  });

  it("shows the empty state", async () => {
    server.use(http.get(url("/drafts"), () => HttpResponse.json({ items: [] })));
    await open("/drafts");
    expect(await screen.findByText(/No drafts yet/)).toBeInTheDocument();
  });
});

describe("draft editor", () => {
  it("malformed ids are not found", async () => {
    await open("/drafts/not-an-id");
    expect(await screen.findByText(/not found|doesn't exist|can't find/i)).toBeInTheDocument();
  });

  it("an unknown but well-formed id is not found either", async () => {
    await open("/drafts/00000000-0000-4000-8000-0000000000ff");
    expect(await screen.findByText(/not found|doesn't exist|can't find|couldn't load/i)).toBeInTheDocument();
  });

  it("saves edits (dirty state) and the preview never renders html, scripts or images", async () => {
    const user = userEvent.setup();
    await open(`/drafts/${D1}`);
    const ta = await editor();
    const save = screen.getByRole("button", { name: "Save" });
    expect(save).toBeDisabled();
    await user.clear(ta);
    await user.click(ta);
    await user.paste("# Hello\n\n<script>window.pwned=1</script><b>raw</b>\n\n![x](http://evil.test/a.png) [bad](javascript:alert(1)) **ok**");
    expect(screen.getByText("Unsaved changes")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Preview" }));
    const prev = await screen.findByLabelText("Preview");
    await waitFor(() => expect(within(prev).getByRole("heading", { name: "Hello" })).toBeInTheDocument());
    expect(prev.querySelector("script, img, b")).toBeNull();
    expect(prev.querySelector("a[href^='javascript']")).toBeNull();
    expect(within(prev).getByText("ok").tagName).toBe("STRONG");
    await user.click(save);
    await waitFor(() => expect(screen.getByText(/version 3/)).toBeInTheDocument());
    expect(screen.queryByText("Unsaved changes")).not.toBeInTheDocument();
  });

  it("a 409 draft_changed conflict offers the latest version", async () => {
    const user = userEvent.setup();
    await open(`/drafts/${D1}`);
    const ta = await editor();
    await user.type(ta, "x");
    server.use(http.put(url(`/drafts/${D1}/content`), () => HttpResponse.json({ detail: { code: "draft_changed", version: 9 } }, { status: 409 })));
    await user.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByText(/Someone saved a newer version/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /Load the latest version/ }));
    await waitFor(() => expect(ta.value).toBe("# Intro\n\nSecond pass with a claim [@spectral-gap-v3]."));
  });

  it("an in-review draft is read-only and shows the lifecycle", async () => {
    await open(`/drafts/${D2}`);
    expect(await editor()).toHaveAttribute("readonly");
    expect(screen.getByText(/in review and can't be edited/)).toBeInTheDocument();
    expect(screen.getAllByText("In review").length).toBeGreaterThan(0);
  });

  it("shows the reviewer note on a sent-back draft", async () => {
    await open(`/drafts/${D3}`);
    expect(await screen.findByText(/Reviewer note: Tighten the aims/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Submit/ })).not.toBeInTheDocument(); // co-author, not owner
  });
});

describe("stale base", () => {
  it("a remote save while editing keeps the base, so Save 409s instead of overwriting", async () => {
    const user = userEvent.setup();
    let sent: number | undefined;
    server.use(http.put(url(`/drafts/${D1}/content`), async ({ request }) => { sent = ((await request.json()) as { base_version: number }).base_version; return HttpResponse.json({ detail: { code: "draft_changed", version: 3 } }, { status: 409 }); }));
    const qc = await open(`/drafts/${D1}`);
    const ta = await editor();
    await user.type(ta, "x");
    // a co-author's save lands and the draft refetches (window focus / invalidation)
    const { state } = await import("@/mocks/state");
    state.drafts!.find((d) => d.id === D1)!.vers.push({ version: 3, author: { id: "u2", username: "ana" }, created_at: "2026-11-15T00:00:00Z", content: "remote" });
    state.drafts!.find((d) => d.id === D1)!.latest_version = 3;
    void qc.invalidateQueries({ queryKey: ["draft", D1], exact: true });
    await waitFor(() => expect(screen.getByText(/version 3/)).toBeInTheDocument());
    expect(ta.value.endsWith("x")).toBe(true);
    await user.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByText(/Someone saved a newer version/)).toBeInTheDocument();
    expect(sent).toBe(2);
  });

  it("a clean editor adopts remote changes, and the PDF result survives a tab switch", async () => {
    const user = userEvent.setup();
    await open(`/drafts/${D1}`);
    const ta = await editor();
    await tab("PDF");
    await user.click(screen.getByRole("button", { name: "Build PDF" }));
    await screen.findByRole("link", { name: /Download PDF/ });
    await tab("Versions");
    await tab("PDF");
    expect(screen.getByRole("link", { name: /Download PDF/ })).toBeInTheDocument();
    expect(ta.value).toContain("Second pass");
  });
});

describe("versions", () => {
  it("views and restores an older version after confirmation", async () => {
    const user = userEvent.setup();
    await open(`/drafts/${D1}`);
    const ta = await editor();
    await tab("Versions");
    await user.click(screen.getByRole("button", { name: "Version 1" }));
    expect(await screen.findByText("First pass.")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Restore this version" }));
    await user.click(screen.getByRole("button", { name: "Restore" }));
    await waitFor(() => expect(ta.value).toBe("# Intro\n\nFirst pass."));
    expect(await screen.findByText(/version 3/)).toBeInTheDocument();
  });
});

describe("checks", () => {
  it("validates, checks citations and acknowledges an unsupported one", async () => {
    const user = userEvent.setup();
    await open(`/drafts/${D1}`);
    await editor();
    await user.click(screen.getByRole("button", { name: "Run validation" }));
    const v = await screen.findByLabelText("Validation results");
    expect(within(v).getByText(/ai_disclosure: Front matter needs ai_assistance/)).toBeInTheDocument();
    expect(within(v).getByText(/Warning abstract_length/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Check citations" }));
    const c = await screen.findByLabelText("Citation results");
    expect(within(c).getByText("Needs attention")).toBeInTheDocument();
    const buttons = within(c).getAllByRole("button", { name: "Acknowledge…" });
    expect(buttons).toHaveLength(1); // only the resolved-but-unsupported item is ackable; the unresolved one is not
    await user.click(buttons[0]);
    await user.type(within(c).getByLabelText("Reason for weak-claim"), "Authors confirmed");
    await user.click(within(c).getByRole("button", { name: "Acknowledge" }));
    expect(await within(c).findByText(/acknowledged/)).toBeInTheDocument();
    await user.click(within(c).getByRole("button", { name: "Remove acknowledgement" }));
    await waitFor(() => expect(within(c).queryByText(/ acknowledged/)).not.toBeInTheDocument());
  });

  it("marks results stale after a save", async () => {
    const user = userEvent.setup();
    await open(`/drafts/${D1}`);
    const ta = await editor();
    await user.click(screen.getByRole("button", { name: "Run validation" }));
    await screen.findByLabelText("Validation results");
    await user.type(ta, "!");
    await user.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByText(/This result is for version 2; the latest is 3/)).toBeInTheDocument();
  });

  it("co-authors can't run checks as non-author viewers see a note (admin reader)", async () => {
    server.use(http.get(url(`/drafts/${D3}`), () => HttpResponse.json({
      id: D3, title: "T", profile: null, status: "draft", owner: { id: "x", username: "x" }, coauthors: { users: [], groups: [] }, latest_version: 1,
      updated_at: "2026-11-14T10:00:00Z", review_note: null, content: "# a", versions: [],
    })));
    await open(`/drafts/${D3}`);
    expect(await screen.findByText(/Only the owner and co-authors can run checks/)).toBeInTheDocument();
  });
});

describe("build pdf", () => {
  it("builds, previews and offers a download; latex is refused inline", async () => {
    const user = userEvent.setup();
    const revoke = vi.spyOn(URL, "revokeObjectURL");
    const create = vi.spyOn(URL, "createObjectURL").mockReturnValue("blob:draft");
    await open(`/drafts/${D1}`);
    await editor();
    await tab("PDF");
    await user.click(screen.getByRole("button", { name: "Build PDF" }));
    const dl = await screen.findByRole("link", { name: /Download PDF/ });
    expect(dl).toHaveAttribute("download", "spectral-gaps-of-random-hypergraphs.pdf");
    expect(dl).toHaveAttribute("href", "blob:draft");
    expect(create).toHaveBeenCalled();
    await waitFor(() => expect(document.querySelectorAll(".pdfpage")).toHaveLength(2));
    server.use(http.post(url(`/drafts/${D1}/build`), () => HttpResponse.json({ detail: "LaTeX builds are not enabled" }, { status: 501 })));
    await user.click(screen.getByRole("button", { name: "Build PDF" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("LaTeX builds are not enabled");
    expect(revoke).toHaveBeenCalledWith("blob:draft");
  });
});

describe("submit", () => {
  it("writes the disclosure into the front matter, then surfaces 409 codes inline", async () => {
    const user = userEvent.setup();
    let saved = "";
    server.use(http.put(url(`/drafts/${D1}/content`), async ({ request }) => { saved = ((await request.json()) as { content: string }).content; return HttpResponse.json({ version: 3, created_at: "2026-11-15T00:00:00Z" }); }));
    await open(`/drafts/${D1}`);
    await editor();
    await user.click(screen.getByRole("button", { name: "Submit…" }));
    const dlg = await screen.findByRole("dialog");
    await user.click(within(dlg).getByLabelText(/AI tools were used/));
    expect(within(dlg).getByRole("button", { name: "Submit" })).toBeDisabled(); // description required when used
    await user.type(within(dlg).getByLabelText("Tools (comma separated)"), "claude, gpt");
    await user.type(within(dlg).getByLabelText("How were they used?"), "Language polish");
    await user.click(within(dlg).getByRole("button", { name: "Submit" }));
    expect(await within(dlg).findByRole("alert")).toHaveTextContent(/validation and the citation check/); // checks_required from the mock
    expect(readAi(saved)).toEqual({ used: true, tools: ["claude", "gpt"], description: "Language polish" });

    server.use(http.post(url(`/drafts/${D1}/submit`), () => HttpResponse.json({ detail: { code: "ai_disclosure_required" } }, { status: 409 })));
    await user.click(within(dlg).getByRole("button", { name: "Submit" }));
    await waitFor(() => expect(within(dlg).getByRole("alert")).toHaveTextContent(/AI-assistance disclosure/));

    server.use(http.post(url(`/drafts/${D1}/submit`), () => HttpResponse.json({ status: "in_review", version: 3 })));
    await user.click(within(dlg).getByRole("button", { name: "Submit" }));
    expect(await within(dlg).findByText(/Submitted for review/)).toBeInTheDocument();
  });

  it("offers the visibility picker with sharing fields", async () => {
    const user = userEvent.setup();
    await open(`/drafts/${D1}`);
    await editor();
    await user.click(screen.getByRole("button", { name: "Submit…" }));
    const dlg = await screen.findByRole("dialog");
    await user.selectOptions(within(dlg).getByLabelText("Visible to"), "shared");
    expect(within(dlg).getByLabelText(/User ids/)).toBeInTheDocument();
  });
});

describe("co-authors", () => {
  it("adds and removes a co-author by id; unknown ids show the server error", async () => {
    const user = userEvent.setup();
    await open(`/drafts/${D1}`);
    await editor();
    await tab("Co-authors");
    await user.type(screen.getByLabelText("Co-author id"), "nobody");
    await user.click(screen.getByRole("button", { name: "Add" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("unknown user");
    const input = screen.getByLabelText("Co-author id");
    await user.clear(input);
    await user.type(input, "11111111-1111-4111-8111-111111111111");
    await user.click(screen.getByRole("button", { name: "Add" }));
    expect(await screen.findByText("11111111-1111-4111-8111-111111111111")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /Remove 11111111/ }));
    await waitFor(() => expect(screen.queryByText("11111111-1111-4111-8111-111111111111")).not.toBeInTheDocument());
  });
});
