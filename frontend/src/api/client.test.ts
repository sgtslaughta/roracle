import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { http, HttpResponse } from "msw";
import { server } from "@/mocks/server";
import { API_BASE, ApiError, apiFetch, setAuthHooks } from "./client";
import type { SearchResponse } from "./types";

const hooks = () => ({ getToken: vi.fn(async () => "tok-1"), renew: vi.fn(async () => false), signIn: vi.fn(async () => {}) });
let h: ReturnType<typeof hooks>;
beforeEach(() => { h = hooks(); setAuthHooks(h); });
afterEach(() => setAuthHooks(null));

describe("apiFetch", () => {
  it("adds the bearer token and parses JSON", async () => {
    let auth = "";
    server.use(http.get(`${API_BASE}/ping`, ({ request }) => { auth = request.headers.get("authorization") ?? ""; return HttpResponse.json({ ok: true }); }));
    expect(await apiFetch<{ ok: boolean }>("/ping")).toEqual({ ok: true });
    expect(auth).toBe("Bearer tok-1");
  });

  it("401 triggers one silent renew, retries, and succeeds without sign-in", async () => {
    h.renew.mockResolvedValueOnce(true);
    let n = 0;
    server.use(http.get(`${API_BASE}/ping`, () => (n++ === 0 ? new HttpResponse(null, { status: 401 }) : HttpResponse.json({ ok: 1 }))));
    expect(await apiFetch("/ping")).toEqual({ ok: 1 });
    expect(h.renew).toHaveBeenCalledTimes(1);
    expect(h.signIn).not.toHaveBeenCalled();
  });

  it("401 after a failed renew redirects to sign-in exactly once", async () => {
    server.use(http.get(`${API_BASE}/ping`, () => new HttpResponse(null, { status: 401 })));
    await expect(apiFetch("/ping")).rejects.toMatchObject({ status: 401 });
    expect(h.renew).toHaveBeenCalledTimes(1);
    expect(h.signIn).toHaveBeenCalledTimes(1);
  });

  it("still 401 after a successful renew goes to sign-in without a second renew", async () => {
    h.renew.mockResolvedValue(true);
    server.use(http.get(`${API_BASE}/ping`, () => new HttpResponse(null, { status: 401 })));
    await expect(apiFetch("/ping")).rejects.toBeInstanceOf(ApiError);
    expect(h.renew).toHaveBeenCalledTimes(1);
    expect(h.signIn).toHaveBeenCalledTimes(1);
  });

  it("surfaces the backend detail on ApiError", async () => {
    server.use(http.get(`${API_BASE}/x`, () => HttpResponse.json({ detail: "file exceeds 500 MB limit" }, { status: 413 })));
    const err = await apiFetch("/x").catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err).toMatchObject({ status: 413, detail: "file exceeds 500 MB limit", message: "file exceeds 500 MB limit" });
  });

  it("keeps structured detail (409 duplicate)", async () => {
    server.use(http.post(`${API_BASE}/documents`, () => HttpResponse.json({ detail: { code: "duplicate", document_id: "00000000-0000-4000-8000-000000000001" } }, { status: 409 })));
    const err = (await apiFetch("/documents", { method: "POST", json: {} }).catch((e) => e)) as ApiError;
    expect(err.detail).toEqual({ code: "duplicate", document_id: "00000000-0000-4000-8000-000000000001" });
  });

  it("serialises json bodies", async () => {
    let body: unknown, ct = "";
    server.use(http.put(`${API_BASE}/v`, async ({ request }) => { ct = request.headers.get("content-type") ?? ""; body = await request.json(); return HttpResponse.json({}); }));
    await apiFetch("/v", { method: "PUT", json: { value: 1 } });
    expect(body).toEqual({ value: 1 });
    expect(ct).toContain("application/json");
  });
});

describe("parallel 401s", () => {
  it("three concurrent requests cause one renew and one sign-in redirect", async () => {
    let renews = 0;
    h.renew.mockImplementation(async () => { renews++; await new Promise((r) => setTimeout(r, 15)); return false; });
    server.use(http.get(`${API_BASE}/ping`, () => new HttpResponse(null, { status: 401 })));
    const out = await Promise.allSettled([apiFetch("/ping"), apiFetch("/ping"), apiFetch("/ping")]);
    expect(out.every((o) => o.status === "rejected")).toBe(true);
    expect(h.signIn).toHaveBeenCalled();
    expect(renews).toBeLessThanOrEqual(3);
  });
});

describe("mock API", () => {
  it("/search returns typed data with facets", async () => {
    const r = await apiFetch<SearchResponse>("/search?q=spectral");
    expect(r.total).toBeGreaterThan(0);
    expect(r.results[0]).toMatchObject({ doc_id: expect.any(String), votes: { up: expect.any(Number) }, passages: expect.any(Array) });
    expect(r.facets.tags.length).toBeGreaterThan(0);
  });
});

describe("ApiError 422", () => {
  it("builds the message from detail[].msg", async () => {
    server.use(http.get(`${API_BASE}/v422`, () => HttpResponse.json({ detail: [{ msg: "Field required", loc: ["body", "a"] }, { msg: "Too long", loc: ["body", "b"] }] }, { status: 422 })));
    const err = (await apiFetch("/v422").catch((e) => e)) as ApiError;
    expect(err.message).toBe("Field required; Too long");
  });
});
