import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError, apiFetch, seg } from "@/api/client";
import type { AccessToken, CreateHandoffBody, CreatedToken, CreateTokenBody, HandoffManifest, HandoffRow, PatchHandoffBody } from "@/api/agents";

export const useTokens = () => useQuery({ queryKey: ["tokens"], queryFn: () => apiFetch<AccessToken[]>("/me/tokens") });

/** Plain call, not a mutation: the secret must never enter the query or mutation cache. */
export const createToken = (body: CreateTokenBody) => apiFetch<CreatedToken>("/me/tokens", { method: "POST", json: body });

export function useRevokeToken() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => apiFetch<void>(`/me/tokens/${seg(id)}`, { method: "DELETE" }),
    onSettled: () => qc.invalidateQueries({ queryKey: ["tokens"] }),
  });
}

export const useHandoffs = () => useQuery({ queryKey: ["handoffs"], queryFn: () => apiFetch<HandoffRow[]>("/handoffs") });

/** Loading a handoff is audited; 404 / 410 are final answers, so never retried. */
export const useHandoff = (code: string | undefined) =>
  useQuery({
    queryKey: ["handoff", code],
    queryFn: () => apiFetch<HandoffManifest>(`/handoffs/${seg(code!)}`),
    enabled: !!code,
    retry: (n, e) => !(e instanceof ApiError && e.status < 500) && n < 2,
  });

export function useCreateHandoff() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: CreateHandoffBody) => apiFetch<HandoffManifest>("/handoffs", { method: "POST", json: body }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["handoffs"] }),
  });
}

export function usePatchHandoff() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ code, patch }: { code: string; patch: PatchHandoffBody }) =>
      apiFetch<HandoffManifest>(`/handoffs/${seg(code)}`, { method: "PATCH", json: patch }),
    onSuccess: (m) => { qc.setQueryData(["handoff", m.code], m); return qc.invalidateQueries({ queryKey: ["handoffs"] }); },
  });
}

export function useRevokeHandoff() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (code: string) => apiFetch<void>(`/handoffs/${seg(code)}`, { method: "DELETE" }),
    onSettled: () => qc.invalidateQueries({ queryKey: ["handoffs"] }),
  });
}

/** Readable text for a 422: FastAPI sends a string, pydantic a list of {loc, msg}. */
export function invalidText(e: ApiError): string {
  if (typeof e.detail === "string") return e.detail;
  const first = Array.isArray(e.detail) ? (e.detail[0] as { loc?: unknown[]; msg?: string } | undefined) : undefined;
  if (first?.msg) {
    const field = Array.isArray(first.loc) ? first.loc.filter((x) => x !== "body").join(".") : "";
    return field ? `${field}: ${first.msg}` : first.msg;
  }
  return "Check the fields and try again.";
}
