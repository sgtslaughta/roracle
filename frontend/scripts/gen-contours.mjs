// Marching-squares contour textures (ported from the approved prototype).
// A terrain field (gaussian hills + soft waves, fixed seeds) is traced into 30 iso-levels; line
// opacity fades with distance from an anchor corner. The SVGs are used as CSS masks coloured by --contour.
// Usage: node scripts/gen-contours.mjs [--out <dir>]   (default: public/)
import { mkdirSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

export const LEVELS = 30;
const N = 96;
const SIZE = 800;

export function contourSvg(hills, ax, ay, seed) {
  const S = SIZE / N;
  const F = (x, y) => {
    let v = 0.05 * Math.sin(x * 9 + y * 4 + seed) * Math.cos(y * 7 - x * 3) + 0.03 * Math.sin(x * 21 - y * 17 + seed * 2);
    for (const [hx, hy, h, s] of hills) v += h * Math.exp(-((x - hx) ** 2 + (y - hy) ** 2) / (2 * s * s));
    return v;
  };
  const g = [];
  let mn = Infinity, mx = -Infinity;
  for (let j = 0; j <= N; j++) {
    g[j] = [];
    for (let i = 0; i <= N; i++) {
      const v = F(i / N, j / N);
      g[j][i] = v; mn = Math.min(mn, v); mx = Math.max(mx, v);
    }
  }
  const B = [[], [], [], [], []];
  for (let l = 1; l < LEVELS; l++) {
    const L = mn + ((mx - mn) * l) / LEVELS;
    for (let j = 0; j < N; j++) for (let i = 0; i < N; i++) {
      const a = g[j][i], b = g[j][i + 1], c = g[j + 1][i + 1], d = g[j + 1][i], pts = [];
      const e = (v1, v2, x1, y1, x2, y2) => {
        const t = (L - v1) / (v2 - v1);
        pts.push([(x1 + (x2 - x1) * t) * S, (y1 + (y2 - y1) * t) * S]);
      };
      if ((a < L) !== (b < L)) e(a, b, i, j, i + 1, j);
      if ((b < L) !== (c < L)) e(b, c, i + 1, j, i + 1, j + 1);
      if ((c < L) !== (d < L)) e(c, d, i + 1, j + 1, i, j + 1);
      if ((d < L) !== (a < L)) e(d, a, i, j + 1, i, j);
      for (let k = 0; k + 1 < pts.length; k += 2) {
        const [x1, y1] = pts[k], [x2, y2] = pts[k + 1];
        const bk = Math.min(4, Math.floor(Math.hypot(x1 / SIZE - ax, y1 / SIZE - ay) / 0.2));
        B[bk].push(`M${x1.toFixed(1)} ${y1.toFixed(1)}L${x2.toFixed(1)} ${y2.toFixed(1)}`);
      }
    }
  }
  const op = [1, 0.7, 0.45, 0.25, 0.1];
  const paths = B.map((b, k) => (b.length ? `<path stroke-opacity="${op[k]}" d="${b.join("")}"/>` : "")).join("");
  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${SIZE} ${SIZE}" fill="none" stroke="#000" stroke-width="1.1" stroke-linecap="round">${paths}</svg>\n`;
}

export const TEXTURES = {
  "contour-main.svg": () => contourSvg([[.8,.2,1,.15],[.56,.1,.6,.09],[.92,.5,.75,.11],[.64,.4,.45,.07],[.72,.28,-.35,.05],[.42,.26,.3,.06]], .85, .15, 1.7),
  "contour-side.svg": () => contourSvg([[.22,.8,1,.16],[.45,.92,.55,.08],[.1,.55,.6,.1],[.32,.66,-.3,.05]], .1, .95, 4.2),
};

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const i = process.argv.indexOf("--out");
  const out = i > 0 ? resolve(process.argv[i + 1]) : join(dirname(fileURLToPath(import.meta.url)), "..", "public");
  mkdirSync(out, { recursive: true });
  for (const [name, make] of Object.entries(TEXTURES)) writeFileSync(join(out, name), make());
  console.log(`wrote ${Object.keys(TEXTURES).join(", ")} to ${out}`);
}
