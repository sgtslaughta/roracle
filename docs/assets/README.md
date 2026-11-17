# Vendored docs-site assets

Not part of the published pages (excluded from the site nav; listed here for provenance). Everything is committed
so the docs build needs no network.

## Mermaid (docs/assets/js/mermaid.min.js)

- Package: `mermaid@12.3.0` (MIT, see `js/mermaid.LICENSE`), file `dist/mermaid.min.js`.
- Source: `https://registry.npmjs.org/mermaid/-/mermaid-12.3.0.tgz`, tarball sha256
  `be0684e8d20bfc874df12c0f04927a5c482f57f2200eec54cca52a4583fd4040`.
- `mermaid.min.js` sha256 `336df803d87a38b70a01e736e666c8f7601d2ce42f5e47c8fafe5d721074b9a3`. The build guard
  (`scripts/docs/check_site.py`) fails if the built file differs from the pin in `scripts/docs/site-url-allow.txt`.
- Why: Zensical's bundle fetches `unpkg.com/mermaid@12` unless a global `mermaid` already exists; this file defines it.
- Bundled third-party code (package dependencies; licences per npm metadata): d3 (ISC), d3-sankey (BSD-3-Clause),
  dagre-d3-es, dayjs, cytoscape (+ cose-bilkent, fcose), katex, marked, roughjs, stylis, khroma, ts-dedent,
  es-toolkit, lodash-es (the bundle's own notice), @braintree/sanitize-url, @iconify/utils, @mermaid-js/parser,
  @upsetjs/venn.js (all MIT); chevrotain (Apache-2.0); DOMPurify 3.4.17 (MPL-2.0 OR Apache-2.0); elkjs
  (EPL-2.0 OR GPL-3.0-or-later; used under EPL-2.0). The bundle carries its own "Bundled license information" block
  at the end of the file; keep it intact.

## Fonts (docs/assets/fonts)

Latin subsets (woff2) from `@fontsource-variable/manrope@5.3.0` and `@fontsource-variable/newsreader@5.3.0`
(the same versions the app pins in `frontend/pnpm-lock.yaml`). Licence: SIL OFL 1.1, text in `fonts/LICENSE.txt`
(OFL allows redistribution; fonts are not sold on their own).

| File | Source tarball (registry.npmjs.org) | tarball sha256 | file sha256 |
|---|---|---|---|
| manrope-latin-wght-normal.woff2 | `@fontsource-variable/manrope/-/manrope-5.3.0.tgz` | `9629a2474e2bd73944d83392c856e268193d42c3954455bd6adb3d1ea74aa27a` | `a30ddcd349703aff7464c34bef3fffdff405ee50c113440d7c8693c02d210972` |
| newsreader-latin-wght-normal.woff2 | `@fontsource-variable/newsreader/-/newsreader-5.3.0.tgz` | `4b8cfba8f4e79abe7b221b761b751d76569fd2d2303a9cace2e90b862c56630d` | `62981321d9a3cc7a61a73792729043703fd6112da86e8ec848bb57f088578757` |
| newsreader-latin-wght-italic.woff2 | same tarball as above | same as above | `48bc8861b9b2ca9300747cad4fd6a3b4ac3028d364df00bd1b72097baa75e509` |

## Logo and favicon

`logo.png` and `favicon.ico` are copies of `frontend/public/logo.png` and `frontend/public/favicon.ico`.
