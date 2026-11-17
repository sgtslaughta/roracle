import { MutationCache, QueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { errorText } from "@/api/client";

/** App-wide client: a failed mutation toasts a readable message unless it set `meta: QUIET` (its caller reports it). */
export function createQueryClient() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: 1, staleTime: 15_000 } },
    mutationCache: new MutationCache({
      onError: (e, _v, _c, m) => { if (!m.meta?.quiet) toast(errorText(e)); },
    }),
  });
  return queryClient;
}
