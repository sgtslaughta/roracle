import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "@/api/client";
import type { Collection, SearchResponse, Votes, VoteRequest } from "@/api/types";
import { stringifySearch, type LibrarySearch } from "@/lib/searchParams";

export const PAGE_SIZE = 20;

export const useSearch = (s: LibrarySearch) =>
  useQuery({
    queryKey: ["search", s],
    queryFn: () => apiFetch<SearchResponse>(`/search${stringifySearch({ ...s, size: PAGE_SIZE })}`),
    placeholderData: keepPreviousData,
  });

export const useCollections = () => useQuery({ queryKey: ["collections"], queryFn: () => apiFetch<Collection[]>("/collections") });

export function useVote(docId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: VoteRequest) => apiFetch<Votes>(`/documents/${docId}/vote`, { method: "PUT", json: body }),
    onSuccess: () => Promise.all([qc.invalidateQueries({ queryKey: ["search"] }), qc.invalidateQueries({ queryKey: ["doc", docId] })]),
  });
}

export function useAddTag(docId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (tag: string) => apiFetch<{ tags: string[] }>(`/documents/${docId}/tags`, { method: "POST", json: { tag } }),
    onSuccess: () => Promise.all([qc.invalidateQueries({ queryKey: ["search"] }), qc.invalidateQueries({ queryKey: ["doc", docId] })]),
  });
}
