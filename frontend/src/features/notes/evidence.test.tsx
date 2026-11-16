import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider } from "@tanstack/react-router";
import { http, HttpResponse } from "msw";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { API_BASE, setAuthHooks } from "@/api/client";
import type { CommunityNote, Evidence } from "@/api/notes";
import { runSearch } from "@/mocks/search";
import { createAppRouter } from "@/app/router";
import { DOCS } from "@/mocks/docs";
import { server } from "@/mocks/server";
import { getReaderState, resetReaderStore } from "@/features/reader/useReaderStore";
import { notesUi } from "./api";
import { buildMeta, EMPTY_FIELDS, type Fields } from "./EvidenceForm";
import { crateView, safeFilename } from "./evidenceApi";

vi.mock("@/features/reader/pdfjs", () => ({
  loadPdf: async () => ({
    numPages: 3,
    getPage: async () => ({
      getViewport: ({ scale }: { scale: number }) => ({ width: 612 * scale, height: 792 * scale, scale }),
      render: () => ({ promise: Promise.resolve() }),
      getTextContent: async () => ({ items: [] }),
    }),
  }),
}));

const D1 = DOCS[0].id;
const H64 = "a".repeat(64);

async function renderApp(path: string) {
  const router = createAppRouter([path]);
  await router.load();
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><RouterProvider router={router} /></QueryClientProvider>);
  return router;
}
async function finding() {
  await renderApp(`/d/${D1}`);
  await screen.findByRole("heading", { level: 2 });
  const reader = within(screen.getByRole("complementary", { name: "Reader" }));
  await userEvent.click(reader.getByRole("tab", { name: /Notes/ }));
  const card = await screen.findByRole("article", { name: /Finding by J. Ruiz/ });
  await within(card).findByRole("listitem", { name: "Evidence inconclusive" });
  return within(card);
}

const seen: Request[] = [];
beforeEach(() => {
  resetReaderStore(); notesUi.reset(); seen.length = 0;
  server.events.on("request:start", ({ request }) => { seen.push(request.clone()); });
});
afterEach(() => { server.events.removeAllListeners(); vi.unstubAllGlobals(); vi.restoreAllMocks(); setAuthHooks(null); });

const BIG_ROWS = Array.from({ length: 100 }, (_, i) => ({ name: `n${i}`.padEnd(400, "n"), uri: "https://x/".padEnd(1900, "u"), sha256: "" }));
const VALID: Fields = {
  ...EMPTY_FIELDS, repo: "https://git.lab/x/y", commit: "4e1c9a2", image: `sha256:${H64}`, client: "claude-code", model: "opus",
  started: "2026-10-01T08:00", ended: "2026-10-01T09:00", seed: "7", parameters: '{"k": 3}',
  inputs: [{ name: "in.csv", uri: "", sha256: "" }, { name: "", uri: "", sha256: "" }], source: "uri", uri: "s3://b/run.tar", uriSha: H64,
};

describe("buildMeta", () => {
  it("builds the multipart meta shape", () => {
    const r = buildMeta(VALID);
    expect(r).toEqual({ meta: {
      stance: "supports", code: { repo: "https://git.lab/x/y", commit: "4e1c9a2" }, environment: { image_digest: `sha256:${H64}` },
      inputs: [{ name: "in.csv" }], outputs: [], parameters: { k: 3 }, seed: 7, agent: { client: "claude-code", model: "opus" },
      started_at: new Date("2026-10-01T08:00").toISOString(), ended_at: new Date("2026-10-01T09:00").toISOString(),
      description: "", uri: "s3://b/run.tar", uri_sha256: H64,
    } });
  });
  it.each([
    [{ commit: "xyz" }, /Commit/],
    [{ image: "sha256:abc" }, /Image digest/],
    [{ repo: "javascript:alert(1)" }, /Repository/],
    [{ parameters: "[1]" }, /JSON object/],
    [{ parameters: '{"a": {"@id": 1}}' }, /@/],
    [{ parameters: "{" }, /valid JSON/],
    [{ ended: "2026-10-01T07:00" }, /end before/],
    [{ uriSha: "" }, /SHA-256/],
    [{ parameters: JSON.stringify({ blob: "é".repeat(33_000) }) }, /64 KB/],
    [{ inputs: BIG_ROWS, outputs: BIG_ROWS }, /256 KB/],
  ] as [Partial<Fields>, RegExp][])("rejects %o", (patch, msg) => {
    const r = buildMeta({ ...VALID, ...patch });
    expect("error" in r && r.error).toMatch(msg);
  });
  it("keeps a non-numeric seed as text and drops the link for file uploads", () => {
    const r = buildMeta({ ...VALID, seed: "abc", source: "file" });
    expect("meta" in r && [r.meta.seed, r.meta.uri, r.meta.uri_sha256]).toEqual(["abc", null, null]);
  });
});

