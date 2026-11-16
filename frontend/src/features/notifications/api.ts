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

type Body = { ids: number[] } | { all: true };
const markPage = (p: Page, b: Body): Page => {
  const hit = (n: AppNotification) => !n.read_at && ("all" in b || b.ids.includes(Number(n.id)));
  const items = p.items.map((n) => (hit(n) ? { ...n, read_at: new Date().toISOString() } : n));
  return { ...p, items, unread: Math.max(0, p.unread - p.items.filter(hit).length) };
};

/** Optimistic: rows read at once, rolled back if the POST fails (the global handler toasts). */
export function useMarkRead() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: Body) => apiFetch<{ updated: number }>("/me/notifications/read", { method: "POST", json: body }),
    onMutate: async (b) => {
      await qc.cancelQueries({ queryKey: ["notifications"] });
      const prev = qc.getQueriesData({ queryKey: ["notifications"] });
      qc.setQueriesData<Page | { pages: Page[]; pageParams: unknown[] }>({ queryKey: ["notifications"] }, (d) =>
        !d ? d : "pages" in d ? { ...d, pages: d.pages.map((p) => markPage(p, b)) } : markPage(d, b));
      return { prev };
    },
    onError: (_e, _b, ctx) => ctx?.prev.forEach(([k, d]) => qc.setQueryData(k, d)),
    onSettled: () => qc.invalidateQueries({ queryKey: ["notifications"] }),
  });
}
