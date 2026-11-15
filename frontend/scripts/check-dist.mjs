// CI guard: the built app must not load fonts/scripts/styles from a CDN (air-gapped deployment).
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";

const bad = [];
const walk = (d) => {
  for (const f of readdirSync(d)) {
    const p = join(d, f);
    if (statSync(p).isDirectory()) walk(p);
    else if (/\.(html|css)$/.test(f)) {
      const s = readFileSync(p, "utf8");
      if (/(src|href)=["']https?:\/\//.test(s) || /url\(\s*["']?https?:\/\//.test(s) || /@import\s+["']?https?:\/\//.test(s)) bad.push(p);
    }
  }
};
walk(new URL("../dist", import.meta.url).pathname);
if (bad.length) { console.error("External URLs found in:", bad.join(", ")); process.exit(1); }
console.log("dist: no external font/script/style URLs");
