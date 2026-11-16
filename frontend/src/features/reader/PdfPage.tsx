import { useEffect, useRef, useState, type ReactNode } from "react";
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
  /** extra highlights, each with a margin marker button (e.g. anchored notes) */
  marks?: PageMark[];
  /** pick mode: called with the clicked point in unscaled points (top-left origin) and the page height */
  onPick?: (pt: { x: number; y: number }, pageHeight: number) => void;
}

export interface PageMark { key: string; bbox: Bbox; className?: string; label: string; icon: ReactNode; onSelect: () => void }

export function PdfPage({ pdf, pageNo, scale, base, highlight, marks, onPick }: PdfPageProps) {
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
  const seen = new Map<number, number>(); // stack markers that share a row
  const pick = onPick && ((e: React.MouseEvent<HTMLDivElement>) => {
    const r = e.currentTarget.getBoundingClientRect();
    onPick({ x: (e.clientX - r.left) / scale, y: (e.clientY - r.top) / scale }, size.height);
  });
  return (
    <div
      ref={wrap} className={onPick ? "pdfpage picking" : "pdfpage"} data-page={pageNo} style={{ width: size.width * scale, height: size.height * scale }}
      onClick={pick || undefined}
    >
      <canvas ref={canvas} style={{ width: "100%", height: "100%" }} aria-label={`Page ${pageNo}`} />
      {rect && highlight && (
        <div
          className="pdfhl" data-testid={`highlight-${highlight.chunkId}`}
          style={{ left: rect.x, top: rect.y, width: rect.w, height: rect.h }}
        />
      )}
      {marks?.map((m) => {
        const r = bboxToViewport(m.bbox, size.height, scale);
        const row = Math.round(r.y), n = seen.get(row) ?? 0;
        seen.set(row, n + 1);
        return (
          <div key={m.key} className={m.className}>
            <div className="pdfhl nhl" style={{ left: r.x, top: r.y, width: r.w, height: r.h }} />
            <button className="nmark" style={{ top: r.y + n * 26 }} aria-label={m.label} title={m.label} onClick={(e) => { e.stopPropagation(); m.onSelect(); }}>{m.icon}</button>
          </div>
        );
      })}
    </div>
  );
}
