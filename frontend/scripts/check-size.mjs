// CI guard (task 10.12): bundle size budget over dist/assets, gzip sizes. Raise a limit deliberately, with the reason in the commit.
import { readdirSync, readFileSync } from "node:fs";
import { gzipSync } from "node:zlib";
import { join } from "node:path";

const dir = new URL("../dist/assets", import.meta.url).pathname;
const KB = 1024;
// [file prefix, max gzip KB]. The entry chunk is what every page load pays; the rest load on demand.
const LIMITS = [["index-", 150], ["PdfPage-", 160], ["AskRoute-", 110], ["pdf.worker", 450]];
const DEFAULT = 60; // any other chunk
const TOTAL = 1300; // sum of all JS+CSS, gzip KB

const files = readdirSync(dir).filter((f) => /\.(js|mjs|css)$/.test(f));
const bad = [];
let total = 0;
const rows = files.map((f) => {
  const gz = gzipSync(readFileSync(join(dir, f))).length / KB;
  total += gz;
  const max = (LIMITS.find(([p]) => f.startsWith(p)) ?? [, DEFAULT])[1];
  if (gz > max) bad.push(`${f}: ${gz.toFixed(1)} KB gzip > ${max} KB`);
  return [f, gz];
});
rows.sort((a, b) => b[1] - a[1]).slice(0, 8).forEach(([f, g]) => console.log(`${g.toFixed(1).padStart(7)} KB  ${f}`));
console.log(`${total.toFixed(1).padStart(7)} KB  total (budget ${TOTAL})`);
if (total > TOTAL) bad.push(`total ${total.toFixed(1)} KB gzip > ${TOTAL} KB`);
if (bad.length) { console.error("check-size failed:\n" + bad.join("\n")); process.exit(1); }
