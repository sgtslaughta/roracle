/// <reference types="vitest/config" />
import { defineConfig, type Plugin } from "vite";
import { existsSync, readFileSync, rmSync, statSync } from "node:fs";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import path from "node:path";

// The MSW worker and the logo source are dev-only files from public/: keep them out of production builds.
const dropDevOnlyPublicFiles = (): Plugin => ({
  name: "drop-dev-only-public-files",
  apply: "build",
  closeBundle() {
    if (process.env.VITE_MOCK === "1") return;
    for (const f of ["mockServiceWorker.js", "logo-source.png"]) rmSync(path.resolve(import.meta.dirname, "dist", f), { force: true });
  },
});

// Dev only: serve the built docs site (../site, from scripts/docs/build-site.sh) at /docs/, like nginx does in prod.
const TYPES: Record<string, string> = { ".html": "text/html", ".css": "text/css", ".js": "text/javascript", ".json": "application/json", ".svg": "image/svg+xml", ".png": "image/png", ".woff2": "font/woff2", ".xml": "application/xml" };
const devDocs = (): Plugin => ({
  name: "dev-docs-site",
  apply: "serve",
  configureServer(server) {
    const root = path.resolve(import.meta.dirname, "..", "site");
    server.middlewares.use("/docs", (req, res) => {
      const rel = decodeURIComponent((req.url ?? "/").split("?")[0]);
      let file = path.join(root, rel);
      if (!file.startsWith(root)) { res.statusCode = 400; return res.end(); } // no ../ escapes
      if (existsSync(file) && statSync(file).isDirectory()) file = path.join(file, "index.html");
      if (!existsSync(file)) {
        res.statusCode = 404;
        res.setHeader("Content-Type", "text/plain");
        return res.end(existsSync(root) ? "Not found" : "Docs site not built. Run scripts/docs/build-site.sh from the repo root.");
      }
      res.setHeader("Content-Type", TYPES[path.extname(file)] ?? "application/octet-stream");
      res.end(readFileSync(file));
    });
  },
});

export default defineConfig({
  plugins: [react(), tailwindcss(), dropDevOnlyPublicFiles(), devDocs()],
  build: {
    chunkSizeWarningLimit: 500, // routes, markdown/KaTeX and pdf.js load on demand
    rolldownOptions: { output: { codeSplitting: { groups: [{ name: "oidc", test: /node_modules[\\/]oidc-client-ts/ }] } } },
  },
  resolve: { alias: { "@": path.resolve(import.meta.dirname, "src") } },
  server: {
    proxy: {
      "/api": { target: "http://localhost:8000", changeOrigin: true, rewrite: (p) => p.replace(/^\/api/, "") },
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test/setup.ts"],
    css: false,
    testTimeout: 20_000, // lazy chunks + parallel workers make cold first renders slow
    env: { VITE_API_BASE: "http://localhost/api" },
  },
});
