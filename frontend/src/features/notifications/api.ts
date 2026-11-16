import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "@/api/client";
import type { AppNotification } from "./describe";

interface Page { items: AppNotification[]; next_before_id: number | null; unread: number }
const PAGE = 30;

/** Bell: latest 10 plus the unread count; polled every minute and on focus. */
export const useLatestNotifications = () =>
  useQuery({
    queryKey: ["notifications", "latest"],
    queryFn: () => apiFetch<Page>("/me/notifications?limit=10"),
    refetchInterval: 60_000,
    refetchOnWindowFocus: true,
  });

/** Full list, keyset paged by `before_id`. */
export const useNotificationList = (unreadOnly: boolean) =>
  useInfiniteQuery({
    queryKey: ["notifications", "list", unreadOnly],
    initialPageParam: null as number | null,
    queryFn: ({ pageParam }) => {
      const sp = new URLSearchParams({ limit: String(PAGE) });
      if (unreadOnly) sp.set("unread_only", "true");
      if (pageParam != null) sp.set("before_id", String(pageParam));
      return apiFetch<Page>(`/me/notifications?${sp}`);
    },
    getNextPageParam: (last) => last.next_before_id,
  });

export function useMarkRead() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { ids: number[] } | { all: true }) => apiFetch<{ updated: number }>("/me/notifications/read", { method: "POST", json: body }),
    onSettled: () => qc.invalidateQueries({ queryKey: ["notifications"] }),
  });
}
