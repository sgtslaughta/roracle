import { useRef } from "react";
import { keepPreviousData, useMutation, useQuery, useQueryClient, type QueryClient } from "@tanstack/react-query";
import { apiFetch, seg } from "@/api/client";
import type { Collection, DocumentDetail, SearchResponse, TagsResponse, Votes, VoteRequest } from "@/api/types";
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

type Patch = { votes?: Votes; tags?: string[] };

/** Optimistic: patch the document in every cached search page and its detail; the returned snapshot rolls it back. */
async function patchDoc(qc: QueryClient, docId: string, fn: (d: Patch) => Patch) {
  await Promise.all([qc.cancelQueries({ queryKey: ["search"] }), qc.cancelQueries({ queryKey: ["doc", docId] })]);
  const searches = qc.getQueriesData<SearchResponse>({ queryKey: ["search"] });
  const doc = qc.getQueryData<DocumentDetail>(["doc", docId]);
  for (const [k, d] of searches) if (d) qc.setQueryData<SearchResponse>(k, { ...d, results: d.results.map((h) => (h.doc_id === docId ? { ...h, ...fn(h) } : h)) });
  if (doc) qc.setQueryData<DocumentDetail>(["doc", docId], { ...doc, ...fn(doc) });
  return { searches, doc };
}
type Snap = Awaited<ReturnType<typeof patchDoc>>;
const rollback = (qc: QueryClient, docId: string, s?: Snap) => {
  s?.searches.forEach(([k, d]) => qc.setQueryData(k, d));
  if (s?.doc) qc.setQueryData(["doc", docId], s.doc);
};
const refresh = (qc: QueryClient, docId: string) => Promise.all([qc.invalidateQueries({ queryKey: ["search"] }), qc.invalidateQueries({ queryKey: ["doc", docId] })]);

const tally = (v: Votes, value: 1 | -1 | 0): Votes => ({
  up: v.up - (v.mine > 0 ? 1 : 0) + (value > 0 ? 1 : 0),
  down: v.down - (v.mine < 0 ? 1 : 0) + (value < 0 ? 1 : 0),
  mine: value,
});

/** Vote with optimistic tallies; a failed PUT rolls back and the global handler toasts. */
export function useVote(docId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: VoteRequest) => apiFetch<Votes>(`/documents/${seg(docId)}/vote`, { method: "PUT", json: body }),
    onMutate: (b) => patchDoc(qc, docId, (d) => ({ votes: d.votes && tally(d.votes, b.value) })),
    onError: (_e, _b, snap) => rollback(qc, docId, snap),
    onSettled: () => refresh(qc, docId),
  });
}

export function useAddTag(docId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (tag: string) => apiFetch<TagsResponse>(`/documents/${seg(docId)}/tags`, { method: "POST", json: { tag } }),
    onMutate: (tag) => {
      const t = tag.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "").slice(0, 40);
      return patchDoc(qc, docId, (d) => ({ tags: d.tags && (!t || d.tags.includes(t) ? d.tags : [...d.tags, t]) }));
    },
    onError: (_e, _t, snap) => rollback(qc, docId, snap),
    onSettled: () => refresh(qc, docId),
  });
}

export function useRemoveTag(docId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (tag: string) => apiFetch<{ tags: string[] }>(`/documents/${seg(docId)}/tags/${seg(tag)}`, { method: "DELETE" }),
    onMutate: (tag) => patchDoc(qc, docId, (d) => ({ tags: d.tags?.filter((x) => x !== tag) })),
    onError: (_e, _t, snap) => rollback(qc, docId, snap),
    onSettled: () => refresh(qc, docId),
  });
}

/** Add a document to a collection, or create the collection first (`create`). */
export function useAddToCollection(docId: string) {
  const qc = useQueryClient();
  const made = useRef<Record<string, string>>({});
  return useMutation({
    mutationFn: async ({ id, create }: { id?: string; create?: string }) => {
      // a created collection is remembered, so a retry after a failed PUT reuses it instead of duplicating
      const cid = id ?? (made.current[create!] ??= (await apiFetch<Collection>("/collections", { method: "POST", json: { name: create } })).id);
      return apiFetch<{ id: string; count: number }>(`/collections/${seg(cid)}/items/${seg(docId)}`, { method: "PUT" });
    },
    onSettled: () => qc.invalidateQueries({ queryKey: ["collections"] }),
  });
}