describe("crate and filenames", () => {
  it("reads the crate defensively", () => {
    expect(crateView(null).repo).toBe("");
    expect(crateView({ "@graph": [{ "@id": "#code", codeRepository: { evil: 1 }, version: "abc1234" }] })).toMatchObject({ repo: "", commit: "abc1234" });
  });
  it("sanitises download names", () => {
    expect(safeFilename("../../etc/\"pass\u0000wd")).toBe("passwd");
    expect(safeFilename("..")).toBe("evidence");
  });
});

describe("evidence panel", () => {
  it("offers download only for clean or unscanned files and shows the scan status otherwise", async () => {
    const c = await finding();
    const items = c.getAllByRole("listitem").filter((li) => li.classList.contains("ev"));
    expect(items).toHaveLength(3);
    const [clean, infected, uri] = items.map((li) => within(li));
    expect(clean.getByRole("button", { name: "Download" })).toBeInTheDocument();
    expect(infected.queryByRole("button", { name: "Download" })).toBeNull();
    expect(infected.getByText("Blocked: infected")).toBeInTheDocument();
    expect(uri.queryByRole("button", { name: "Download" })).toBeNull();
    expect(uri.getByText("s3://lab-runs/sweep-2026-10.tar")).toBeInTheDocument();
    // crate as plain text; parameters collapsed
    expect(clean.getByText("https://git.lab/spectral/sweeps @ 4e1c9a2")).toBeInTheDocument();
    expect(clean.getByText("Parameters").closest("details")).not.toHaveAttribute("open");
    // outputs capped by OverflowList
    expect(clean.getByRole("button", { name: /More outputs/ })).toBeInTheDocument();
    // delete only for the creator (the link-only one is the mock user's)
    expect(clean.queryByRole("button", { name: "Delete evidence" })).toBeNull();
    expect(uri.getByRole("button", { name: "Delete evidence" })).toBeInTheDocument();
  });

  it("downloads via a blob and a temporary <a download>, never navigating", async () => {
    const created = vi.spyOn(URL, "createObjectURL").mockReturnValue("blob:mock");
    vi.spyOn(URL, "revokeObjectURL").mockImplementation(() => {});
    const clicked: HTMLAnchorElement[] = [];
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (this: HTMLAnchorElement) { clicked.push(this); });
    const open = vi.spyOn(window, "open").mockImplementation(() => null);
    const c = await finding();
    await userEvent.click(c.getByRole("button", { name: "Download" }));
    await waitFor(() => expect(clicked).toHaveLength(1));
    expect(clicked[0].download).toBe("gap_sweep.tar");
    expect(clicked[0].getAttribute("href")).toBe("blob:mock");
    expect(clicked[0].isConnected).toBe(false);
    expect((created.mock.calls[0] as unknown as [Blob])[0].type).toBe("application/octet-stream");
    expect(seen.some((r) => r.method === "GET" && r.url.endsWith("/evidence/ev-clean/file"))).toBe(true);
    expect(open).not.toHaveBeenCalled();
  });

  it("polls while a scan is pending and offers Download once it is clean", async () => {
    let calls = 0;
    server.use(http.get(`${API_BASE}/annotations/n-finding/evidence`, () => {
      const status = ++calls === 1 ? "pending" : "clean";
      const ev: Evidence = { id: "ev-p", annotation_id: "n-finding", stance: "inconclusive", crate: {}, uri: null, uri_sha256: null,
        file: { filename: "a.tar", size: 3, sha256: H64, scan_status: status }, reproductions: { reproduced: 0, failed: 0, mine: null }, created_by: "u-x", created_at: null };
      return HttpResponse.json({ items: [ev] });
    }));
    const c = await finding();
    const item = within(c.getByRole("listitem", { name: "Evidence inconclusive" }));
    expect(item.getByText("Scanning for viruses…")).toBeInTheDocument();
    expect(item.queryByRole("button", { name: "Download" })).toBeNull();
    await waitFor(() => expect(item.getByRole("button", { name: "Download" })).toBeInTheDocument(), { timeout: 8000 });
  }, 12000);

  it("toggles my reproduction outcome with PUT", async () => {
    const c = await finding();
    const clean = within(c.getAllByRole("group", { name: "Your reproduction" })[0]);
    await userEvent.type(clean.getByLabelText("Reproduction note (optional)"), "same gap");
    await userEvent.click(clean.getByRole("button", { name: "Reproduced" }));
    await waitFor(() => expect(clean.getByRole("button", { name: "Reproduced" })).toHaveAttribute("aria-pressed", "true"));
    const put = seen.find((r) => r.method === "PUT" && r.url.endsWith("/evidence/ev-clean/reproduction"))!;
    expect(await put.json()).toEqual({ outcome: "reproduced", note: "same gap" });
    expect(c.getByText("Reproduced ×3")).toBeInTheDocument();
    await userEvent.click(clean.getByRole("button", { name: "Reproduced" }));
    await waitFor(() => expect(clean.getByRole("button", { name: "Reproduced" })).toHaveAttribute("aria-pressed", "false"));
  });
});

