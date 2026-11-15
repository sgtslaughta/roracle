import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiFetch, seg } from "@/api/client";
import type { Chat, ChatDetail, ChatScope, ChatSummary, ModelsResponse } from "@/api/chat";

export const useChats = () => useQuery({ queryKey: ["chats"], queryFn: () => apiFetch<ChatSummary[]>("/chats") });

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
    mutationFn: (patch: { title?: string; scope?: ChatScope; pins?: string[]; excludes?: string[] }) =>
      apiFetch<Chat>(`/chats/${seg(id)}`, { method: "PATCH", json: patch }),
    // optimistic: pins/excludes/title show at once and roll back if the PATCH fails
    onMutate: async (p) => {
      await qc.cancelQueries({ queryKey: ["chat", id] });
      const prev = qc.getQueryData<ChatDetail>(["chat", id]);
      qc.setQueryData<ChatDetail>(["chat", id], (old) => (old ? { ...old, ...p } : old));
      return { prev };
    },
    onError: (_e, _p, ctx) => { if (ctx?.prev) qc.setQueryData(["chat", id], ctx.prev); },
    onSuccess: (chat) => {
      qc.setQueryData<ChatDetail>(["chat", id], (old) => (old ? { ...old, ...chat } : old));
      return qc.invalidateQueries({ queryKey: ["chats"] });
    },
  });
}

export const useShareChat = (id: string) =>
  useMutation({ mutationFn: (b: { users: string[]; groups: string[] }) => apiFetch<void>(`/chats/${seg(id)}/shares`, { method: "PUT", json: b }) });

export function useRateMessage(chatId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ messageId, value, comment }: { messageId: string; value: 1 | -1; comment?: string }) =>
      apiFetch<{ value: 1 | -1; comment: string | null }>(`/messages/${seg(messageId)}/rating`, { method: "PUT", json: { value, comment } }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["chat", chatId] }),
  });
}

// ui: GET /chats/models is a contract gap in P4
export const useModels = () => useQuery({ queryKey: ["chat-models"], queryFn: () => apiFetch<ModelsResponse>("/chats/models"), staleTime: Infinity });

/** Markdown export as a Blob (apiFetch returns a blob for non-JSON responses). */
export const exportChat = (id: string) => apiFetch<Blob>(`/chats/${seg(id)}/export.md`);
