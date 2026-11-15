import { useQuery } from "@tanstack/react-query";
import { apiFetch } from "@/api/client";
import type { CitedByItem, DoclingDoc, DocumentDetail, ReferenceItem } from "@/api/types";

export const useDoc = (id: string) => useQuery({ queryKey: ["doc", id], queryFn: () => apiFetch<DocumentDetail>(`/documents/${id}`) });
export const useReferences = (id: string) => useQuery({ queryKey: ["refs", id], queryFn: () => apiFetch<ReferenceItem[]>(`/documents/${id}/references`) });
export const useCitedBy = (id: string) => useQuery({ queryKey: ["cited-by", id], queryFn: () => apiFetch<CitedByItem[]>(`/documents/${id}/cited-by`) });
export const useDocling = (id: string, version: number | undefined) =>
  useQuery({
    queryKey: ["docling", id, version],
    enabled: version != null,
    staleTime: Infinity,
    queryFn: () => apiFetch<DoclingDoc>(`/documents/${id}/versions/${version}/docling`),
  });
