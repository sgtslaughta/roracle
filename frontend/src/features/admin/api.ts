import { keepPreviousData, useInfiniteQuery, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRef } from "react";
import { apiFetch, seg } from "@/api/client";
import type {
  AdminJob, Connector, ConnectorBody, ConnectorRun, ConnectorRunDetail, JobsSummary, Keyset, Paged, ReviewItem, ReviewSource,
} from "@/api/admin";

const POLL_MS = 3000;
/** Polling stops after this long even if a run still reports "running" (e.g. a crashed worker). */
const POLL_MAX_MS = 10 * 60_000;
const PAGE = 50;

/** Keyset paging: `?before_id=&limit=` -> `{items, next_before_id}`. */
const keyset = <T,>(path: string, extra: Record<string, string> = {}) => ({
  initialPageParam: undefined as number | undefined,
  getNextPageParam: (last: Keyset<T>) => last.next_before_id ?? undefined,
  queryFn: ({ pageParam }: { pageParam: number | undefined }) => {
    const sp = new URLSearchParams({ ...extra, limit: String(PAGE) });
    if (pageParam) sp.set("before_id", String(pageParam));
    return apiFetch<Keyset<T>>(`${path}?${sp}`);
  },
});

/** Polls every few seconds while `active(data)`, for a bounded time. */
function useBoundedPoll<D>(active: (d: D | undefined) => boolean) {
  const since = useRef<number | null>(null);
  return (data: D | undefined) => {
    if (!active(data)) { since.current = null; return false; }
    since.current ??= Date.now();
    return Date.now() - since.current < POLL_MAX_MS ? POLL_MS : false;
  };
}

export const useConnectors = () =>
  useQuery({ queryKey: ["admin", "connectors"], queryFn: () => apiFetch<{ items: Connector[] }>("/admin/connectors").then((r) => r.items) });

export const useConnector = (id: string | undefined) =>
  useQuery({ queryKey: ["admin", "connector", id], enabled: !!id, queryFn: () => apiFetch<Connector>(`/admin/connectors/${seg(id!)}`) });

/** Create (no id) or PATCH (id). */
export function useSaveConnector() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, body }: { id?: string; body: ConnectorBody | Partial<ConnectorBody> }) =>
      id ? apiFetch<Connector>(`/admin/connectors/${seg(id)}`, { method: "PATCH", json: body }) : apiFetch<Connector>("/admin/connectors", { method: "POST", json: body }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["admin"] }),
  });
}

export function useDeleteConnector() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => apiFetch<void>(`/admin/connectors/${seg(id)}`, { method: "DELETE" }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["admin"] }),
  });
}

/** connector id -> when Run now last succeeded; its runs list polls for a while so the queued run shows up. */
const queuedAt = new Map<string, number>();
const QUEUED_POLL_MS = 60_000;

export function useRunConnector() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => apiFetch<{ job_id: number }>(`/admin/connectors/${seg(id)}/run`, { method: "POST" }),
    onSuccess: (_r, id) => { queuedAt.set(id, Date.now()); return qc.invalidateQueries({ queryKey: ["admin"] }); },
  });
}

export function useRuns(id: string) {
  const interval = useBoundedPoll<{ pages: Keyset<ConnectorRun>[] }>((d) => !!d?.pages[0]?.items.some((r) => r.status === "running") || Date.now() - (queuedAt.get(id) ?? 0) < QUEUED_POLL_MS);
  return useInfiniteQuery({
    queryKey: ["admin", "runs", id],
    ...keyset<ConnectorRun>(`/admin/connectors/${seg(id)}/runs`),
    refetchInterval: (q) => interval(q.state.data),
  });
}

export function useRun(id: string, runId: number | null) {
  const interval = useBoundedPoll<ConnectorRunDetail>((d) => d?.status === "running");
  return useQuery({
    queryKey: ["admin", "run", id, runId],
    enabled: runId != null,
    queryFn: () => apiFetch<ConnectorRunDetail>(`/admin/connectors/${seg(id)}/runs/${runId}`),
    refetchInterval: (q) => interval(q.state.data),
  });
}

export const useJobsSummary = () => useQuery({ queryKey: ["admin", "jobs-summary"], queryFn: () => apiFetch<JobsSummary>("/admin/jobs/summary") });

export const useJobs = (status: string, kind: string) =>
  useInfiniteQuery({ queryKey: ["admin", "jobs", status, kind], ...keyset<AdminJob>("/admin/jobs", { ...(status && { status }), ...(kind && { kind }) }) });

export function useRetryJob() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: number) => apiFetch<AdminJob>(`/admin/jobs/${seg(id)}/retry`, { method: "POST" }),
    onSettled: () => qc.invalidateQueries({ queryKey: ["admin"] }),
  });
}

export function useRetryFailed() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (kind: string) => apiFetch<{ retried: number }>("/admin/jobs/retry-failed", { method: "POST", json: kind ? { kind } : {} }),
    onSettled: () => qc.invalidateQueries({ queryKey: ["admin"] }),
  });
}

export const REVIEW_PAGE = 20;
export const useReview = (source: ReviewSource, page: number) =>
  useQuery({
    queryKey: ["admin", "review", source, page],
    placeholderData: keepPreviousData,
    queryFn: () => apiFetch<Paged<ReviewItem>>(`/admin/review?source=${source}&page=${page}&limit=${REVIEW_PAGE}`),
  });
