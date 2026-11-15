// CI guard: the built app must not load fonts/scripts/styles/workers from a CDN (air-gapped deployment),
// and dev-only files must not ship. Scans html, css and js.
import { existsSync, readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";

const dist = new URL("../dist", import.meta.url).pathname;
const bad = [];
// namespace identifiers and error-doc links that are strings, never fetched
const ALLOW = /^https?:\/\/(www\.w3\.org\/|react\.dev\/errors|reactjs\.org\/docs\/error|localhost[:/])/;
const walk = (d) => {
  for (const f of readdirSync(d)) {
    const p = join(d, f);
    if (statSync(p).isDirectory()) { walk(p); continue; }
    if (!/\.(html|css|js|mjs)$/.test(f)) continue;
    const s = readFileSync(p, "utf8");
    for (const m of s.matchAll(/https?:\/\/[^\s"'`)\\<>]+/g)) {
      if (ALLOW.test(m[0])) continue;
      // only flag URLs used as a source: src/href/@import/url()/import()/new Worker/fetch
      const before = s.slice(Math.max(0, m.index - 40), m.index);
      if (/(src|href)\s*=\s*["']?$|url\(\s*["']?$|@import\s+["']?$|import\(\s*["'`]$|Worker\(\s*["'`]$|fetch\(\s*["'`]$/.test(before)) bad.push(`${p}: ${m[0]}`);
    }
  }
};
walk(dist);
if (process.env.VITE_MOCK !== "1" && existsSync(join(dist, "mockServiceWorker.js"))) bad.push("mockServiceWorker.js ships in dist");
if (bad.length) { console.error("check-dist failed:\n" + bad.join("\n")); process.exit(1); }
console.log("dist: no external font/script/style/worker URLs, no dev-only files");
