import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiFetch, seg } from "@/api/client";
import type { Note, NoteAnchor, NoteKind, NoteRelation, NoteShare, NoteVisibility } from "@/api/notes";

/** P8 contract: an agent's note (or reply, when `parent_id` is set) waiting for a human decision. Untrusted text. */
export interface ProposalPayload {
  document_id: string;
  parent_id?: string;
  kind?: NoteKind;
  body: string;
  anchor?: NoteAnchor | null;
  relation?: NoteRelation | null;
  visibility?: NoteVisibility;
  shares?: NoteShare[];
}
export type ProposalStatus = "pending" | "approved" | "rejected";
export interface Proposal {
  id: string;
  kind: "note" | "relation";
  /** the agent client name from the access token */
  client: string;
  payload: ProposalPayload;
  status: ProposalStatus;
  annotation_id: string | null;
  created_at: string;
  decided_at: string | null;
  /** null when the document is gone or no longer readable */
  document: { id: string; title: string | null } | null;
}
export interface ApproveBody { body?: string; visibility?: NoteVisibility; shares?: NoteShare[] }
export interface BulkResult { approved: { proposal_id: string; annotation_id: string }[]; failed: { proposal_id: string; detail: string }[] }

export const BULK_MAX = 100;

/** Unpaged, newest first, at most 200 rows; `total` counts them all. */
export const useProposals = (status: ProposalStatus) =>
  useQuery({
    queryKey: ["proposals", status],
    queryFn: () => apiFetch<{ total: number; items: Proposal[] }>(`/me/proposals?status=${status}`),
    refetchOnWindowFocus: true,
    refetchInterval: status === "pending" ? 60_000 : false,
  });

/** Every decision refreshes all proposal lists (the sidebar badge too) the reader's notes and search. */
function useDecision<V, R>(fn: (v: V) => Promise<R>) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: fn,
    onSettled: () => Promise.all([qc.invalidateQueries({ queryKey: ["proposals"] }), qc.invalidateQueries({ queryKey: ["notes"] }), qc.invalidateQueries({ queryKey: ["search"] })]),
  });
}

export const useApprove = () =>
  useDecision(({ id, body }: { id: string; body: ApproveBody }) => apiFetch<Note>(`/proposals/${seg(id)}/approve`, { method: "POST", json: body }));
export const useReject = () => useDecision((id: string) => apiFetch<void>(`/proposals/${seg(id)}/reject`, { method: "POST" }));

/** Bulk approve in chunks of 100; a failed request counts every id in its chunk as failed. */
export const useBulkApprove = () =>
  useDecision(async (ids: string[]) => {
    const out: BulkResult = { approved: [], failed: [] };
    for (let i = 0; i < ids.length; i += BULK_MAX) {
      const chunk = ids.slice(i, i + BULK_MAX);
      try {
        const r = await apiFetch<BulkResult>("/proposals/bulk-approve", { method: "POST", json: { ids: chunk } });
        out.approved.push(...r.approved);
        out.failed.push(...r.failed);
      } catch {
        out.failed.push(...chunk.map((proposal_id) => ({ proposal_id, detail: "The request failed. Try again." })));
      }
    }
    return out;
  });
