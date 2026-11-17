# Docs screenshots

Regenerates `docs/assets/screens/<id>.png`, the images used in `docs/user/` and `docs/admin/`.
It drives the real frontend in MSW mock mode (`VITE_MOCK=1`), so it needs no backend, Keycloak or Docker, and it sends no traffic outside localhost (every other host is blocked in the browser context).

## Re-run

```bash
# 1. dev server in mock mode (any free port; default below)
cd frontend && VITE_MOCK=1 pnpm exec vite --port 5183 --strictPort &

# 2. install the pinned browser driver once, then capture
cd scripts/docs/screenshots && npm ci
node capture.mjs                 # every screen
node capture.mjs reader-split    # only the ids you name
```

| Setting | Default |
|---|---|
| `DOCS_BASE_URL` | `http://localhost:5183` |
| `PW_CHROMIUM` | `~/.cache/ms-playwright/chromium_headless_shell-1254/chrome-headless-shell-linux64/chrome-headless-shell` |

`playwright-core` is pinned in `package.json`. It brings no browser; point `PW_CHROMIUM` at an installed Chromium or headless shell.

## What it does

- Viewport 1440x900, light theme, one fresh browser context per screen.
- Admin screens set `roracle-mock-admin` in local storage, which makes the mock `/me` return an admin.
- PNGs are reduced to 128 colours with ImageMagick `convert` (needed to stay under 300 KB; the script fails any file over that).
- Demo rows that the stock mock lacks (extra tokens, handoffs, a label proposal, UUID audit users) come from `frontend/src/mocks/docsFixtures.ts`, loaded only by the browser mock.

## Adding or changing a screen

1. Put `<!-- screenshot: <id>: <description> -->` in the page, then add an entry with the same `id` to `shots.mjs` (`path`, optional `admin`, `run`, `el` or `clip`).
2. Run `node capture.mjs <id>` and look at the PNG.
3. Replace the placeholder with `![<description>](../assets/screens/<id>.png)`.

Mock state lives in the page, so every flow must finish inside one page load.
