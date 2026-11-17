import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { apiFetch, ApiError, QUIET, seg } from "@/api/client";
import type { SavedSearch } from "@/api/types";
import { stringifySearch, type LibrarySearch } from "@/lib/searchParams";

const KEY = ["saved-searches"];
const BASE = "/me/saved-searches";

/** The URL query string for a search: no leading `?`, no page. */
export const savedQuery = (s: LibrarySearch) => stringifySearch({ ...s, page: undefined }).slice(1);

/** Readable text for a failed save/rename (409 is the duplicate name; 422 carries the server's limit message). */
export const saveError = (e: unknown) =>
  e instanceof ApiError && e.status === 409 ? "You already have a saved search with this name." : e instanceof ApiError ? e.message : "That didn't work. Try again.";

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
    onSettled: refresh,
  });
}