/** Minimal XHR double: records the request, lets the test drive progress, load and abort. */
class FakeXhr {
  static last: FakeXhr | null = null;
  static all: FakeXhr[] = [];
  upload: { onprogress: ((e: { lengthComputable: boolean; loaded: number; total: number }) => void) | null } = { onprogress: null };
  onload: (() => void) | null = null; onerror: (() => void) | null = null; onabort: (() => void) | null = null;
  headers: Record<string, string> = {}; method = ""; url = ""; body: FormData | null = null;
  status = 0; statusText = ""; responseText = "";
  aborted = false;
  open(m: string, u: string) { this.method = m; this.url = u; }
  setRequestHeader(k: string, v: string) { this.headers[k] = v; }
  send(b: FormData) { this.body = b; FakeXhr.last = this; FakeXhr.all.push(this); }
  abort() { this.aborted = true; this.onabort?.(); }
}

async function openForm() {
  const c = await finding();
  await userEvent.click(c.getByRole("button", { name: "Add evidence" }));
  const d = within(await screen.findByRole("dialog"));
  for (const [label, v] of [["Repository", "https://git.lab/x/y"], ["Commit", "4e1c9a2"], ["Image digest", `sha256:${H64}`], ["Agent client", "cli"], ["Model", "m"]]) {
    fireEvent.change(d.getByLabelText(label), { target: { value: v } });
  }
  fireEvent.change(d.getByLabelText("Started"), { target: { value: "2026-10-01T08:00" } });
  fireEvent.change(d.getByLabelText("Ended"), { target: { value: "2026-10-01T09:00" } });
  return d;
}

