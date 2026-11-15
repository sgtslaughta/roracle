import { useQuery } from "@tanstack/react-query";
import { apiFetch } from "@/api/client";
import type { Me } from "@/api/types";

export const useMe = () => useQuery({ queryKey: ["me"], queryFn: () => apiFetch<Me>("/me"), staleTime: 5 * 60_000 });

/** Tag removal is allowed for the document owner and admins (P3). */
export function useCanRemoveTags(ownerId: string | undefined) {
  const { data: me } = useMe();
  return !!me && (me.is_admin || (!!ownerId && me.id === ownerId));
}
