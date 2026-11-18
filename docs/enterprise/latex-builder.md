# latex-builder service

Optional, shipped, **off by default**. `ROR_LATEX_BUILDER=disabled` makes `POST /api/drafts/{id}/build` and submit return 501
`LaTeX builds are not enabled` for drafts with `format: latex`. Set `ROR_LATEX_BUILDER` to the service URL
(for example `http://roracle-latexbuilder:8080`) and those drafts build and submit like Markdown ones. Markdown drafts never
use this service.

## Why a separate service

LaTeX is a programming language (`\write18`, `\input`, `\openin`, shell escape). The source is hostile code, so TeX runs only in
this container, never in the api or worker.

## Enable it

1. **Build the image** from `deploy/latex-builder/` (`texlive/texlive:latest-small` pinned by tag and digest, about 2.3 GB; the
   full image is about 5 GB). Mirror the base with `--build-arg BASE=<mirror>/texlive/texlive:<tag>@<digest>` and push the result to
   your registry ([mirror manifest](airgap-mirror-manifest.md)).
2. **Dev**: `docker compose -f deploy/dev/compose.yaml --profile latex up -d --build latex-builder`, then
   `ROR_LATEX_BUILDER=http://localhost:3302` (host port `ROR_LATEX_BUILDER_PORT`). The compose network has no outbound NAT, so the
   container cannot reach the internet.
3. **Helm**: `parsers.latexbuilder.enabled=true`, `parsers.latexbuilder.image.repository` and `.tag` (or `.digest`) set to your image.
   The chart fills `ROR_LATEX_BUILDER`, adds a NetworkPolicy with ingress from the api pods only and no egress, and the pod runs
   non-root (uid 1001), read-only root FS, tmpfs `/tmp`, no capabilities, seccomp `RuntimeDefault`.

The api limits concurrent builds with `ROR_BUILD_CONCURRENCY` (more is 503 `build service busy`) and bounds the whole build by
`ROR_BUILD_TIMEOUT_S` (at most 120 s) and the PDF by `ROR_BUILD_MAX_PDF_BYTES`.

## Contract

`POST {latex-builder}/build`, `Content-Type: application/json`

```json
{"source": "<draft text>", "files": {"refs.bib": "<bibtex>"}, "timeout_s": 120}
```

rOracle sends the draft without its YAML front matter and, when the draft cites library papers, a generated `refs.bib`
(one `@misc` entry per resolved key: title, authors, year). `files` names must be simple `*.bib` or `*.tex` names (at most 20).

- `200 application/pdf`: the PDF (`%PDF-` prefix; rOracle rejects anything else and anything over `ROR_BUILD_MAX_PDF_BYTES`).
- `422 {"detail": "<short message>"}`: TeX error. The message is the TeX error lines (`! ...` and the `l.N` context), at most 500
  characters, shown to the author. The full log is never returned.
- `400` malformed request, `413` body over 2 MiB: shown to the author as a 422.
- `503`, a timeout or a connection failure: unavailable (rOracle returns 503 to the caller).
- `GET /health` returns 200 `ok`.

## Threat model

The input is user-controlled TeX. What each control does and does not do:

| Risk | Control |
|---|---|
| Command execution (`\write18`, `minted`, `pythontex`) | `latexmk -pdf -no-shell-escape`, `shell_escape=f`; only `*.bib` / `*.tex` auxiliary files are accepted |
| Writing outside the work dir | `openout_any=p` (verified: `\openout` to an absolute path fails), read-only root FS, tmpfs `/tmp` only |
| Reading files | `openin_any=p` is set but **TeX Live still lets `\input` read absolute paths** (verified with `/etc/passwd`). The defence is that the container holds nothing secret: no secrets in env or mounted, no service account token, image content only |
| Network (SSRF, exfiltration) | No egress: NetworkPolicy deny-all (Helm), no outbound NAT (compose). TeX has no network primitives without shell escape, so this is defence in depth |
| One request affecting another | Fresh `mkdtemp` directory per request, deleted afterwards; `HOME` is that directory; the process group is killed after every build |
| Resource exhaustion | Wall-clock `timeout_s` (max 120) kills the process group; `RLIMIT_CPU` 120 s, `RLIMIT_AS` 2 GiB, `RLIMIT_FSIZE` and work dir 256 MiB, `RLIMIT_NPROC` 256; body 2 MiB; PDF `LATEX_MAX_PDF_BYTES` (100 MiB); `LATEX_CONCURRENCY` (2) builds at once, more is 503 busy; pod memory limit |
| Privilege | uid 1001, `ALL` capabilities dropped, `no-new-privileges`, seccomp `RuntimeDefault`, no secrets in env |
| Information leakage in errors | Only TeX error lines are returned, never the log; the server logs no request content |
| Who may call it | Network level only: ingress from the api pods. The service never sees user identity |

A fixed TeX Live package set ships in the image; there is no `tlmgr` or network install at run time. A document that needs a
package the image lacks fails with a TeX error. Build a larger image (the full `texlive/texlive`) if you need more.
