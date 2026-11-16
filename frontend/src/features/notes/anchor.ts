import { QUOTE_MAX, type NoteBbox } from "@/api/notes";
import type { Bbox, DoclingDoc } from "@/api/types";
import { bboxToViewport } from "@/features/reader/bbox";

/**
 * Note anchor box -> reader box. The backend (notes/anchor.py) stores a docling chunk box as
 * {x0: l, y0: t, x1: r, y1: b, coord_origin}, so this is a rename; a missing origin means docling's BOTTOMLEFT.
 */
export function noteBbox(b: NoteBbox): Bbox {
  return { l: b.x0, t: b.y0, r: b.x1, b: b.y1, coord_origin: b.coord_origin === "TOPLEFT" ? "TOPLEFT" : "BOTTOMLEFT" };
}

/** Reader (docling chunk) box -> note anchor box: inverse of `noteBbox`. */
export const anchorBbox = (b: Bbox): NoteBbox => ({ x0: b.l, y0: b.t, x1: b.r, y1: b.b, coord_origin: b.coord_origin });

type Chunk = DoclingDoc["chunks"][number];

/** The chunk on `page` containing `pt` (PDF points, top-left origin, as on screen at scale 1). */
export function chunkAt(chunks: Chunk[], page: number, pt: { x: number; y: number }, pageHeight: number): Chunk | null {
  return chunks.find((c) => {
    if (c.page_no !== page) return false;
    const r = bboxToViewport(c.bbox, pageHeight, 1);
    return pt.x >= r.x && pt.x <= r.x + r.w && pt.y >= r.y && pt.y <= r.y + r.h;
  }) ?? null;
}

/** pdf.js text item (getTextContent): `transform[4..5]` is the baseline origin in PDF user space (bottom-left). */
export interface TextItem { str?: string; transform?: number[] }

/** Text of the items whose origin lies in `bbox`, whitespace-collapsed and capped at the anchor quote limit. */
export function quoteIn(items: TextItem[], bbox: Bbox, pageHeight: number): string {
  const r = bboxToViewport(bbox, pageHeight, 1); // top-left rectangle
  const text = items
    .filter((it) => {
      if (!it.str || !it.transform) return false;
      const x = it.transform[4], y = pageHeight - it.transform[5];
      return x >= r.x && x <= r.x + r.w && y >= r.y && y <= r.y + r.h;
    })
    .map((it) => it.str)
    .join(" ");
  return text.replace(/\s+/g, " ").trim().slice(0, QUOTE_MAX);
}