describe("evidence upload", () => {
  beforeEach(() => { FakeXhr.last = null; FakeXhr.all = []; vi.stubGlobal("XMLHttpRequest", FakeXhr); });

  it("sends multipart meta + file with progress, and cancel aborts", async () => {
    const d = await openForm();
    const file = new File(["hello"], "run.tar");
    fireEvent.change(d.getByLabelText(/File \(up to 1 GiB\)/), { target: { files: [file] } });
    await userEvent.click(d.getByRole("button", { name: "Add evidence" }));
    await waitFor(() => expect(FakeXhr.last).not.toBeNull());
    const x = FakeXhr.last!;
    expect([x.method, x.url]).toEqual(["POST", `${API_BASE}/annotations/n-finding/evidence`]);
    const meta = JSON.parse(String(x.body!.get("meta")));
    expect(meta).toMatchObject({ stance: "supports", code: { repo: "https://git.lab/x/y", commit: "4e1c9a2" }, agent: { client: "cli", model: "m" }, uri: null, uri_sha256: null, seed: null, parameters: {} });
    expect((x.body!.get("file") as File).name).toBe("run.tar");
    x.upload.onprogress!({ lengthComputable: true, loaded: 50, total: 100 });
    await waitFor(() => expect(d.getByRole("progressbar", { name: "Upload progress" })).toHaveAttribute("aria-valuenow", "50"));
    await userEvent.click(d.getByRole("button", { name: "Cancel upload" }));
    expect(x.aborted).toBe(true);
    await waitFor(() => expect(d.queryByRole("progressbar")).toBeNull());
    expect(screen.getByRole("dialog")).toBeInTheDocument(); // stays open to retry
  });

  it("closes and refreshes on success", async () => {
    const d = await openForm();
    await userEvent.click(d.getByLabelText("Metadata only"));
    await userEvent.click(d.getByRole("button", { name: "Add evidence" }));
    await waitFor(() => expect(FakeXhr.last).not.toBeNull());
    const x = FakeXhr.last!;
    expect(x.body!.get("file")).toBeNull();
    Object.assign(x, { status: 201, responseText: JSON.stringify({ id: "ev-new" }) });
    x.onload!();
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  });

  it("renews once on 401 and retries with the new token", async () => {
    const tokens = ["old", "new"];
    setAuthHooks({ getToken: async () => tokens[0], renew: vi.fn(async () => { tokens.shift(); return true; }), signIn: vi.fn() });
    const d = await openForm();
    await userEvent.click(d.getByLabelText("Metadata only"));
    await userEvent.click(d.getByRole("button", { name: "Add evidence" }));
    await waitFor(() => expect(FakeXhr.all).toHaveLength(1));
    expect(FakeXhr.all[0].headers.Authorization).toBe("Bearer old");
    FakeXhr.all[0].status = 401; FakeXhr.all[0].onload!();
    await waitFor(() => expect(FakeXhr.all).toHaveLength(2));
    const x = FakeXhr.all[1];
    expect(x.headers.Authorization).toBe("Bearer new");
    expect(String(x.body!.get("meta"))).toBe(String(FakeXhr.all[0].body!.get("meta")));
    Object.assign(x, { status: 201, responseText: "{}" }); x.onload!();
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  });

  it("goes to sign-in when the renew fails", async () => {
    const signIn = vi.fn();
    setAuthHooks({ getToken: async () => "old", renew: async () => false, signIn });
    const d = await openForm();
    await userEvent.click(d.getByLabelText("Metadata only"));
    await userEvent.click(d.getByRole("button", { name: "Add evidence" }));
    await waitFor(() => expect(FakeXhr.all).toHaveLength(1));
    FakeXhr.all[0].status = 401; FakeXhr.all[0].onload!();
    await waitFor(() => expect(signIn).toHaveBeenCalledOnce());
    expect(FakeXhr.all).toHaveLength(1);
  });

  it("closing the dialog aborts a running upload", async () => {
    const d = await openForm();
    await userEvent.click(d.getByLabelText("Metadata only"));
    await userEvent.click(d.getByRole("button", { name: "Add evidence" }));
    await waitFor(() => expect(FakeXhr.last).not.toBeNull());
    await userEvent.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(FakeXhr.last!.aborted).toBe(true);
  });

  it("rejects empty files before sending", async () => {
    const d = await openForm();
    fireEvent.change(d.getByLabelText(/File \(up to 1 GiB\)/), { target: { files: [new File([], "empty.bin")] } });
    expect(d.getByRole("alert")).toHaveTextContent("empty");
    await userEvent.click(d.getByRole("button", { name: "Add evidence" }));
    expect(FakeXhr.last).toBeNull();
  });

  it("rejects files over 1 GiB before sending", async () => {
    const d = await openForm();
    const big = new File(["x"], "huge.bin");
    Object.defineProperty(big, "size", { value: 2 ** 30 + 1 });
    fireEvent.change(d.getByLabelText(/File \(up to 1 GiB\)/), { target: { files: [big] } });
    expect(d.getByRole("alert")).toHaveTextContent("up to 1 GiB");
    await userEvent.click(d.getByRole("button", { name: "Add evidence" }));
    expect(FakeXhr.last).toBeNull();
  });

  it("shows field errors without sending", async () => {
    const d = await openForm();
    fireEvent.change(d.getByLabelText("Commit"), { target: { value: "nothex" } });
    await userEvent.click(d.getByRole("button", { name: "Add evidence" }));
    expect(d.getByRole("alert")).toHaveTextContent(/Commit/);
    expect(FakeXhr.last).toBeNull();
  });
});

describe("community notes on search hits", () => {
  const hits = () => screen.getAllByRole("listitem").filter((li) => li.classList.contains("result"));

  it("requests notes and shows them as plain text", async () => {
    await renderApp("/library");
    await waitFor(() => expect(hits().length).toBeGreaterThan(0));
    expect(seen.some((r) => r.url.includes("/search?") && new URL(r.url).searchParams.get("notes") === "true")).toBe(true);
    const first = within(hits().find((h) => h.dataset.doc === D1)!);
    const cards = await first.findAllByRole("button", { name: /Community note by/ });
    expect(cards).toHaveLength(2);
    // markdown is not rendered: the ** stay literal, no <strong>
    expect(cards[0]).toHaveTextContent("**6% above**");
    expect(cards[0].querySelector("strong")).toBeNull();
  });

  it("caps at two and opens the reader's Notes tab", async () => {
    const note = (i: number): CommunityNote => ({ id: `cn${i}`, kind: "comment", body: `note ${i}`, author: { id: "u", username: "ann" }, score: 0.6, page: i });
    server.use(http.get(`${API_BASE}/search`, ({ request }) => {
      const real = runSearch(new URL(request.url).searchParams);
      return HttpResponse.json({ ...real, results: real.results.map((h, i) => ({ ...h, notes: i === 0 ? [1, 2, 3, 4].map(note) : [] })) });
    }));
    await renderApp("/library");
    await waitFor(() => expect(hits().length).toBeGreaterThan(0));
    const first = within(hits()[0]);
    expect(await first.findAllByRole("button", { name: /Community note by/ })).toHaveLength(2);
    await userEvent.click(first.getByRole("button", { name: "+2 notes" }));
    expect(getReaderState().view).toBe("notes");
    expect(getReaderState().active).toBe(hits()[0].dataset.doc);
  });
});
