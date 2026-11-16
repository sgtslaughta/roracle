/// <reference types="vitest/config" />
import { defineConfig, type Plugin } from "vite";
import { rmSync } from "node:fs";
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

export default defineConfig({
  plugins: [react(), tailwindcss(), dropDevOnlyPublicFiles()],
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
    env: { VITE_API_BASE: "http://localhost/api" },
  },
});
