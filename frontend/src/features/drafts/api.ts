import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError, apiFetch, authedFetch, errorOf, QUIET, seg } from "@/api/client";
import type { Citations, DraftDetail, DraftSummary, SubmitBody, SubmitResult, Validation, VersionBody } from "@/api/drafts";

const P = (id: string) => `/drafts/${seg(id)}`;
const draftKey = (id: string) => ["draft", id];
const reportKey = (id: string, kind: "validation" | "citations") => ["draft", id, kind];

export const useDrafts = () => useQuery({ queryKey: ["drafts"], queryFn: async () => (await apiFetch<{ items: DraftSummary[] }>("/drafts")).items });
export const useDraft = (id: string) => useQuery({ queryKey: draftKey(id), enabled: !!id, queryFn: () => apiFetch<DraftDetail>(P(id)), retry: false });
export const useDraftVersion = (id: string, n: number | null) =>
  useQuery({ queryKey: ["draft", id, "version", n], enabled: n !== null, retry: false, queryFn: () => apiFetch<VersionBody>(`${P(id)}/versions/${seg(n!)}`) });

function useDraftMutation<V, R>(id: string | null, fn: (v: V) => Promise<R>, onOk?: (r: R, qc: ReturnType<typeof useQueryClient>) => void) {
  const qc = useQueryClient();
  return useMutation({
    meta: QUIET, mutationFn: fn,
    onSuccess: (r) => onOk?.(r, qc),
    onSettled: () => Promise.all([qc.invalidateQueries({ queryKey: ["drafts"] }), id ? qc.invalidateQueries({ queryKey: draftKey(id), exact: true }) : null]),
  });
}

export const useCreateDraft = () =>
  useDraftMutation(null, (b: { title: string; profile?: string }) => apiFetch<DraftSummary>("/drafts", { method: "POST", json: b }));
export const useDeleteDraft = () => useDraftMutation(null, (id: string) => apiFetch<void>(P(id), { method: "DELETE" }));
export const useSaveDraft = (id: string) =>
  useDraftMutation(id, (b: { content: string; base_version?: number }) => apiFetch<{ version: number; created_at: string }>(`${P(id)}/content`, { method: "PUT", json: b }));
export const useSetCoauthors = (id: string) =>
  useDraftMutation(id, (b: { users: string[]; groups: string[] }) => apiFetch<{ coauthors: DraftSummary["coauthors"] }>(`${P(id)}/coauthors`, { method: "PUT", json: b }));
export const useSubmitDraft = (id: string) => useDraftMutation(id, (b: SubmitBody) => apiFetch<SubmitResult>(`${P(id)}/submit`, { method: "POST", json: b }));

/** Reports have no GET: they live in the query cache, filled by the POST responses. */
export const useReport = <T extends Validation | Citations>(id: string, kind: "validation" | "citations") =>
  useQuery<T | null>({ queryKey: reportKey(id, kind), queryFn: () => null, enabled: false, staleTime: Infinity, gcTime: Infinity });
const useReportMutation = <T,>(id: string, kind: "validation" | "citations", path: string) =>
  useDraftMutation(id, () => apiFetch<T>(`${P(id)}/${path}`, { method: "POST" }), (r, qc) => qc.setQueryData(reportKey(id, kind), r));
export const useValidateDraft = (id: string) => useReportMutation<Validation>(id, "validation", "validate");
export const useCheckCitations = (id: string) => useReportMutation<Citations>(id, "citations", "check-citations");
export const useAckCitation = (id: string) =>
  useDraftMutation(id, (v: { key: string; sentence_hash: string; reason?: string }) =>
    apiFetch<Citations>(`${P(id)}/citations/ack`, v.reason === undefined ? { method: "DELETE", json: v } : { method: "POST", json: v }),
  (r, qc) => qc.setQueryData(reportKey(id, "citations"), r));

/** Synchronous build (up to ~120 s): returns the PDF bytes. */
export async function buildDraftPdf(id: string): Promise<Blob> {
  const res = await authedFetch(`${P(id)}/build`, { method: "POST" });
  if (!res.ok) throw await errorOf(res);
  return res.blob();
}

const CODES: Record<string, string> = {
  in_review: "This draft is already in review.", nothing_to_submit: "This version was already submitted. Save a change first.",
  checks_required: "Run validation and the citation check on the latest saved version first.", blocking_findings: "Fix the blocking findings first.",
  citations_unresolved: "Some citations are unresolved. Fix them first.", ai_disclosure_required: "Complete the AI-assistance disclosure.",
  not_in_review: "This draft is not in review.", draft_changed: "Someone saved a newer version.",
};
export const detailCode = (e: unknown) => {
  const d = e instanceof ApiError ? e.detail : null;
  return d && typeof d === "object" && "code" in d ? String((d as { code: unknown }).code) : null;
};
/** Readable text for string or `{code}` error details. */
export const draftError = (e: unknown) => {
  const c = detailCode(e);
  return c ? CODES[c] ?? c : e instanceof ApiError ? e.message : "That didn't work. Try again.";
};
