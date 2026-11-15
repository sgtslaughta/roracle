import type { QueryClient } from "@tanstack/react-query";
import type { PdfDocLike } from "./pdfjs";

/** Free pdf.js worker memory when a cached PDF document query is removed (tab closed or evicted). */
export function attachPdfCleanup(qc: QueryClient): () => void {
  return qc.getQueryCache().subscribe((e) => {
    if (e.type === "removed" && e.query.queryKey[0] === "pdf") void (e.query.state.data as PdfDocLike | undefined)?.destroy?.();
  });
}
