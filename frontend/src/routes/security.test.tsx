import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider } from "@tanstack/react-router";
import { describe, expect, it, vi } from "vitest";
import { ApiError, apiFetch, seg } from "@/api/client";
import { createAppRouter } from "@/app/router";
import { getReaderState, resetReaderStore } from "@/features/reader/useReaderStore";
import { isChunkId, isDocId, isVersion } from "@/lib/ids";

async function renderAt(path: string) {
  const router = createAppRouter([path]);
  await router.load();
  render(<QueryClientProvider client={new QueryClient()}><RouterProvider router={router} /></QueryClientProvider>);
}
const docCalls = (spy: { mock: { calls: unknown[][] } }) => spy.mock.calls.filter(([u]) => /\/documents|admin/.test(String(u instanceof Request ? u.url : u)));

describe("path safety", () => {
  it("seg encodes path separators", () => {
    expect(seg("a/b")).toBe("a%2Fb");
    expect(seg("../x")).toBe("..%2Fx");
    expect(seg(3)).toBe("3");
  });

  it("apiFetch refuses dot-segments and sends nothing", async () => {
    const spy = vi.spyOn(globalThis, "fetch");
    await expect(apiFetch("/documents/../admin/users")).rejects.toBeInstanceOf(ApiError);
    expect(spy).not.toHaveBeenCalled();
    spy.mockRestore();
  });

  it("validators", () => {
    expect(isDocId("00000000-0000-4000-8000-000000000001")).toBe(true);
    for (const bad of ["../admin", "d1", "", "1/2", "00000000-0000-4000-8000-00000000000g"]) expect(isDocId(bad)).toBe(false);
    expect(isVersion("12")).toBe(true);
    for (const bad of ["0", "-1", "1.5", "../1", "1/2", "a"]) expect(isVersion(bad)).toBe(false);
    expect(isChunkId("v:2")).toBe(true);
    for (const bad of ["../x", "a/b", "a b", "?x", ".."]) expect(isChunkId(bad)).toBe(false);
  });

  it("/d/..%2Fadmin renders not-found and no document request is sent", async () => {
    resetReaderStore();
    const spy = vi.spyOn(globalThis, "fetch");
    await renderAt("/d/..%2Fadmin");
    expect(await screen.findByText("Document not found")).toBeInTheDocument();
    expect(getReaderState().tabs).toHaveLength(0);
    expect(docCalls(spy)).toHaveLength(0);
    spy.mockRestore();
  });

  it("invalid version is not found too", async () => {
    resetReaderStore();
    const spy = vi.spyOn(globalThis, "fetch");
    await renderAt("/d/00000000-0000-4000-8000-000000000001/v/..%2F1");
    expect(await screen.findByText("Document not found")).toBeInTheDocument();
    expect(docCalls(spy)).toHaveLength(0);
    spy.mockRestore();
  });

  it("a hostile chunk id in the hash is dropped", async () => {
    resetReaderStore();
    await renderAt("/d/00000000-0000-4000-8000-000000000001#p=2&c=..%2F..%2Fadmin");
    await screen.findByRole("heading", { name: /Lower bounds/ });
    expect(getReaderState().tabs[0].chunkId).toBeUndefined();
  });
});
