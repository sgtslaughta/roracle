import { waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { http, HttpResponse } from "msw";
import { server } from "@/mocks/server";
import { API_BASE, ApiError, apiFetch, setAuthHooks } from "@/api/client";
import type { ChatDetail, Citation, Viewer } from "@/api/chat";
import { createSseParser, streamMessage } from "./stream";

const VIEWER: Viewer = { tabs: [], ignored: [], selection: null };
const collect = () => {
  const evs: { event: string; data: string }[] = [];
  return { evs, feed: createSseParser((e) => evs.push(e)) };
};
const handlers = () => ({ onToken: vi.fn(), onCitations: vi.fn(), onDone: vi.fn(), onError: vi.fn() });
const sse = (body: string | ReadableStream<Uint8Array>) => new HttpResponse(body, { headers: { "Content-Type": "text/event-stream" } });

describe("createSseParser", () => {
  it("handles an event split across chunks mid-line and mid-CRLF", () => {
    const { evs, feed } = collect();
    ["event: tok", "en\r", "\ndata: {\"a\"", ":1}\r\n\r", "\n"].forEach(feed);
    expect(evs).toEqual([{ event: "token", data: '{"a":1}' }]);
  });
  it("joins multi-line data, strips one leading space, defaults event to message", () => {
    const { evs, feed } = collect();
    feed("data: a\ndata:  b\ndata:c\n\n");
    expect(evs).toEqual([{ event: "message", data: "a\n b\nc" }]);
  });
  it("ignores comments, id and retry; no dispatch without data; lone CR is a line end", () => {
    const { evs, feed } = collect();
    feed(": ping\nid: 3\nretry: 10\n\nevent: x\n\n");
    expect(evs).toEqual([]);
    feed("event: done\rdata: 1\r\r");
    expect(evs).toEqual([{ event: "done", data: "1" }]);
  });
});

describe("createSseParser edge cases", () => {
  it("no event without the blank line", () => {
    const { evs, feed } = collect();
    feed("data: x\n");
    expect(evs).toEqual([]);
  });
  it("CR, CR, LF split across chunks yields exactly two events", () => {
    const { evs, feed } = collect();
    ["data: 1\r", "\r", "\ndata: 2\n\n"].forEach(feed);
    expect(evs).toEqual([{ event: "message", data: "1" }, { event: "message", data: "2" }]);
  });
});

describe("streamMessage", () => {
  const hooks = { getToken: vi.fn(async () => "t"), renew: vi.fn(async () => true), signIn: vi.fn(async () => {}) };
  beforeEach(() => setAuthHooks(hooks));
  afterEach(() => setAuthHooks(null));

  it("dispatches tokens, citations and done (mock chat end to end)", async () => {
    const h = handlers();
    await streamMessage("c1", { content: "spectral gap hypergraphs", viewer: VIEWER }, h);
    expect(h.onToken.mock.calls.length).toBeGreaterThan(1);
    expect(h.onToken.mock.calls.map((c) => c[0]).join("").length).toBeGreaterThan(20);
    const items = h.onCitations.mock.calls[0][0] as Citation[];
    expect(items[0].n).toBe(1);
    expect(h.onDone).toHaveBeenCalledWith(expect.any(String));
    expect(h.onError).not.toHaveBeenCalled();
  });

  it("422 throws ApiError with the detail", async () => {
    const err = await streamMessage("c1", { content: "q", viewer: VIEWER, model: "nope" }, handlers()).catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err).toMatchObject({ status: 422, detail: "unknown model" });
  });

  it("401 then renew retries the stream", async () => {
    let n = 0;
    server.use(http.post(`${API_BASE}/chats/c1/messages`, () =>
      n++ === 0 ? new HttpResponse(null, { status: 401 }) : sse('event: token\ndata: {"text":"hi"}\n\nevent: done\ndata: {"message_id":"m9"}\n\n')));
    const h = handlers();
    await streamMessage("c1", { content: "q", viewer: VIEWER }, h);
    expect(hooks.renew).toHaveBeenCalledTimes(1);
    expect(h.onToken).toHaveBeenCalledWith("hi");
    expect(h.onDone).toHaveBeenCalledWith("m9");
  });

  it("ignores malformed JSON and reports a cut-off stream", async () => {
    server.use(http.post(`${API_BASE}/chats/c1/messages`, () => sse('event: token\ndata: {oops\n\nevent: token\ndata: {"text":"a"}\n\n')));
    const h = handlers();
    await streamMessage("c1", { content: "q", viewer: VIEWER }, h);
    expect(h.onToken).toHaveBeenCalledTimes(1);
    expect(h.onError).toHaveBeenCalledWith("The answer was cut off.");
    expect(h.onDone).not.toHaveBeenCalled();
  });

  it("error event goes to onError only", async () => {
    server.use(http.post(`${API_BASE}/chats/c1/messages`, () => sse('event: error\ndata: {"detail":"boom"}\n\n')));
    const h = handlers();
    await streamMessage("c1", { content: "q", viewer: VIEWER }, h);
    expect(h.onError).toHaveBeenCalledTimes(1);
    expect(h.onError).toHaveBeenCalledWith("boom");
  });

  it("abort mid-stream rejects and stops tokens", async () => {
    const enc = new TextEncoder();
    server.use(http.post(`${API_BASE}/chats/c1/messages`, () => sse(new ReadableStream({
      start(c) { c.enqueue(enc.encode('event: token\ndata: {"text":"a"}\n\n')); }, // never closes
    }))));
    const ac = new AbortController();
    const h = handlers();
    h.onToken.mockImplementation(() => ac.abort());
    const err = await streamMessage("c1", { content: "q", viewer: VIEWER }, h, ac.signal).catch((e) => e);
    expect(ac.signal.aborted).toBe(true);
    expect(err).toBeInstanceOf(Error);
    expect(h.onToken).toHaveBeenCalledTimes(1);
    expect(h.onError).not.toHaveBeenCalled();
  });

  const enc = new TextEncoder();
  const spyStream = (chunks: string[], cancel = vi.fn()) =>
    http.post(`${API_BASE}/chats/c1/messages`, () => sse(new ReadableStream({ start(c) { chunks.forEach((x) => c.enqueue(enc.encode(x))); }, cancel }))); // never closes
  const tok = (t: string) => `event: token\ndata: {"text":"${t}"}\n\n`;

  it("abort inside the first token of a chunk drops the rest and cancels the reader", async () => {
    const cancel = vi.fn();
    server.use(spyStream([tok("a") + tok("b")], cancel));
    const ac = new AbortController();
    const h = handlers();
    h.onToken.mockImplementation(() => ac.abort());
    const err = await streamMessage("c1", { content: "q", viewer: VIEWER }, h, ac.signal).catch((e) => e);
    expect(h.onToken).toHaveBeenCalledTimes(1);
    expect(err).toBeInstanceOf(Error);
    expect((err as Error).name).toBe("AbortError");
    expect(cancel).toHaveBeenCalledTimes(1);
  });

  it("a throwing handler rejects and cancels the reader", async () => {
    const cancel = vi.fn();
    server.use(spyStream([tok("a")], cancel));
    const h = handlers();
    h.onToken.mockImplementation(() => { throw new Error("bad handler"); });
    await expect(streamMessage("c1", { content: "q", viewer: VIEWER }, h)).rejects.toThrow("bad handler");
    expect(cancel).toHaveBeenCalledTimes(1);
  });

  it("401, renew ok, retry 401: signIn once and ApiError 401", async () => {
    server.use(http.post(`${API_BASE}/chats/c1/messages`, () => new HttpResponse(null, { status: 401 })));
    hooks.renew.mockClear(); hooks.signIn.mockClear();
    const err = await streamMessage("c1", { content: "q", viewer: VIEWER }, handlers()).catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err).toMatchObject({ status: 401 });
    expect(hooks.renew).toHaveBeenCalledTimes(1);
    expect(hooks.signIn).toHaveBeenCalledTimes(1);
  });

  it("401 with failed renew signs in", async () => {
    server.use(http.post(`${API_BASE}/chats/c1/messages`, () => new HttpResponse(null, { status: 401 })));
    hooks.renew.mockResolvedValueOnce(false); hooks.signIn.mockClear();
    await expect(streamMessage("c1", { content: "q", viewer: VIEWER }, handlers())).rejects.toMatchObject({ status: 401 });
    expect(hooks.signIn).toHaveBeenCalledTimes(1);
  });

  it("ignores done without a string message_id and citations without an items array", async () => {
    server.use(http.post(`${API_BASE}/chats/c1/messages`, () =>
      sse('event: citations\ndata: {"items":5}\n\nevent: done\ndata: {"message_id":7}\n\nevent: done\ndata: {}\n\n')));
    const h = handlers();
    await streamMessage("c1", { content: "q", viewer: VIEWER }, h);
    expect(h.onCitations).not.toHaveBeenCalled();
    expect(h.onDone).not.toHaveBeenCalled();
    expect(h.onError).toHaveBeenCalledWith("The answer was cut off.");
  });

  it("mock: aborting mid-stream saves the assistant message as interrupted with partial content", async () => {
    const ac = new AbortController();
    const h = handlers();
    h.onToken.mockImplementation(() => ac.abort());
    await streamMessage("c1", { content: "spectral gap hypergraphs", viewer: VIEWER }, h, ac.signal).catch(() => {});
    const last = await waitFor(async () => {
      const chat = await apiFetch<ChatDetail>("/chats/c1");
      const m = chat.messages[chat.messages.length - 1];
      expect(m).toMatchObject({ role: "assistant", status: "interrupted" });
      return m;
    });
    expect(last.citations).toEqual([]);
    expect(last.content.length).toBeGreaterThan(0);
  });
});
