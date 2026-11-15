import { useQuery } from "@tanstack/react-query";
import { apiFetch } from "@/api/client";
import type { UploadsResponse } from "@/api/types";

/** An upload is terminal when it has finished, failed, or waits on the user. */
export const isTerminal = (u: { status: string; stage: string }) => u.status === "done" || u.status === "failed" || u.stage === "review";

export const useUploads = (poll = true) =>
  useQuery({
    queryKey: ["uploads"],
    queryFn: () => apiFetch<UploadsResponse>("/me/uploads?limit=200"),
    refetchInterval: (q) => (poll && q.state.data?.items.some((u) => !isTerminal(u)) ? 3000 : false),
  });

/** Count for the sidebar badge: uploads that need a decision or failed. */
export function useUploadAttention() {
  const { data } = useUploads();
  return (data?.items ?? []).filter((u) => u.status === "failed" || u.stage === "review" || u.stage === "rejected").length;
}
