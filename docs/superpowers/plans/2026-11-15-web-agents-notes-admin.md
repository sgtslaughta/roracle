# Web: Agents, Notes & Admin (P7/P8/P10 UI) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The web UI for access tokens and agent handoffs (P7), reader annotations, evidence and agent proposals (P8), notifications and the admin section (P10), against the final backend contracts and the MSW mock.

**Architecture:** New API modules `src/api/{agents,notes,admin}.ts` (types mirror the backend FINAL contracts) with hooks in feature folders `src/features/{agents,notes,proposals,notifications,admin}/`. One shared `SafeMarkdown` (extracted from `AnswerText`: GFM only, no raw HTML, images render as alt text, links via `safeHref`) renders note bodies; everything else from users/agents (titles, briefs, evidence metadata, audit details, run logs) renders as plain text. MSW handlers per area under `src/mocks/` keep `VITE_MOCK=1` fully usable.

**Tech Stack:** P5/P6 stack; no new dependencies.

**Spec:** `docs/spec.md` §6, §7, §9, §10, §11; ADR-0012; prototype `docs/prototype/roracle.html` (notes, evidence, handoff dialog, proposals — CSS block "notes, evidence, handoff, proposals, drafts"). Contracts: `.worktrees/p7-mcp/.superpowers/sdd/p7-mcp-handoff/FINAL.md`, `.worktrees/p8-notes/.superpowers/sdd/p8-notes/FINAL.md`, `.worktrees/p10-connectors/.superpowers/sdd/p10-connectors-admin/FINAL.md` (route code under each worktree's `backend/src/roracle/api/routes/` is authoritative when a shape is unclear).

## Global Constraints

- All P5 + P6 Global Constraints apply (tokens, bundled fonts, no CDN, `OverflowList` caps + context menus on every unbounded list, keyboard, a11y, thumbs up/down for every vote, files < 500 lines, `seg()` for every id in a path).
- Checks before every commit (from `frontend/`): `corepack pnpm lint && corepack pnpm exec vitest run && corepack pnpm build && corepack pnpm check:dist`.
- **Untrusted content:** note/reply bodies → `SafeMarkdown` only. Handoff title/goal/brief, proposal payloads, evidence crate fields, notification payloads, connector logs, audit details → plain text (`white-space: pre-wrap` where multi-line). No `dangerouslySetInnerHTML` anywhere new. Evidence is never previewed or opened inline: download only via `apiFetch` blob + `<a download>`, and only when `file.scan_status` is `clean` or `unscanned`; otherwise show the status.
- Secrets: a created PAT `token` is shown once (copy button), never stored in query cache or `localStorage`; cleared when the dialog closes.
- Admin UI renders only when `me.is_admin`; `/admin/*` for non-admins shows "Admins only" (backend 403 is still the authority).
- Note anchor bbox (`{x0,y0,x1,y1,coord_origin}`) is converted to the reader `Bbox` (`{l,t,r,b,coord_origin}`) in one helper (`noteBbox`).
- Mock: each area gets its own handler file; `resetMockState()` resets all of it; admin via existing `roracle-mock-admin`.

## Review Focus

1. No XSS path: note bodies through `SafeMarkdown` (no `<img>`, no `javascript:`), everything else plain text — Tasks 1–4.
2. PAT secret shown once and dropped from memory on close; scopes/expiry bounds enforced in the form — Task 1.
3. Evidence download gated on scan status; no inline preview; upload progress + cancel — Task 3.
4. Threads/votes/report/hide permissions match the contract (own-note vote disabled, hide only doc owner, admin remove) — Task 2.
5. Admin routes invisible and inert for non-admins; secret fields write-only (`***` keeps) — Tasks 5–6.

---

### Task 1: Shared SafeMarkdown, access tokens, handoffs
**Files:** `src/components/SafeMarkdown.tsx` (+ `AnswerText` reuses `safeHref`/component map), `src/api/agents.ts`, `src/features/agents/{api.ts,TokensPanel.tsx,CreateTokenDialog.tsx,HandoffDialog.tsx,HandoffsPanel.tsx,HandoffLanding.tsx,ContinueInAgent.tsx}`, `src/routes/{SettingsRoute,HandoffRoute}.tsx`, `src/app/{router.tsx,TopBar.tsx}` (avatar → menu: Settings, Admin when admin), call sites `features/ask/AnswerFooter.tsx`, `features/reader/ReaderPane.tsx`, `features/library/SearchPage.tsx` (kind `search`, collection filter active → kind `collection`), `src/mocks/agents.ts`, tests.
**Interfaces:** `/settings` (tabs "Access tokens" | "Handoffs"); `/h/$code`. `<ContinueInAgent kind sourceId? docIds title/>` opens `HandoffDialog` (title, goal, editable brief, audience creator|shared|authenticated, shares chips when shared, expiry 1–90 d default 7) → manifest view with display code, Copy link, Copy agent prompt `Using the rOracle MCP server, load handoff XXXX-XXXX and continue: <goal>`. Handoffs panel: list mine (OverflowList/paging), edit, revoke. Landing: loads manifest, shows title/goal/brief (plain text), doc list (open in reader), "N sources withheld", notes, copy prompt; 404 → "not found or not shared with you"; 410 → expired/revoked copy.
- [ ] Tests: SafeMarkdown drops `<img>`/`javascript:` links, keeps safe links; create token sends `{name, scopes, expires_in_days}`, secret visible once and gone after close; revoke DELETE; handoff create body (shares only when shared); landing 404/410 states; chat/reader/search buttons open the dialog with the right `kind`.
- [ ] **Commit** `feat(web): access tokens and agent handoffs`

### Task 2: Reader annotations and threads (opus implementer)
**Files:** `src/api/notes.ts`, `src/features/notes/{api.ts,NotesTab.tsx,NoteCard.tsx,NoteComposer.tsx,NoteActions.tsx,anchor.ts}`, `src/features/reader/{ReaderPane,ReferencesTab,PdfView,PdfPage}.tsx`, `src/styles/notes.css`, `src/mocks/notes.ts`, tests.
**Interfaces:** Notes tab: top-level notes + replies (by `parent_id`), filter by kind; `NoteCard` (kind pill, author, "Agent-assisted", visibility, "May not apply to version N" when `may_not_apply_to_version`, quote, `SafeMarkdown` body, thumbs vote — disabled on own note, reply, ⋯ menu: report reason / hide·unhide (doc owner) / edit·delete (author) / remove (admin)). Composer: kind, body, visibility (private|shared|everyone, shares chips), relation type + target doc picker (library search) for kind relation. Anchoring: "Add note" mode in Document view — clicking a page picks the docling chunk under the pointer → anchor `{quote ≤1000, page, bbox}`; doc-level notes have no anchor. Anchored notes with `page`+`bbox` on the viewed version draw highlights + `.nmark` margin markers; marker click → Notes tab, flash card.
- [ ] Tests: list/thread grouping; create body for comment/relation/anchored; reply; vote PUT + aria-pressed; own-note vote disabled; report body; hide only for owner; flag text; markdown body sanitised; marker click switches tab.
- [ ] **Commit** `feat(web): reader annotations with threads, votes and anchors`

### Task 3: Evidence panel and community notes on search (opus implementer)
**Files:** `src/features/notes/{Evidence.tsx,EvidenceForm.tsx,evidenceApi.ts}`, `src/features/library/{ResultCard.tsx,api.ts}` (`notes=true`), `src/mocks/{notes,search}.ts`, tests.
**Interfaces:** On finding/correction notes: evidence list (stance pill, crate summary as `<dl>` plain text: repo@commit, image digest, agent client/model, seed, time span, inputs/outputs capped via OverflowList, file name/size/sha/scan status or URI+hash), reproductions `reproduced ×n / failed ×n` + my outcome toggle (PUT), Download only when clean|unscanned, delete (creator). Upload form: stance + provenance fields + file (XMLHttpRequest with progress bar and cancel, multipart `meta` JSON + `file`) or URI + sha256. Note header evidence summary counts from `note.evidence`. Search hits: `hit.notes` rendered as `.hit.cnote` cards (kind, author, score, page, plain-text body ≤500), max 2 then "+K notes" → opens reader Notes tab.
- [ ] Tests: download button absent for infected/pending; download uses blob, never navigates; upload progress events + cancel; meta JSON shape; reproduction PUT; community notes shown and capped.
- [ ] **Commit** `feat(web): evidence panel and community notes in search`

### Task 4: Proposals inbox and notifications (opus implementer)
**Files:** `src/features/proposals/{api.ts,ProposalsPage.tsx,ProposalCard.tsx}`, `src/features/notifications/{api.ts,Bell.tsx,NotificationsPage.tsx,describe.ts}`, `src/routes/placeholders.tsx` (drop Proposals), `src/app/{router.tsx,Sidebar.tsx,TopBar.tsx}`, `src/mocks/{proposals,notifications}.ts`, tests.
**Interfaces:** `/proposals`: pending list grouped by client, each card (kind pill, target doc link, "Agent-assisted" badge, payload body plain text / edit textarea, relation, visibility) with Approve / Edit (approve with `{body, visibility, shares}`) / Reject; select + "Approve selected" / "Approve all" → bulk-approve (≤100, report `failed[]`); status filter (pending/approved/rejected). Sidebar badge = pending total. Bell: unread count badge, popover with latest 10 (`describe(kind, payload)` → plain sentence + link), "Mark all read", "See all" → `/notifications` (keyset "Load more", unread filter, mark read on click).
- [ ] Tests: approve/edit/reject bodies; bulk with partial failure toast; badge counts; bell unread + mark-all body `{all:true}`; unknown notification kind renders generic text; payload strings never as HTML.
- [ ] **Commit** `feat(web): agent proposals inbox and notifications`

### Task 5: Admin shell, connectors, jobs, review queue
**Files:** `src/api/admin.ts`, `src/features/admin/{AdminLayout.tsx,api.ts,ConnectorsPage.tsx,ConnectorForm.tsx,ConnectorRuns.tsx,JobsPage.tsx,ReviewPage.tsx}`, `src/features/uploads/{NeedsTitleQueue,UploadsPage}.tsx` (queue moves to `/admin/needs-title`), `src/routes/AdminRoute.tsx`, `src/app/{router.tsx,Sidebar.tsx}` (Admin entry when admin), `src/mocks/adminOps.ts`, tests.
**Interfaces:** `/admin` → `/admin/connectors`; sub-nav: Connectors, Jobs, Review, Needs title, Settings, Tags, Audit, Users, Stats, Health. Connectors: table, create/edit dialog per kind (s3 secret write-only shows `***`), enable toggle, schedule, Run now (202/409), runs list (keyset) with status + stats, run detail log (`<pre>` plain text). Jobs: summary counts, filter status/kind, retry one, retry all failed. Review: near-dup queue paged, decide via `POST /documents/{doc}/versions/{v}/near-dup`.
- [ ] Tests: non-admin sees "Admins only" and no sidebar entry; connector create body per kind; PATCH keeps `***`; run 409 toast; job retry; review decide; needs-title queue only under admin.
- [ ] **Commit** `feat(web): admin connectors, jobs and review queue`

### Task 6: Admin settings, tags, audit, users, stats, health
**Files:** `src/features/admin/{SettingsPage,TagsPage,AuditPage,UsersPage,StatsPage,HealthPage}.tsx`, `src/mocks/adminOps.ts` (or `adminData.ts`), tests.
**Interfaces:** Settings: one row per key with value input, source badge (env/override), Save (PUT `{key: value}`), Reset (`null`), 422 detail inline. Tags: list with counts, multi-select sources + target → merge. Audit: keyset list, filter action prefix/user, details as plain text. Users: search + paging, groups, "Revoke tokens" (DELETE `/admin/users/{id}/pats`, confirm). Stats + health cards (status colours from tokens).
- [ ] Tests: settings PUT/reset bodies and 422; tag merge body; audit filters in query; revoke confirm + DELETE; health degraded state.
- [ ] **Commit** `feat(web): admin settings, tags, audit, users, stats and health`
