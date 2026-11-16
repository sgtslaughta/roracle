import { keepPreviousData, useInfiniteQuery, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiFetch, seg } from "@/api/client";
import type { AdminHealth, AdminStats, AdminUser, AuditEntry, GroupCount, Keyset, Paged, SettingsMap, SettingValue, TagCount } from "@/api/admin";

export const USERS_PAGE = 20;
export const AUDIT_PAGE = 50;

export const useSettings = () => useQuery({ queryKey: ["admin", "settings"], queryFn: () => apiFetch<SettingsMap>("/admin/settings") });

/** `value: null` removes the override. */
export function useSaveSetting() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ key, value }: { key: string; value: SettingValue | null }) => apiFetch<SettingsMap>("/admin/settings", { method: "PUT", json: { [key]: value } }),
    onSuccess: (data) => qc.setQueryData(["admin", "settings"], data),
  });
}

export const useTags = () => useQuery({ queryKey: ["admin", "tags"], queryFn: async () => (await apiFetch<{ items: TagCount[]; total: number }>("/admin/tags")).items });

export function useMergeTags() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { sources: string[]; target: string }) => apiFetch<{ documents: number }>("/admin/tags/merge", { method: "POST", json: body }),
    onSuccess: () => Promise.all(["admin", "search", "doc"].map((k) => qc.invalidateQueries({ queryKey: k === "admin" ? ["admin", "tags"] : [k] }))),
  });
}

export const useAudit = (action: string, userId: string) =>
  useInfiniteQuery({
    queryKey: ["admin", "audit", action, userId],
    initialPageParam: undefined as number | undefined,
    getNextPageParam: (last: Keyset<AuditEntry>) => last.next_before_id ?? undefined,
    queryFn: ({ pageParam }) => {
      const sp = new URLSearchParams({ limit: String(AUDIT_PAGE) });
      if (action) sp.set("action", action);
      if (userId) sp.set("user_id", userId);
      if (pageParam) sp.set("before_id", String(pageParam));
      return apiFetch<Keyset<AuditEntry>>(`/admin/audit?${sp}`);
    },
  });

export const useUsers = (q: string, page: number) =>
  useQuery({
    queryKey: ["admin", "users", q, page],
    placeholderData: keepPreviousData,
    queryFn: () => apiFetch<Paged<AdminUser>>(`/admin/users?${new URLSearchParams({ q, page: String(page), limit: String(USERS_PAGE) })}`),
  });

export const useGroups = () => useQuery({ queryKey: ["admin", "groups"], queryFn: async () => (await apiFetch<{ items: GroupCount[]; total: number }>("/admin/groups")).items });

export const useRevokePats = () => useMutation({ mutationFn: (id: string) => apiFetch<void>(`/admin/users/${seg(id)}/pats`, { method: "DELETE" }) });

export const useStats = () => useQuery({ queryKey: ["admin", "stats"], queryFn: () => apiFetch<AdminStats>("/admin/stats") });

/** Refetches every 30 s while mounted; a failed request is an error, not stale data. */
export const useHealth = () =>
  useQuery({ queryKey: ["admin", "health"], queryFn: () => apiFetch<AdminHealth>("/admin/health"), refetchInterval: 30_000, retry: false });
