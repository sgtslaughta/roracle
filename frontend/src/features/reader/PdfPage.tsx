import { useEffect, useRef, useState } from "react";
import type { Bbox } from "@/api/types";
import { bboxToViewport } from "./bbox";
import type { PdfDocLike } from "./pdfjs";

export interface PdfPageProps {
  pdf: PdfDocLike;
  pageNo: number;
  scale: number;
  /** unscaled page size in points (from page 1 until this page renders) */
  base: { width: number; height: number };
  highlight?: { chunkId: string; bbox: Bbox } | null;
}

export function PdfPage({ pdf, pageNo, scale, base, highlight }: PdfPageProps) {
  const wrap = useRef<HTMLDivElement>(null);
  const canvas = useRef<HTMLCanvasElement>(null);
  const [visible, setVisible] = useState(false);
  const [size, setSize] = useState(base); // actual page size in points once known

  useEffect(() => {
    const el = wrap.current;
    if (!el) return;
    const io = new IntersectionObserver((es) => es.forEach((e) => e.isIntersecting && setVisible(true)), { rootMargin: "600px 0px" });
    io.observe(el);
    return () => io.disconnect();
  }, []);

  useEffect(() => {
    if (!visible) return;
    let cancelled = false;
    (async () => {
      const page = await pdf.getPage(pageNo);
      if (cancelled) return;
      const pts = page.getViewport({ scale: 1 });
      setSize({ width: pts.width, height: pts.height });
      const c = canvas.current, ctx = c?.getContext("2d");
      if (!c || !ctx) return;
      const dpr = window.devicePixelRatio || 1;
      const vp = page.getViewport({ scale: scale * dpr });
      c.width = Math.floor(vp.width); c.height = Math.floor(vp.height);
      await page.render({ canvas: c, canvasContext: ctx, viewport: vp }).promise;
    })().catch(() => { /* render cancelled or failed: leave the placeholder */ });
    return () => { cancelled = true; };
  }, [visible, pdf, pageNo, scale]);

  const rect = highlight ? bboxToViewport(highlight.bbox, size.height, scale) : null;
  return (
    <div ref={wrap} className="pdfpage" data-page={pageNo} style={{ width: size.width * scale, height: size.height * scale }}>
      <canvas ref={canvas} style={{ width: "100%", height: "100%" }} aria-label={`Page ${pageNo}`} />
      {rect && highlight && (
        <div
          className="pdfhl" data-testid={`highlight-${highlight.chunkId}`}
          style={{ left: rect.x, top: rect.y, width: rect.w, height: rect.h }}
        />
      )}
    </div>
  );
}
