// Tiny PDF writer for the mock API: one Helvetica text PDF per sample document, so the reader renders real PDF.js output.
// Layout is deterministic, so chunk boxes (Docling-style, BOTTOMLEFT origin, PDF points) line up with the drawn text.
import type { Bbox, DoclingDoc } from "@/api/types";
import { DOCS, type MockDoc } from "./docs";
import { chunkId } from "./search";

export const PAGE = { w: 612, h: 792 };
const M = { left: 64, right: 64, top: 72 }, SIZE = 11, LEAD = 15, TITLE = 17;
const MAXC = Math.floor((PAGE.w - M.left - M.right) / (SIZE * 0.5));

const ASCII: Record<string, string> = { "≥": ">=", "≤": "<=", "−": "-", "–": "-", "—": "-", "λ": "lambda", "₂": "_2", "₁": "_1", "√": "sqrt", "²": "^2", "·": ".", "⁻": "-", "⁸": "8", "⁶": "6", "⁴": "4", "³": "^3", "¹": "1", "⁰": "0", "≈": "~", "“": '"', "”": '"', "’": "'", "‖": "||", "γ": "gamma", "κ": "kappa", "ε": "eps", "∈": " in ", "…": "...", "×": "x", "ʳ": "^r", "ᵣ": "r" };
const ascii = (s: string) => s.replace(/[^\x20-\x7e]/g, (c) => ASCII[c] ?? "");
const esc = (s: string) => ascii(s).replace(/[\\()]/g, "\\$&");

function wrap(text: string): string[] {
  const out: string[] = [];
  let line = "";
  for (const w of ascii(text).split(/\s+/)) {
    if ((line + " " + w).trim().length > MAXC) { out.push(line); line = w; } else line = (line + " " + w).trim();
  }
  if (line) out.push(line);
  return out;
}

interface Laid { pages: { ops: string[]; chunks: { id: string; bbox: Bbox }[] }[] }
function layout(d: MockDoc): Laid {
  const pages: Laid["pages"] = [];
  d.pages.forEach((paras, p) => {
    const ops: string[] = [], chunks: Laid["pages"][number]["chunks"] = [];
    let y = PAGE.h - M.top; // PDF y grows upward
    const text = (s: string, size: number, x = M.left, font = "F1") => ops.push(`BT /${font} ${size} Tf ${x} ${y} Td (${esc(s)}) Tj ET`);
    if (p === 0) {
      for (const l of wrap(d.title)) { text(l, TITLE, M.left, "F2"); y -= TITLE + 5; }
      text(d.authors.join(", "), SIZE, M.left, "F1"); y -= LEAD * 2;
    }
    paras.forEach((para, c) => {
      const lines = wrap(para);
      const top = y + SIZE; // top of first line
      lines.forEach((l) => { text(l, SIZE); y -= LEAD; });
      const bottom = y + LEAD - 4;
      chunks.push({ id: chunkId(d, p, c), bbox: { l: M.left - 4, r: PAGE.w - M.right + 4, t: top + 2, b: bottom, coord_origin: "BOTTOMLEFT" } });
      y -= LEAD * 0.7;
    });
    ops.push(`BT /F1 9 Tf ${PAGE.w / 2 - 4} 40 Td (${p + 1}) Tj ET`);
    pages.push({ ops, chunks });
  });
  return { pages };
}

export function docling(d: MockDoc): DoclingDoc {
  const l = layout(d);
  return {
    pages: Object.fromEntries(l.pages.map((_, i) => [String(i + 1), { size: { width: PAGE.w, height: PAGE.h } }])),
    chunks: l.pages.flatMap((pg, i) => pg.chunks.map((c) => ({ chunk_id: c.id, page_no: i + 1, bbox: c.bbox }))),
  };
}

export function buildPdf(d: MockDoc): Uint8Array {
  const { pages } = layout(d);
  const n = pages.length;
  // objects: 1 catalog, 2 pages, 3 F1, 4 F2, then per page: page obj + content obj
  const objs: string[] = [
    "<< /Type /Catalog /Pages 2 0 R >>",
    `<< /Type /Pages /Kids [${pages.map((_, i) => `${5 + i * 2} 0 R`).join(" ")}] /Count ${n} >>`,
    "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold >>",
  ];
  pages.forEach((pg, i) => {
    const content = pg.ops.join("\n");
    objs.push(`<< /Type /Page /Parent 2 0 R /MediaBox [0 0 ${PAGE.w} ${PAGE.h}] /Resources << /Font << /F1 3 0 R /F2 4 0 R >> >> /Contents ${6 + i * 2} 0 R >>`);
    objs.push(`<< /Length ${content.length} >>\nstream\n${content}\nendstream`);
  });
  let out = "%PDF-1.4\n";
  const offs: number[] = [];
  objs.forEach((o, i) => { offs.push(out.length); out += `${i + 1} 0 obj\n${o}\nendobj\n`; });
  const xref = out.length;
  out += `xref\n0 ${objs.length + 1}\n0000000000 65535 f \n${offs.map((o) => `${String(o).padStart(10, "0")} 00000 n \n`).join("")}`;
  out += `trailer\n<< /Size ${objs.length + 1} /Root 1 0 R >>\nstartxref\n${xref}\n%%EOF\n`;
  return Uint8Array.from(out, (c) => c.charCodeAt(0));
}

export const docById = (id: string) => DOCS.find((d) => d.id === id);
