import { useEffect, useState } from "react";
import { loadPdf, type PdfDocLike } from "@/features/reader/pdfjs";
import { PdfPage } from "@/features/reader/PdfPage";

/** Renders a built-PDF blob with the reader's page renderer (read-only, fit to a fixed width). */
export default function DraftPdf({ blob }: { blob: Blob }) {
  const [pdf, setPdf] = useState<PdfDocLike | null>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    let dead = false, doc: PdfDocLike | null = null;
    setPdf(null); setFailed(false);
    blob.arrayBuffer().then(loadPdf).then((d) => { doc = d; if (dead) void d.destroy?.(); else setPdf(d); }).catch(() => !dead && setFailed(true));
    return () => { dead = true; void doc?.destroy?.(); };
  }, [blob]);
  if (failed) return <p role="alert" className="derr">The PDF can't be previewed here. Use Download.</p>;
  if (!pdf) return <p className="sub" role="status">Loading preview…</p>;
  return (
    <div className="pages dpdf">
      {Array.from({ length: pdf.numPages }, (_, i) => <PdfPage key={i} pdf={pdf} pageNo={i + 1} scale={1} base={{ width: 612, height: 792 }} />)}
    </div>
  );
}
