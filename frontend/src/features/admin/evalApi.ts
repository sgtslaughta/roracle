import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiFetch, seg, QUIET } from "@/api/client";

export type EvalPath = "chat" | "search";
export type EvalKind = "fact" | "multihop" | "global";
export interface EvalSet { id: string; name: string; description: string; is_reference: boolean; queries: number; drafts: number }
export interface GoldRef { doc: string; quote?: string; page?: number; grade?: number }
export interface EvalQuery {
  id: string; set_id: string; key: string; text: string; kind: EvalKind; principals: string[]; principal_names: string[];
  as_pat: boolean; relevant: GoldRef[]; status: "draft" | "active";
}
export type Scores = Record<string, number>;
export interface EvalRunItem {
  id: string; set_id: string | null; set_name: string; started_at: string; trigger: string; label: string; path: EvalPath;
  status: "running" | "done" | "failed"; metrics: { overall: Scores } | null;
}
export interface WorstPair { query_key: string; text: string; kind: string; principal: string; "p_recall@10": number; top_docs: string[] }
export interface EvalRunDetail extends Omit<EvalRunItem, "metrics"> {
  error: string | null; by_kind: Record<string, Scores>; by_principal: Record<string, Scores>; worst: WorstPair[];
}

const KEYS = ["admin", "eval"];
export const useEvalSets = () => useQuery({ queryKey: [...KEYS, "sets"], queryFn: async () => (await apiFetch<{ items: EvalSet[] }>("/admin/eval/sets")).items });
export const useEvalQueries = (setId: string) => useQuery({ queryKey: [...KEYS, "queries", setId], queryFn: async () => (await apiFetch<{ items: EvalQuery[] }>(`/admin/eval/sets/${seg(setId)}/queries`)).items });
export const useEvalRuns = (setId: string) =>
  useQuery({ queryKey: [...KEYS, "runs", setId], queryFn: async () => (await apiFetch<{ items: EvalRunItem[] }>(`/admin/eval/runs?limit=100${setId ? `&set_id=${seg(setId)}` : ""}`)).items });
export const useEvalRun = (id: string | null) => useQuery({ queryKey: [...KEYS, "run", id], enabled: !!id, queryFn: () => apiFetch<EvalRunDetail>(`/admin/eval/runs/${seg(id!)}`) });

function useEvalMutation<V, R>(fn: (v: V) => Promise<R>) {
  const qc = useQueryClient();
  return useMutation({ meta: QUIET, mutationFn: fn, onSuccess: () => qc.invalidateQueries({ queryKey: KEYS }) });
}
export const useCreateSet = () => useEvalMutation((b: { name: string }) => apiFetch<EvalSet>("/admin/eval/sets", { method: "POST", json: b }));
export const usePatchSet = () => useEvalMutation(({ id, ...b }: { id: string; is_reference?: boolean }) => apiFetch<EvalSet>(`/admin/eval/sets/${seg(id)}`, { method: "PATCH", json: b }));
export const useDeleteSet = () => useEvalMutation((id: string) => apiFetch<void>(`/admin/eval/sets/${seg(id)}`, { method: "DELETE" }));
export const usePatchQuery = () => useEvalMutation(({ id, ...b }: { id: string; status?: "draft" | "active" }) => apiFetch<EvalQuery>(`/admin/eval/queries/${seg(id)}`, { method: "PATCH", json: b }));
export const useDeleteQuery = () => useEvalMutation((id: string) => apiFetch<void>(`/admin/eval/queries/${seg(id)}`, { method: "DELETE" }));
export const useGenerate = () => useEvalMutation(({ id, ...b }: { id: string; n: number; principal: string; kind?: EvalKind }) => apiFetch<{ job_id: number }>(`/admin/eval/sets/${seg(id)}/generate`, { method: "POST", json: b }));
export const useStartRun = () => useEvalMutation((b: { set_id: string; path: EvalPath; judge: boolean }) => apiFetch<{ job_id: number }>("/admin/eval/runs", { method: "POST", json: b }));
