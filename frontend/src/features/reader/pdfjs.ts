// pdf.js with its worker bundled by Vite (no CDN). Kept behind a tiny interface so tests can fake it.
import * as pdfjs from "pdfjs-dist";
import workerUrl from "pdfjs-dist/build/pdf.worker.min.mjs?url";

pdfjs.GlobalWorkerOptions.workerSrc = workerUrl;

export interface PdfViewport { width: number; height: number; scale: number }
export interface PdfPageLike {
  getViewport(o: { scale: number }): PdfViewport;
  render(o: { canvas: HTMLCanvasElement; canvasContext: CanvasRenderingContext2D; viewport: PdfViewport }): { promise: Promise<void> };
  /** text runs with their PDF-space origin (`transform[4..5]`); used to quote a picked passage */
  getTextContent?(): Promise<{ items: { str?: string; transform?: number[] }[] }>;
}
export interface PdfDocLike { numPages: number; getPage(n: number): Promise<PdfPageLike>; destroy?(): Promise<void> | void }

export async function loadPdf(data: ArrayBuffer): Promise<PdfDocLike> {
  return (await pdfjs.getDocument({ data }).promise) as unknown as PdfDocLike;
}
