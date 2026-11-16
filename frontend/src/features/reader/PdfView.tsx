import { useQuery } from "@tanstack/react-query";
import { Minus, Plus } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { toast } from "sonner";
import { ApiError, apiFetch, seg } from "@/api/client";
import type { Bbox } from "@/api/types";
import { Button } from "@/components/ui/button";
import { chunkAt, noteBbox, quoteIn } from "@/features/notes/anchor";
import { notesUi, useNotes } from "@/features/notes/api";
import { KIND_ICON, KIND_LABEL } from "@/features/notes/labels";
import { useDocling } from "./api";
import { loadPdf } from "./pdfjs";
import { TextFallback } from "./TextFallback";
import { PdfPage, type PageMark } from "./PdfPage";
import { setView, type ReaderTab } from "./useReaderStore";

const ZOOMS = [0.6, 0.8, 1, 1.25, 1.6, 2];

export default function PdfView({ tab, version, filename, onDownload }: { tab: ReaderTab; version: number; filename: string; onDownload: () => void }) {
  const box = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(0);
  const [zoom, setZoom] = useState(2); // index into ZOOMS: 1.0 = fit width
  const [current, setCurrent] = useState(1);

  const pdf = useQuery({
    queryKey: ["pdf", tab.docId, version],
    staleTime: Infinity,
    gcTime: 60_000, // documents are destroyed when the query is garbage collected (see pdfCleanup)
    queryFn: async () => loadPdf(await (await apiFetch<Blob>(`/documents/${seg(tab.docId)}/versions/${seg(version)}/pdf`)).arrayBuffer()),
  });
  const docling = useDocling(tab.docId, version).data;
  const notes = useNotes(tab.docId).data;
  const adding = notesUi.use((s) => s.adding === tab.docId);

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

  useEffect(() => { notesUi.set((s) => ({ ...s, page: current })); }, [current]);

  // "Add note" mode: Esc cancels before the reader's own Esc handling (capture phase)
  useEffect(() => {
    if (!adding) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape" || document.querySelector('[role="dialog"]') || (e.target as Element | null)?.closest?.('[role="dialog"]')) return;
      e.preventDefault(); e.stopPropagation();
      notesUi.set((s) => ({ ...s, adding: null }));
    };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [adding]);

  // anchored notes on this version: highlight + margin marker
  const marks = useMemo(() => {
    const by = new Map<number, PageMark[]>();
    for (const n of notes ?? []) {
      const a = n.anchor;
      if (n.parent_id || n.version !== version || a?.page == null || !a.bbox) continue;
      const Icon = KIND_ICON[n.kind];
      by.set(a.page, [...(by.get(a.page) ?? []), {
        key: n.id, bbox: noteBbox(a.bbox), className: `t-${n.kind}`, label: `Note by ${n.author.username}: ${KIND_LABEL[n.kind]}`,
        icon: <Icon aria-hidden />, onSelect: () => { notesUi.set((s) => ({ ...s, flash: n.id })); setView("notes"); },
      }]);
    }
    return by;
  }, [notes, version]);

  const pick = async (pageNo: number, pt: { x: number; y: number }, h: number) => {
    const c = docling && chunkAt(docling.chunks, pageNo, pt, h);
    if (!c) { toast("No passage there. Click on the text you want to note, or press Esc."); return; }
    let quote = "";
    try {
      const page = await pdf.data!.getPage(pageNo);
      if (page.getTextContent) quote = quoteIn((await page.getTextContent()).items, c.bbox, h);
    } catch { /* no text layer: the composer asks for the quote */ }
    notesUi.set((s) => ({ ...s, adding: null, draft: { docId: tab.docId, version, quote, page: pageNo, bbox: c.bbox } }));
    setView("notes");
  };

  if (pdf.error instanceof ApiError && pdf.error.status === 404) return <TextFallback docId={tab.docId} version={version} filename={filename} onDownload={onDownload} />;
  if (pdf.isError) return <div className="pages"><div className="empty" role="alert"><p>This version can't be previewed. Use the download button to open the original.</p></div></div>;
  if (!pdf.data) return <div className="pages"><p className="sub" role="status">Loading document…</p></div>;

  return (
    <div className="pages" ref={box}>
      {adding && (
        <div className="pickbar">
          <span role="status">Click the passage you want to note. Esc cancels.</span>
          <Button variant="ghost" onClick={() => {
            notesUi.set((s) => ({ ...s, adding: null, draft: { docId: tab.docId, version, quote: "", page: current, bbox: null } }));
            setView("notes");
          }}>Write without picking</Button>
          <Button variant="ghost" onClick={() => notesUi.set((s) => ({ ...s, adding: null }))}>Cancel</Button>
        </div>
      )}
      {Array.from({ length: pdf.data.numPages }, (_, i) => (
        <PdfPage
          key={i + 1} pdf={pdf.data} pageNo={i + 1} scale={scale} base={base} highlight={target && target.page === i + 1 ? target : null}
          marks={marks.get(i + 1)} onPick={adding ? (pt, h) => void pick(i + 1, pt, h) : undefined}
        />
      ))}
      <div className="pgind" role="status">
        <button className="icon-btn" aria-label="Zoom out" disabled={zoom === 0} onClick={() => setZoom(zoom - 1)}><Minus aria-hidden /></button>
        Page {current} of {pdf.data.numPages}
        <button className="icon-btn" aria-label="Zoom in" disabled={zoom === ZOOMS.length - 1} onClick={() => setZoom(zoom + 1)}><Plus aria-hidden /></button>
      </div>
    </div>
  );
}
