# ADR-0012: Frontend stack and design tokens

Status: Accepted (2026-11-15)

## Decision

React + Vite + TypeScript SPA; shadcn/ui (Radix + Tailwind, components vendored in-repo); Lucide
icons; TanStack Query + Router; PDF.js; KaTeX. Fonts (Newsreader, Manrope, STIX Two) and icons are
bundled — no runtime CDN. Theme tokens live in one CSS file mapped to shadcn variables; light =
Peach skyline, dark = Wildflower meadow on warm slate (values in spec §11). Contour textures are SVGs
generated at build time with a fixed seed.

## Consequences

- Air-gapped builds need only the npm mirror. Changing the palette is a token edit.
