# rOracle web

Vite + React 19 + TypeScript (strict), Tailwind v4, shadcn/ui (vendored in `src/components/ui`),
TanStack Query/Router, pdf.js (worker bundled, no CDN), MSW for the mock API.

Running it (mock mode with `VITE_MOCK=1`, real API mode, Keycloak sign-in and the `VITE_*` settings):
[local development](../docs/developer/local-dev.md). Source layout: [repository layout](../docs/developer/repo-layout.md).
Test setup, CI commands and bundle checks: [testing](../docs/developer/testing.md).
The full developer guide starts at [docs/developer](../docs/developer/README.md).

```sh
corepack pnpm install
VITE_MOCK=1 corepack pnpm dev      # mock API, no backend or login
corepack pnpm dev                  # real API
```
