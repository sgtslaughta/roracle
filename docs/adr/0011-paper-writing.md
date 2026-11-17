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
