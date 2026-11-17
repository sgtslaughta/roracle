# Developer guide

For contributors: repository layout, local development, testing and conventions. How the system fits together is in [architecture](../architecture/README.md); decisions and their rationale are in the [ADRs](../adr/README.md); terms are in the [glossary](../glossary.md). Generated references ([CLI](../reference/cli.md), [settings](../reference/settings.md), [OpenAPI](../reference/openapi.json)) are not repeated here.

## Pages

- [Repository layout](repo-layout.md): backend modules, frontend structure, deploy, scripts.
- [Local development](local-dev.md): `scripts/dev-up.sh`, the dev compose stack, the Keycloak dev realm, the inference box, image builds, troubleshooting.
- [Testing](testing.md): unit and integration split, the test-database guard, the integration lock, Elasticsearch 9.5.6, frontend tests, Helm `assert-render` and kubeconform.
- [Eval harness](eval-harness.md): `roracle eval`, the synthetic fixture and the CI job.
- [Perf suite](perf-suite.md): `roracle perf`, the scratch-only guards, budgets and the CI job.
- [Conventions](conventions.md): semantic commits, the single SemVer, the 500-line rule, docs rules, ADRs.
- [Subagent-driven development](sdd-workflow.md): the optional plan and writer/reviewer workflow.

## Quick start

```sh
scripts/dev-up.sh                                    # dev stack, migrated
cd backend && cp .env.example .env && set -a && source .env && set +a && uv sync
uv run python -m roracle api                         # and `worker` in a second shell
cd ../frontend && corepack pnpm install && corepack pnpm dev
```

Details and the unit-test commands are in [local development](local-dev.md) and [testing](testing.md). Run `scripts/docs/check.sh` before committing documentation changes.
