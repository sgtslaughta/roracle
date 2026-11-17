# ADR-0011: Paper writing — profiles, citation verification, sandboxed builds

Status: Accepted (2026-11-15)

## Decision

- Writing profiles are versioned YAML managed in admin; each rule is `blocking` or `warning`.
  Deterministic rules may block; LLM-judged rules only warn by default.
- `check_citations` resolves each citation to a library document and asks a model whether the cited
  passage supports the sentence; failures block submission until fixed or acknowledged.
- LaTeX builds run in `latex-builder`: no shell-escape, no network, read-only FS except scratch,
  120 s / 2 GB limits, behind a `Builder` interface (GitLab-runner backend possible later).

## Consequences

- Fabricated citations cannot be submitted silently. TeX Live image (~5 GB) must be mirrored.

## Update (2026-11-17)

Draft PDFs are built by a separate Chromium-enabled Gotenberg (`ROR_DRAFT_GOTENBERG_URL`; empty means the build returns 503), not by the ingest Gotenberg (`config.py:147`). `ROR_BUILD_TIMEOUT_S` (120) bounds each pandoc call and the Gotenberg request; `ROR_BUILD_CONCURRENCY` (2) caps concurrent builds per API process. The `latex-builder` service is a contract only and is not shipped: `ROR_LATEX_BUILDER` accepts only `disabled`, so a draft with `format: latex` returns 501 on build ([latex-builder contract](../enterprise/latex-builder.md)).
