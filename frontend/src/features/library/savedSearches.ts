import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { apiFetch, ApiError, QUIET, seg } from "@/api/client";
import type { SavedSearch } from "@/api/types";
import { stringifySearch, type LibrarySearch } from "@/lib/searchParams";

const KEY = ["saved-searches"];
const BASE = "/me/saved-searches";

/** The URL query string for a search: no leading `?`, no page. */
export const savedQuery = (s: LibrarySearch) => stringifySearch({ ...s, page: undefined }).slice(1);

export const QUERY_MAX = 4000;
export const TOO_LONG = "This search is too long to save. Remove some filters.";

/** Product copy for any failed saved-search request (the one place server errors are mapped). */
export function saveError(e: unknown): string {
  if (e instanceof ApiError) {
    if (e.status === 409) return "You already have a saved search with this name.";
    if (e.status === 404) return "That saved search no longer exists.";
    if (e.status === 422 && /at most 200/.test(e.message)) return "You can keep up to 200 saved searches. Delete one to save another.";
    if (e.status === 422 && /4000/.test(e.message)) return TOO_LONG;
  }
  return "Couldn't complete that. Try again.";
}

export const useSavedSearches = () => useQuery({ queryKey: KEY, queryFn: () => apiFetch<SavedSearch[]>(BASE) });

export function useSaveSearch() {
  const qc = useQueryClient();
  return useMutation({
    meta: QUIET,
    mutationFn: (b: { name: string; query: string }) => apiFetch<SavedSearch>(BASE, { method: "POST", json: b }),
    onSettled: () => qc.invalidateQueries({ queryKey: KEY }),
  });
}

export function useRenameSavedSearch() {
  const qc = useQueryClient();
  return useMutation({
    meta: QUIET,
    mutationFn: ({ id, name }: { id: string; name: string }) => apiFetch<SavedSearch>(`${BASE}/${seg(id)}`, { method: "PATCH", json: { name } }),
    onSettled: () => qc.invalidateQueries({ queryKey: KEY }),
  });
}

/** Deletes, then offers Undo, which re-creates it (same name and query). */
export function useDeleteSavedSearch() {
  const qc = useQueryClient();
  const refresh = () => qc.invalidateQueries({ queryKey: KEY });
  return useMutation({
    mutationFn: (s: SavedSearch) => apiFetch(`${BASE}/${seg(s.id)}`, { method: "DELETE" }),
    onSuccess: (_d, s) => {
      const undo = () => apiFetch(BASE, { method: "POST", json: { name: s.name, query: s.query } }).then(refresh, () => toast("Couldn't undo. Save the search again."));
      toast("Saved search deleted.", { action: { label: "Undo", onClick: () => void undo() } });
    },
    onError: (e) => toast(saveError(e)),
    onSettled: refresh,
  });
}
