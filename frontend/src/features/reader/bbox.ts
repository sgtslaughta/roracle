import type { Bbox } from "@/api/types";

export interface Rect { x: number; y: number; w: number; h: number }

/**
 * Docling boxes are in PDF points. Docling's origin is BOTTOMLEFT (y grows upward), while PDF.js viewport
 * coordinates are TOP-left (y grows downward). `pageHeight` is the unscaled page height in points.
 */
export function bboxToViewport(bbox: Bbox, pageHeight: number, scale: number): Rect {
  const w = Math.abs(bbox.r - bbox.l);
  const h = Math.abs(bbox.t - bbox.b);
  const left = Math.min(bbox.l, bbox.r);
  // BOTTOMLEFT: the top edge is the larger y; its distance from the page top is pageHeight - max(t, b)
  const top = bbox.coord_origin === "BOTTOMLEFT" ? pageHeight - Math.max(bbox.t, bbox.b) : Math.min(bbox.t, bbox.b);
  return { x: left * scale, y: top * scale, w: w * scale, h: h * scale };
}
