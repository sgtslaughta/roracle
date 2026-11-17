// Captures docs screenshots from the frontend running in MSW mock mode.
// Usage: node capture.mjs [id ...]   (no ids = all)   see README.md
import { chromium } from "playwright-core";
import { mkdirSync, statSync } from "node:fs";
import { execFileSync } from "node:child_process";
import { homedir } from "node:os";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { SHOTS } from "./shots.mjs";

const BASE = process.env.DOCS_BASE_URL ?? "http://localhost:5183";
const EXE = process.env.PW_CHROMIUM ?? `${homedir()}/.cache/ms-playwright/chromium_headless_shell-1254/chrome-headless-shell-linux64/chrome-headless-shell`;
const OUT = resolve(dirname(fileURLToPath(import.meta.url)), "../../../docs/assets/screens");
mkdirSync(OUT, { recursive: true });

const want = process.argv.slice(2);
const todo = SHOTS.filter((s) => !want.length || want.includes(s.id));
const browser = await chromium.launch({ executablePath: EXE, args: ["--no-sandbox"] });
let failed = 0;
for (const s of todo) {
  const ctx = await browser.newContext({ viewport: { width: 1440, height: s.tall ? 1600 : 900 }, colorScheme: "light" });
  // no external network: only the mock origin may be reached
  await ctx.route((u) => !["localhost", "127.0.0.1"].includes(u.hostname), (r) => r.abort());
  await ctx.addInitScript((admin) => {
    localStorage.setItem("roracle-theme", "light");
    if (admin) localStorage.setItem("roracle-mock-admin", "1"); else localStorage.removeItem("roracle-mock-admin");
  }, !!s.admin);
  const page = await ctx.newPage();
  page.on("pageerror", (e) => console.log(`  [${s.id}] pageerror: ${e.message}`));
  try {
    await page.goto(BASE + s.path, { waitUntil: "networkidle" });
    await page.waitForTimeout(600);
    if (s.run) await s.run(page);
    await page.waitForTimeout(400);
    const file = `${OUT}/${s.id}.png`;
    await page.evaluate(() => { const a = document.activeElement; if (a && a.tagName !== "BODY" && a.tagName !== "TEXTAREA" && !(a.tagName === "INPUT" && a.value)) a.blur(); });
    if (s.el) await s.el(page).screenshot({ path: file }); // crop to one element
    else await page.screenshot({ path: file, ...(s.clip ? { clip: s.clip } : {}), ...(s.full ? { fullPage: true } : {}) });
    // shrink: 128-colour palette, no dithering (needs ImageMagick `convert`; flat UI screens lose nothing visible)
    try { execFileSync("convert", [file, "-dither", "None", "-colors", "128", "-define", "png:compression-level=9", file]); }
    catch { console.log("  warn: ImageMagick convert not found, PNG not optimised"); }
    const kb = Math.round(statSync(file).size / 1024);
    if (kb >= 300) throw new Error(`${kb} KB is over the 300 KB limit`);
    console.log("ok", s.id, `${kb} KB`);
  } catch (e) {
    failed++;
    console.log("FAIL", s.id, String(e.message).split("\n")[0]);
  }
  await ctx.close();
}
await browser.close();
process.exit(failed ? 1 : 0);
