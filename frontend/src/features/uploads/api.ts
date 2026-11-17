import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { ApiError, apiFetch, errorText, seg, QUIET } from "@/api/client";
import type { DuplicateDetail, Visibility, NearDupAction, NearDupResult, NeedsTitleItem, TitleResult, UploadCreated, UploadsResponse } from "@/api/types";

const TERMINAL_STAGES = ["searchable", "review", "needs_title", "rejected", "cancelled"];
/** An upload is terminal when it has finished, failed, or waits on the user. */
export const isTerminal = (u: { status: string; stage: string }) => u.status === "done" || u.status === "failed" || TERMINAL_STAGES.includes(u.stage);

export const UPLOADS_PAGE = 200;
/** The newest `limit` uploads (`total` says how many exist); grow `limit` to page. */
export const useUploads = (poll = true, limit = UPLOADS_PAGE) =>
  useQuery({
    queryKey: ["uploads", limit],
    queryFn: () => apiFetch<UploadsResponse>(`/me/uploads?limit=${limit}`),
    placeholderData: keepPreviousData,
    refetchInterval: (q) => (poll && q.state.data?.items.some((u) => !isTerminal(u)) ? 3000 : false),
  });

/** Count for the sidebar badge: uploads that need a decision or a title, or failed. */
export function useUploadAttention() {
  const { data } = useUploads();
  return (data?.items ?? []).filter((u) => u.status === "failed" || u.stage === "review" || u.stage === "needs_title" || u.stage === "rejected").length;
}

export const isDuplicate = (e: unknown): e is ApiError & { detail: DuplicateDetail } =>
  e instanceof ApiError && e.status === 409 && (e.detail as DuplicateDetail | undefined)?.code === "duplicate";

export function useUpload() {
  const qc = useQueryClient();
  return useMutation({
    meta: QUIET,
    mutationFn: ({ file, force, visibility, title, label_id }: { file: File; force?: boolean; visibility?: Visibility; title?: string; label_id?: string }) => {
      const form = new FormData();
      form.append("file", file);
      if (visibility) form.append("visibility", visibility);
      if (title?.trim()) form.append("title", title.trim());
      if (label_id) form.append("label_id", label_id);
      if (force) form.append("force", "true");
      return apiFetch<UploadCreated>("/documents", { method: "POST", body: form });
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ["uploads"] }),
  });
}

export function useNearDup() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ documentId, versionId, action }: { documentId: string; versionId: string; action: NearDupAction }) =>
      apiFetch<NearDupResult>(`/documents/${seg(documentId)}/versions/${seg(versionId)}/near-dup`, { method: "POST", json: { action } }),
    onSuccess: () => Promise.all([qc.invalidateQueries({ queryKey: ["uploads"] }), qc.invalidateQueries({ queryKey: ["admin", "review"] })]),
  });
}

export function useSetTitle() {
  const qc = useQueryClient();
  return useMutation({
    meta: QUIET,
    mutationFn: ({ documentId, versionId, title }: { documentId: string; versionId: string; title: string }) =>
      apiFetch<TitleResult>(`/documents/${seg(documentId)}/versions/${seg(versionId)}/title`, { method: "PUT", json: { title } }),
    onSuccess: () => {
      toast("Title saved. Processing continues.");
      return Promise.all([qc.invalidateQueries({ queryKey: ["uploads"] }), qc.invalidateQueries({ queryKey: ["needs-title"] })]);
    },
    onError: (e) => toast(errorText(e)),
  });
}

/** Admin-only queue of every upload waiting for a title (oldest first, unpaginated). */
export const useNeedsTitle = (enabled: boolean) =>
  useQuery({ queryKey: ["needs-title"], enabled, queryFn: () => apiFetch<NeedsTitleItem[]>("/admin/needs-title") });

/** Name an already-processed document that has no title (PATCH /documents/{id}). */
export function useRename() {
  const qc = useQueryClient();
  return useMutation({
    meta: QUIET,
    mutationFn: ({ documentId, title }: { documentId: string; title: string }) => apiFetch(`/documents/${seg(documentId)}`, { method: "PATCH", json: { title } }),
    onSuccess: (_d, { documentId }) => {
      toast("Title saved.");
      return Promise.all([["uploads"], ["doc", documentId], ["search"]].map((queryKey) => qc.invalidateQueries({ queryKey })));
    },
    onError: (e) => toast(errorText(e)),
  });
}
