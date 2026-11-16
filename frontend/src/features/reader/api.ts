import { useQuery } from "@tanstack/react-query";
import { apiFetch, seg } from "@/api/client";
import type { CitedByItem, DoclingDoc, DocumentDetail, ReferenceItem } from "@/api/types";

export const useDoc = (id: string) => useQuery({ queryKey: ["doc", id], queryFn: () => apiFetch<DocumentDetail>(`/documents/${seg(id)}`) });
export const useReferences = (id: string) => useQuery({ queryKey: ["refs", id], queryFn: () => apiFetch<ReferenceItem[]>(`/documents/${seg(id)}/references`) });
export const useCitedBy = (id: string) => useQuery({ queryKey: ["cited-by", id], queryFn: () => apiFetch<CitedByItem[]>(`/documents/${seg(id)}/cited-by`) });
export const useDocling = (id: string, version: number | undefined) =>
  useQuery({
    queryKey: ["docling", id, version],
    enabled: version != null,
    staleTime: Infinity,
    queryFn: () => apiFetch<DoclingDoc>(`/documents/${seg(id)}/versions/${seg(version!)}/docling`),
  });

/** Authed download of a version's original file via a temporary blob link. */
export async function downloadOriginal(docId: string, version: number, filename: string) {
  const blob = await apiFetch<Blob>(`/documents/${seg(docId)}/versions/${seg(version)}/file`);
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = filename;
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}
