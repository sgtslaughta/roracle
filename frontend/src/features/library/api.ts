import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiFetch, seg } from "@/api/client";
import type { Collection, SearchResponse, TagsResponse, Votes, VoteRequest } from "@/api/types";
import { stringifySearch, type LibrarySearch } from "@/lib/searchParams";

export const PAGE_SIZE = 20;
/** The backend caps `total` here; at the cap the count reads "200+". */
export const RESULT_CAP = 200;

export const useSearch = (s: LibrarySearch) =>
  useQuery({
    queryKey: ["search", s],
    queryFn: () => apiFetch<SearchResponse>(`/search${stringifySearch(s, { size: PAGE_SIZE, notes: true })}`),
    placeholderData: keepPreviousData,
  });

export const useCollections = () => useQuery({ queryKey: ["collections"], queryFn: () => apiFetch<Collection[]>("/collections") });

/** id -> collection name (collection filters carry ids). */
export function useCollectionName() {
  const { data } = useCollections();
  return (id: string) => data?.find((c) => c.id === id)?.name ?? id;
}

export function useVote(docId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: VoteRequest) => apiFetch<Votes>(`/documents/${seg(docId)}/vote`, { method: "PUT", json: body }),
    onSuccess: () => Promise.all([qc.invalidateQueries({ queryKey: ["search"] }), qc.invalidateQueries({ queryKey: ["doc", docId] })]),
  });
}

export function useAddTag(docId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (tag: string) => apiFetch<TagsResponse>(`/documents/${seg(docId)}/tags`, { method: "POST", json: { tag } }),
    onSuccess: () => Promise.all([qc.invalidateQueries({ queryKey: ["search"] }), qc.invalidateQueries({ queryKey: ["doc", docId] })]),
  });
}

export function useRemoveTag(docId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (tag: string) => apiFetch<{ tags: string[] }>(`/documents/${seg(docId)}/tags/${seg(tag)}`, { method: "DELETE" }),
    onSuccess: () => Promise.all([qc.invalidateQueries({ queryKey: ["search"] }), qc.invalidateQueries({ queryKey: ["doc", docId] })]),
  });
}
