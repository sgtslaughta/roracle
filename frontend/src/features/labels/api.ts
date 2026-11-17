import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiFetch, seg, QUIET } from "@/api/client";
import type { AdminLabel, Label } from "@/api/types";
import type { Note } from "@/api/notes";

/** All labels by rank (GET /labels is open to every signed-in user). */
export const useLabels = () =>
  useQuery({ queryKey: ["labels"], staleTime: 60_000, queryFn: async () => (await apiFetch<{ items: AdminLabel[] }>("/labels")).items });

export const useLabelById = (id: string | undefined) => useLabels().data?.find((l) => l.id === id);

const refreshAll = (qc: ReturnType<typeof useQueryClient>, docId: string) =>
  Promise.all(["search", "uploads", "notes", "drafts"].map((k) => qc.invalidateQueries({ queryKey: [k] })).concat(qc.invalidateQueries({ queryKey: ["doc", docId] })));

/** PUT /documents/{id}/label: owner/admin; lowering is admin-only and may need a reason. */
export function useSetDocLabel(docId: string) {
  const qc = useQueryClient();
  return useMutation({
    meta: QUIET,
    mutationFn: (b: { label_id: string; reason?: string }) => apiFetch<{ label: Label }>(`/documents/${seg(docId)}/label`, { method: "PUT", json: b }),
    onSuccess: () => refreshAll(qc, docId),
  });
}

/** PUT /annotations/{id}/label: author/admin; returns the note. */
export function useSetNoteLabel(docId: string) {
  const qc = useQueryClient();
  return useMutation({
    meta: QUIET,
    mutationFn: ({ id, ...b }: { id: string; label_id: string | null; reason?: string }) => apiFetch<Note>(`/annotations/${seg(id)}/label`, { method: "PUT", json: b }),
    onSuccess: () => refreshAll(qc, docId),
  });
}

/** Tooltip text for a download blocked by `block_export` (owner and admins are exempt). */
export const downloadBlockedText = (label: Label) => `Downloads are disabled by the ${label.name} label`;

/** id -> label name (label filters carry ids). */
export function useLabelName() {
  const { data } = useLabels();
  return (id: string) => data?.find((l) => l.id === id)?.name ?? id;
}
