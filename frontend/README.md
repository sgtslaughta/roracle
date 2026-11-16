# rOracle web

Vite + React 19 + TypeScript (strict), Tailwind v4, shadcn/ui (vendored in `src/components/ui`),
TanStack Query/Router, pdf.js (worker bundled, no CDN), MSW for the mock API.

## Develop

```sh
corepack pnpm install
VITE_MOCK=1 corepack pnpm dev      # mock API (MSW): the prototype's 7 sample documents, no backend or login
corepack pnpm dev                   # real API
```

Real API mode: the dev server proxies `/api` to `http://localhost:8000` (prefix stripped) and signs in
through Keycloak at `http://localhost:8080`, realm `roracle`, client `roracle-web`
(dev user `researcher` / `researcher`). Settings live in `.env.example`
(`VITE_API_BASE`, `VITE_OIDC_AUTHORITY`, `VITE_OIDC_CLIENT_ID`, `VITE_MOCK`).

## Checks

```sh
corepack pnpm test -- --run        # Vitest + Testing Library (MSW node server)
corepack pnpm build                # tsc --noEmit (strict) + vite build
corepack pnpm check:dist           # fails if dist/ references an external font/script/style URL
corepack pnpm contours             # regenerate public/contour-*.svg (deterministic, fixed seed)
```

## Layout

- `src/app` shell, router, top bar, sidebar · `src/routes` route components · `src/features/{library,reader,uploads,palette,ask}`
- `src/api` typed client and shared types · `src/auth` OIDC · `src/mocks` MSW handlers, sample data, generated sample PDFs
- `src/styles` design tokens (spec §11) and per-area CSS

## CI commands

Run from `frontend/` (Node >= 22, `corepack enable` or `npx pnpm@9`):

```sh
corepack pnpm install --frozen-lockfile
corepack pnpm lint                     # tsc --noEmit
corepack pnpm exec vitest run          # tests (must print no warnings)
corepack pnpm build                    # tsc --noEmit (strict) + vite build
corepack pnpm check:dist               # no CDN URLs in html/css/js, no mockServiceWorker.js in dist
```
