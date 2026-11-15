import type { ReactNode } from "react";
import { describe, expect, it } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor, act } from "@testing-library/react";
import { exportChat, useChat, useChats, useCreateChat, useModels, usePatchChat, useRateMessage } from "./api";

const wrap = () => {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return ({ children }: { children: ReactNode }) => <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
};

describe("chat hooks", () => {
  it("lists the seeded chat and models", async () => {
    const w = wrap();
    const chats = renderHook(() => useChats(), { wrapper: w });
    await waitFor(() => expect(chats.result.current.data?.[0].title).toBe("Spectral gap in hypergraphs"));
    const models = renderHook(() => useModels(), { wrapper: w });
    await waitFor(() => expect(models.result.current.data?.default).toBe("frontier-large"));
  });
  it("creates then gets, patches and rates", async () => {
    const w = wrap();
    const create = renderHook(() => useCreateChat(), { wrapper: w });
    let id = "";
    await act(async () => { id = (await create.result.current.mutateAsync({ title: "New one" })).id; });
    const chat = renderHook(() => useChat(id), { wrapper: w });
    await waitFor(() => expect(chat.result.current.data?.title).toBe("New one"));
    expect(chat.result.current.data?.is_owner).toBe(true);
    const patch = renderHook(() => usePatchChat(id), { wrapper: w });
    await act(async () => { await patch.result.current.mutateAsync({ title: "Renamed" }); });
    await waitFor(() => expect(chat.result.current.data?.title).toBe("Renamed"));
    const rate = renderHook(() => useRateMessage("c1"), { wrapper: w });
    await act(async () => { await rate.result.current.mutateAsync({ messageId: "c1-m2", value: 1 }); });
  });
  it("exports markdown", async () => {
    const blob = await exportChat("c1");
    expect(await blob.text()).toMatch(/^# Spectral gap in hypergraphs/);
    expect(await blob.text()).toMatch(/\[1\] M\. Chen, R\. Aldana, Lower bounds/);
  });
});
