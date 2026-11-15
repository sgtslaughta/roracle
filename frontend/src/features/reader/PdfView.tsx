import { useQuery } from "@tanstack/react-query";
import { Minus, Plus } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { apiFetch, seg } from "@/api/client";
import type { Bbox } from "@/api/types";
import { useDocling } from "./api";
import { loadPdf } from "./pdfjs";
import { PdfPage } from "./PdfPage";
import type { ReaderTab } from "./useReaderStore";

const ZOOMS = [0.6, 0.8, 1, 1.25, 1.6, 2];

export default function PdfView({ tab, version }: { tab: ReaderTab; version: number }) {
  const box = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(0);
  const [zoom, setZoom] = useState(2); // index into ZOOMS: 1.0 = fit width
  const [current, setCurrent] = useState(1);

  const pdf = useQuery({
    queryKey: ["pdf", tab.docId, version],
    staleTime: Infinity,
    gcTime: 60_000, // documents are destroyed when the query is garbage collected (see pdfCleanup)
    queryFn: async () => loadPdf(await (await apiFetch<Blob>(`/documents/${seg(tab.docId)}/versions/${seg(version)}/file`)).arrayBuffer()),
  });
  const docling = useDocling(tab.docId, version).data;

  useEffect(() => {
    const el = box.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setWidth(el.clientWidth));
    ro.observe(el);
    setWidth(el.clientWidth);
    return () => ro.disconnect();
  }, []);

  const [base, setBase] = useState({ width: 612, height: 792 });
  useEffect(() => {
    void pdf.data?.getPage(1).then((p) => { const v = p.getViewport({ scale: 1 }); setBase({ width: v.width, height: v.height }); });
  }, [pdf.data]);

  const scale = (width > 40 ? (width - 36) / base.width : 1) * ZOOMS[zoom];

  const target = useMemo<{ chunkId: string; bbox: Bbox; page: number } | null>(() => {
    if (!tab.chunkId) return null;
    const c = docling?.chunks.find((x) => x.chunk_id === tab.chunkId);
    if (c) return { chunkId: c.chunk_id, bbox: c.bbox, page: c.page_no };
    return tab.bbox ? { chunkId: tab.chunkId, bbox: tab.bbox, page: tab.page } : null;
  }, [tab.chunkId, tab.bbox, tab.page, docling]);

  // scroll to the passage (or page) when the tab is (re)opened and layout is ready
  useEffect(() => {
    if (!pdf.data) return;
    const sel = target ? `.pdfhl` : `[data-page="${tab.page}"]`;
    const id = requestAnimationFrame(() => box.current?.querySelector(sel)?.scrollIntoView({ block: target ? "center" : "start" }));
    return () => cancelAnimationFrame(id);
  // eslint-disable-next-line react-hooks/exhaustive-deps -- re-scroll only when the tab is re-opened or the passage changes, never on zoom/resize
  }, [pdf.data, tab.seq, tab.page, target?.chunkId]);

  useEffect(() => {
    const el = box.current;
    if (!el || !pdf.data) return;
    const io = new IntersectionObserver((es) => es.forEach((e) => e.isIntersecting && setCurrent(Number((e.target as HTMLElement).dataset.page))), { root: el, threshold: 0.5 });
    el.querySelectorAll("[data-page]").forEach((p) => io.observe(p));
    return () => io.disconnect();
  }, [pdf.data, scale]);

  if (pdf.isError) return <div className="pages"><div className="empty" role="alert"><p>This version can't be previewed. Use the download button to open the original.</p></div></div>;
  if (!pdf.data) return <div className="pages"><p className="sub" role="status">Loading document…</p></div>;

  return (
    <div className="pages" ref={box}>
      {Array.from({ length: pdf.data.numPages }, (_, i) => (
        <PdfPage key={i + 1} pdf={pdf.data} pageNo={i + 1} scale={scale} base={base} highlight={target && target.page === i + 1 ? target : null} />
      ))}
      <div className="pgind" role="status">
        <button className="icon-btn" aria-label="Zoom out" disabled={zoom === 0} onClick={() => setZoom(zoom - 1)}><Minus aria-hidden /></button>
        Page {current} of {pdf.data.numPages}
        <button className="icon-btn" aria-label="Zoom in" disabled={zoom === ZOOMS.length - 1} onClick={() => setZoom(zoom + 1)}><Plus aria-hidden /></button>
      </div>
    </div>
  );
}
