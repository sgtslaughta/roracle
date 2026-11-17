# Subagent-driven development (optional)

Much of rOracle was built with a plan-driven, writer and reviewer workflow. You do not need it to contribute; it is documented so you can read the history and reuse it. It is a process, not a feature of the product.

## Plans

Plans live in `docs/superpowers/plans/` as `YYYY-MM-DD-<slug>.md` (for example `2026-11-17-p12-docs.md`). They are excluded from the docs site, so open them in the repository. A plan has:

- a header naming the required method (subagent-driven development, or executing the plan inline), a goal, the architecture and the tech stack;
- global constraints that every task obeys (file size, air-gap, commit trailer, lint);
- numbered tasks, each listing the files it owns, its steps as checkboxes, and a final commit step.

Plans say why and what, and they bind only while the work is in flight. Once code is merged, the code is the truth and the docs follow it ([conventions](conventions.md)).

## The loop

One task at a time:

1. **Brief.** The controller writes the task requirements, with exact values, into a per-plan workspace directory `.superpowers/sdd/<plan>/` (gitignored), next to shared notes and the implementer rules.
2. **Writer.** One implementer works only on its task, in its own git worktree and branch, owns only the files the task lists, and never spawns further agents. It follows the brief exactly, runs the checks, commits with semantic commits, and writes a report: what changed, commands and output, claims it could not verify, concerns. Docs tasks add a claim table that maps each claim to `path:line`. It replies with a short status: DONE, DONE_WITH_CONCERNS, BLOCKED or NEEDS_CONTEXT.
3. **Reviewer.** A separate reviewer checks the result against the brief and the code. For docs it audits the claim table and spot-checks claims against the code; for code it checks spec and quality. Security-sensitive tasks get a stronger model.
4. **Fix loop.** Findings go back to the writer, which fixes, re-runs the checks, commits and appends a fix report. The loop repeats up to five rounds, then the controller decides.
5. **Ledger.** The controller records the outcome of each task and each ruling in `progress.md` in the workspace, in the form `Ruling: <decision> - <why> - <cost if wrong>`.

At the end of a plan: one whole-branch review, one fix wave and a scoped re-review. The controller pushes and opens the merge request; writers never push, merge or open it.

## Shared rules worth knowing

- Never touch another worktree or the main checkout while working on a task.
- Shared dev-stack services are not restarted or modified; integration tests are serialized with the [integration lock](testing.md#the-integration-lock).
- A statement you cannot verify in code is written as `> TODO(verify): ...` and listed in the report; the reviewer resolves it or removes it before merge.
