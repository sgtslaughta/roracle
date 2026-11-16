import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { apiFetch, seg } from "@/api/client";
import type { Chat, ChatDetail, ChatPage, ChatScope, ModelsResponse, Rating, Shares } from "@/api/chat";

/** Recent chats, 20 per page, newest first; `fetchNextPage` follows the opaque `next` cursor. */
export const useChats = () =>
  useInfiniteQuery({
    queryKey: ["chats"],
    initialPageParam: null as string | null,
    queryFn: ({ pageParam }) => apiFetch<ChatPage>(`/chats?limit=20${pageParam ? `&before=${encodeURIComponent(pageParam)}` : ""}`),
    getNextPageParam: (last) => last.next,
    select: (d) => d.pages.flatMap((p) => p.items),
  });

export const useChat = (id: string | undefined) =>
  useQuery({ queryKey: ["chat", id], queryFn: () => apiFetch<ChatDetail>(`/chats/${seg(id!)}`), enabled: !!id });

export function useCreateChat() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { title?: string; scope?: ChatScope; model?: string } = {}) => apiFetch<Chat>("/chats", { method: "POST", json: body }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["chats"] }),
  });
}

export function usePatchChat(id: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (patch: { title?: string; scope?: ChatScope; pins?: string[]; excludes?: string[]; model?: string }) =>
      apiFetch<Chat>(`/chats/${seg(id)}`, { method: "PATCH", json: patch }),
    // optimistic: pins/excludes/title show at once and roll back if the PATCH fails
    onMutate: async (p) => {
      await qc.cancelQueries({ queryKey: ["chat", id] });
      const prev = qc.getQueryData<ChatDetail>(["chat", id]);
      qc.setQueryData<ChatDetail>(["chat", id], (old) => (old ? { ...old, ...p } : old));
      return { prev };
    },
    onError: (_e, _p, ctx) => {
      if (ctx?.prev) qc.setQueryData(["chat", id], ctx.prev);
      toast("The change couldn't be saved. Try again.");
    },
    // settle on server truth (also covers a post-stream refetch that onMutate cancelled)
    onSettled: () => qc.invalidateQueries({ queryKey: ["chat", id] }),
    onSuccess: (chat) => {
      qc.setQueryData<ChatDetail>(["chat", id], (old) => (old ? { ...old, ...chat } : old));
      return qc.invalidateQueries({ queryKey: ["chats"] });
    },
  });
}

export const useShares = (id: string, enabled: boolean) =>
  useQuery({ queryKey: ["shares", id], queryFn: () => apiFetch<Shares>(`/chats/${seg(id)}/shares`), enabled, gcTime: 0 });

export function useShareChat(id: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (b: Shares) => apiFetch<Shares>(`/chats/${seg(id)}/shares`, { method: "PUT", json: b }),
    onSuccess: (s) => qc.setQueryData(["shares", id], s),
  });
}

export function useRateMessage(chatId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ messageId, value, comment }: { messageId: string; value: 1 | -1 | 0; comment?: string }) =>
      apiFetch<Rating>(`/messages/${seg(messageId)}/rating`, { method: "PUT", json: { value, comment } }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["chat", chatId] }),
  });
}

// ui: GET /chats/models is a contract gap in P4
export const useModels = () => useQuery({ queryKey: ["chat-models"], queryFn: () => apiFetch<ModelsResponse>("/chats/models"), staleTime: Infinity });

/** Markdown export as a Blob (apiFetch returns a blob for non-JSON responses). */
export const exportChat = (id: string) => apiFetch<Blob>(`/chats/${seg(id)}/export.md`);
