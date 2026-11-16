import { useMutation, useQueryClient } from "@tanstack/react-query";
import { apiFetch, seg, QUIET } from "@/api/client";
import type { AdminLabel, LabelGates } from "@/api/types";

export interface LabelBody { name: string; colour: string; description: string; rank?: number; gates?: Partial<LabelGates>; is_default?: boolean }

function useLabelMutation<V, R>(fn: (v: V) => Promise<R>) {
  const qc = useQueryClient();
  return useMutation({ meta: QUIET, mutationFn: fn, onSuccess: () => Promise.all(["labels", "search", "doc", "uploads", "notes", "proposals", "drafts"].map((k) => qc.invalidateQueries({ queryKey: [k] }))) });
}

export const useCreateLabel = () => useLabelMutation((b: LabelBody) => apiFetch<AdminLabel>("/admin/labels", { method: "POST", json: b }));
export const usePatchLabel = () => useLabelMutation(({ id, ...b }: { id: string } & Partial<LabelBody>) => apiFetch<AdminLabel>(`/admin/labels/${seg(id)}`, { method: "PATCH", json: b }));
export const useDeleteLabel = () => useLabelMutation((id: string) => apiFetch<void>(`/admin/labels/${seg(id)}`, { method: "DELETE" }));
/** `ids` lists every label, least sensitive first. */
export const useReorderLabels = () => useLabelMutation((ids: string[]) => apiFetch<{ items: AdminLabel[] }>("/admin/labels/order", { method: "PUT", json: { ids } }));
