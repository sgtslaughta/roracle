# latex-builder service contract

Optional, **not shipped**. `ROR_LATEX_BUILDER=disabled` (default) makes `POST /drafts/{id}/build` return 501
`LaTeX builds are not enabled` for drafts with `format: latex`. A site that needs LaTeX implements this
contract and a `Builder` (`roracle/writing/build.py`) that calls it; the service is the only place TeX runs.

## Why a separate service

LaTeX is a programming language (`\write18`, `\input`, `\openin`, shell escape). Treat the source as hostile code.

## Contract

`POST {latex-builder}/build`, `Content-Type: application/json`

```json
{"source": "<draft text>", "files": {"refs.bib": "<bibtex>"}, "timeout_s": 120}
```

- `200 application/pdf`: the PDF (`%PDF-` prefix; rOracle rejects anything else and anything over
  `ROR_BUILD_MAX_PDF_BYTES`).
- `422 {"detail": "<short message>"}`: TeX error (message shown to the author, max 500 chars).
- `503`/timeout: unavailable (rOracle returns 503 to the caller).

## Required isolation

- One-shot engine per request (`latexmk -pdf -no-shell-escape`, `openin_any=p`, `openout_any=p`, `shell_escape=f`),
  fresh empty work directory, deleted afterwards.
- Non-root, read-only root FS (tmpfs work dir), seccomp `RuntimeDefault`, no capabilities, no secrets in env.
- Deny-all egress NetworkPolicy; ingress only from the api pods.
- Hard limits: wall-clock (`timeout_s`, max 120), CPU, memory, work dir size, request body (2 MiB), PDF size.
- No `--shell-escape`, no `\write18`, no `minted`/`pythontex`; a fixed TeX Live package set (see the mirror manifest:
  `texlive/texlive`). Log output is not returned.
- Authentication: network-level only (mTLS or NetworkPolicy); the service never sees user identity.
