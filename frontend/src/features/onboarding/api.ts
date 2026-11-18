import { useQuery, type QueryClient } from "@tanstack/react-query";
import { apiFetch } from "@/api/client";
import { getQueryClient } from "@/app/queryClient";

export type Onboarding = { steps: string[]; dismissed: boolean; pats_enabled: boolean };

export const useOnboarding = () =>
  useQuery({ queryKey: ["onboarding"], queryFn: () => apiFetch<Onboarding>("/me/onboarding"), refetchOnWindowFocus: true });

/** Hide (true) or bring back (false) the Get started card. `qc` defaults to the app client. */
export async function setDismissed(dismissed: boolean, qc: QueryClient | undefined = getQueryClient()): Promise<void> {
  await apiFetch("/me/onboarding", { method: "PUT", json: { dismissed } });
  await qc?.invalidateQueries({ queryKey: ["onboarding"] });
}

export const reopenCard = () => setDismissed(false);
