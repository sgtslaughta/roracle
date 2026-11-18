# Help modal + onboarding checklist Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** In-app Help modal rendered from `docs/user/*.md` (one source with the /docs site) plus a server-tracked "Get started" checklist on the library.

**Architecture:** Backend: `onboarding_progress` table, `roracle/onboarding.py` (`mark()` upsert with a per-process "already done" cache), hooks at the real events, `GET/PUT /me/onboarding`. Frontend: `features/help/*` (manifest, Markdown segment parser, lazy modal with tree nav, hash deep links, Try-it actions) and `features/onboarding/*` (card). Docs: restructured `docs/user/*.md` using Zensical-safe syntax that the in-app renderer also understands.

**Tech Stack:** FastAPI, SQLAlchemy, Alembic, Postgres; React 19, TanStack Router/Query, Radix Dialog, react-markdown + remark-gfm, Vite `import.meta.glob`, vitest + MSW; Zensical docs.

**Spec:** `/tmp/claude-1000/-home-user-code-research-rag/0173bda9-2e20-42fd-b0e1-9e49c9882037/scratchpad/help-brief.md` (decisions 1-10 binding) and the mockup `docs/superpowers/specs/2026-11-17-help-mockup.html`.

## Global Constraints

- Work only in `/home/user/code/research_rag/.worktrees/help` (branch `feat/help-onboarding`). No push, no merge, never bare `git stash`.
- Every file < 500 lines. Conventional Commits; trailer `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Backend tests: `export ROR_DATABASE_URL=postgresql+psycopg://roracle:roracle@localhost:55433/roracle_help_test` (create DB if missing), `ROR_ELASTIC_URL=http://localhost:29201`, integration under `flock /tmp/roracle-integ.lock`. Run suites to a log file in the foreground; no sleep/pgrep loops.
- Lint: backend `uv run ruff check . && uv run ruff format --check .`; frontend `pnpm exec tsc --noEmit && pnpm exec vitest run`; docs `scripts/docs/check.sh`.
- Do not invent product facts. Verified facts: MCP is Streamable HTTP, stateless JSON, `POST <origin>/mcp`, header `Authorization: Bearer <token>`. Token scopes are only `read` and `write` (UI: "Read only" / "Read and write"); there is NO `manage` scope and NO whoami tool. Labels are "General", "Exclude from AI", "Block agents". Scope failure text is `403 insufficient scope`; 429 carries `Retry-After`; 401 = token wrong/expired/revoked/owner stale. Handoff codes display as `XXXX-XXXX`.
- Air-gap: no external URLs loaded by the bundle; screenshots come from `docs/assets/screens/*.png` as Vite assets.
- Example origin in docs is `https://roracle.example.internal`; the help renderer replaces it with `window.location.origin`.

## Review Focus

1. User presses `?` while typing in the search box or a textarea: nothing opens (test in Task 6).
2. A `#help-unknown` hash or a `#help-<admin topic>` for a non-admin: help opens on the route's topic, never crashes, never shows admin content (test in Task 6).
3. A rolled-back request after `mark()` must not leave the step permanently cached as done in that process (cache only rows confirmed already present; test in Task 2).
4. PATs disabled site-wide: token/MCP checklist steps and Agents topics `mcp`/`trouble` hidden; card's agent group then has only "Hand a chat to an agent" (test in Tasks 3 and 7).
5. Closing help must clear the hash without breaking TanStack Router's history state (`history.replaceState(history.state, "", pathname+search)`; test in Task 6).

---

### Task 1: Docs content restructure (Markdown source of truth)

**Files:**
- Modify: all of `docs/user/*.md`, `docs/user/README.md` (page list), `zensical.toml` only if a nav entry is needed.
- Create: `docs/user/first-hour.md`, `docs/user/sharing-help.md`, `docs/user/agents-mcp-setup.md`, `docs/user/agents-troubleshooting.md`, `docs/user/keyboard-shortcuts.md`.

**Interfaces (the Markdown dialect the in-app renderer, Task 5, parses; all of it must build in Zensical and pass pymarkdown):**
- Line 1 `# <Topic title>`; the first paragraph after it is the **lede** (one or two sentences, plain).
- Sections: `##` (and optional `###`). Slugs follow python-markdown toc.
- Callouts: `!!! tip` or `!!! warning` (optional `"Title"`), body on the NEXT line indented 4 spaces, single paragraph, no blank line between.
- Definition lists: `Term` line, next line `:   definition` (def_list).
- Titled snippets: fenced block with `title="Label"`, e.g. ```` ```text title="Search box" ````.
- Client tabs: wrap titled fences in `<!-- tabs -->` … `<!-- /tabs -->`; fence title `"Claude Code | Terminal"` = tab name before ` | `, caption after.
- Walkthrough: wrap in `<!-- steps -->` … `<!-- /steps -->`; each `### Heading` inside starts one numbered step.
- Try-it: `<!-- try:ACTION label="Button text" hint="optional" q='optional query' -->` where ACTION ∈ `search|ask|uploads|handoff|token|card|docs`.
- Images: `![Caption sentence](../assets/screens/<file>.png)` (alt text = caption).

- [ ] **Step 1:** Restructure every existing `docs/user` page: add lede, convert 2-column "term / meaning" tables to definition lists where natural, add 1-2 `!!! tip` / `!!! warning` callouts where the mockup has them, keep all facts and existing anchors that other pages link to (check_links enforces). Topic H1s: getting-started → `# Welcome to rOracle`; library-search → `# Finding papers`; reader → `# Reading a paper`; chat → `# Asking questions`; uploads → `# Uploading papers`; agents-and-tokens → `# Agents and tokens`; handoffs-inbox → `# Hand work to an agent`. Others keep their H1. Sidebar row "Docs" in getting-started's layout table becomes "Help"; mention `?` opens help.
- [ ] **Step 2:** `handoffs-inbox.md`: put a 4-step `<!-- steps -->` walkthrough first (Start from what you're doing — "Continue in agent" on a chat, search or paper, screenshot `handoff-dialog.png`; Paste the prompt into your agent — titled snippet `Prompt for your agent`; What is left out — withheld text / invisible papers; Review what comes back — Agent proposals), then `<!-- try:handoff label="Hand the open paper to an agent" hint="Open a paper first." -->`, then the existing sections (proposals, notifications) unchanged in substance.
- [ ] **Step 3:** `agents-mcp-setup.md` `# Set up MCP, step by step`, lede "Connect an AI agent such as Claude Code or Claude Desktop to rOracle. About ten minutes.", `!!! tip` "You need an MCP client…", then 6 steps in `<!-- steps -->`: Know what the agent will see (link agents-and-tokens.md); Create a token (Read only vs Read and write, expiry, `tokens-create.png`, `<!-- try:token label="Create a read and write token" hint="Opens the dialog with Read and write selected." -->`); Add rOracle to your client (`<!-- tabs -->` with `Claude Code | Terminal` = `claude mcp add --transport http roracle https://roracle.example.internal/mcp --header "Authorization: Bearer $RORACLE_TOKEN"` (preceded by `export RORACLE_TOKEN=<your token>`), `Claude Desktop | claude_desktop_config.json` = the JSON from agents-and-tokens.md, `Other client | Connection settings` = Transport Streamable HTTP / URL / Header); Check the connection (snippet `Prompt`: `Use rOracle to list my collections.` — it ticks the checklist on first use); Hand it some work (link handoffs-inbox.md); Review its suggestions. Verify the Claude Desktop JSON against current Claude docs via context7 if unsure; if Desktop needs a bridge for remote HTTP servers say so plainly rather than inventing.
- [ ] **Step 4:** `agents-troubleshooting.md` `# Troubleshooting agents`: sections `401 Unauthorized`, `403 Insufficient scope`, `429 Too many requests`, `Paper not found` with the verified causes (see Global Constraints, "withheld by label" 403 too).
- [ ] **Step 5:** `first-hour.md` `# Your first hour` (Basics: 4 steps; Connect your agents: 3 steps, exact labels from Task 7; `<!-- try:card label="Show the Get started card" hint="Brings back the card if you hid it." -->`). `sharing-help.md` `# Sharing help topics` (Copy link to this topic; snippet `Example link` = `https://roracle.example.internal/library#help-agents-mcp-setup`; tip that links don't share papers). `keyboard-shortcuts.md` `# Keyboard shortcuts`: move the shortcuts section out of getting-started (leave a one-line link there, fix inbound anchors), add `?` = Help on this page. `library-search.md` gets a `search` try with a query that the parser supports (q='"multi-agent" -survey'); `chat.md` gets `<!-- try:ask label="Ask about the open paper" -->`; `uploads.md` gets `<!-- try:uploads label="Open My uploads" -->`.
- [ ] **Step 6:** Run `scripts/docs/check.sh` → ends `docs checks OK`. Commit `docs(user): restructure user guide as help topics`.

### Task 2: Backend onboarding model, migration, `mark()`

**Files:**
- Create: `backend/migrations/versions/0022_onboarding.py` (revision `"0022"`, down_revision `"0020"` — renumber at merge time if 0021 has landed), `backend/src/roracle/onboarding.py`, `backend/tests/test_onboarding.py`.
- Modify: `backend/src/roracle/models_saved.py` (add `OnboardingProgress`; small, same "per-user prefs" family).

**Interfaces — Produces:**
- Table `onboarding_progress(user_id UUID PK FK users.id ON DELETE CASCADE, steps text[] NOT NULL DEFAULT '{}', dismissed bool NOT NULL DEFAULT false, updated_at timestamptz NOT NULL DEFAULT now())`.
- `STEPS: tuple[str, ...] = ("search", "ask", "upload", "collect", "token", "mcp", "handoff")`
- `mark(session: Session, user_id: UUID, step: str) -> None` — no-op if `(user_id, step)` in module `_known`; else `INSERT … ON CONFLICT (user_id) DO UPDATE SET steps = array_append(…), updated_at = now() WHERE NOT (:s = ANY(onboarding_progress.steps))`; if rowcount == 0 (already present) add to `_known` (an insert/append is not cached, so a rollback cannot poison the cache). Runs in the caller's transaction; errors propagate. Unknown step → `ValueError`.
- `state(session, user_id) -> dict` → `{"steps": [...ordered by STEPS], "dismissed": bool}` (no row → `[]`, `False`).
- `set_dismissed(session, user_id, dismissed: bool) -> None` (upsert).
- `reset_cache() -> None` for tests.

- [ ] **Step 1: failing tests** in `test_onboarding.py`: `test_mark_appends_once` (mark twice → steps `["search"]`); `test_mark_order_and_state` (mark collect then search → `state` returns `["search","collect"]`); `test_rollback_not_cached` (mark, `session.rollback()`, mark again, commit → step present); `test_known_cache_skips_db` (after a second mark that found it present, a third mark issues no SQL — assert via `event.listen(engine, "before_cursor_execute")` counter); `test_unknown_step_raises`; `test_set_dismissed_roundtrip`; `test_migration_single_head` (ScriptDirectory heads == ["0022"], like test_saved_searches).
- [ ] **Step 2:** run `uv run pytest tests/test_onboarding.py -q` → FAIL (import error).
- [ ] **Step 3:** implement migration, model, module.
- [ ] **Step 4:** tests PASS; ruff clean.
- [ ] **Step 5:** commit `feat(onboarding): progress table and idempotent step marking`.

### Task 3: Onboarding API + event hooks

**Files:**
- Create: `backend/src/roracle/api/routes/onboarding.py`, `backend/tests/test_onboarding_api.py`.
- Modify: `backend/src/roracle/api/app.py` (include router), `api/routes/search.py` (`search`: when `f.mode != "quick"` and `f.q.strip()` → `mark(session, p.user_id, "search")` before branching), `api/routes/chats.py` (`post_message` → `"ask"`), upload path shared by `POST /documents` and MCP `upload` (`api/routes/documents.py` `accept()` or wherever both converge → `"upload"` after the version is created), `api/routes/library.py` `create_collection` → `"collect"` (before commit), `api/routes/tokens.py` `create_token` → `"token"`, `handoff.py` `create()` → `"handoff"` (covers UI and MCP), `mcp/auth.py` `_resolve` → if `p.via == "pat"`: `"mcp"` before `session.commit()`.

**Interfaces — Consumes:** Task 2. **Produces:**
- `GET /me/onboarding` → `{"steps": string[], "dismissed": bool, "pats_enabled": bool}` (`pats_enabled` from `get_app_settings`, the runtime-effective settings).
- `PUT /me/onboarding` body `{"dismissed": bool}` → same shape.

- [ ] **Step 1: failing tests**: `test_get_default` (new user → `[]`, false, true); `test_put_dismiss_and_reopen`; `test_pats_disabled_flag` (runtime setting off → `pats_enabled` false); one test per hook: search with q ticks `search`, quick-mode does not; posting a chat message ticks `ask`; upload ticks `upload`; create collection ticks `collect`; create token ticks `token`; MCP call with a PAT ticks `mcp` (use `tests/mcp_helpers.py`); handoff create ticks `handoff`; user A's events never tick user B. Reuse fixtures from existing tests (`client`, `as_ana`, search/upload/mcp helpers in `test_search_api.py`, `test_my_uploads.py`, `test_mcp.py`, `test_handoff.py`). Put any test needing ES in the integration set the same way neighbours do.
- [ ] **Step 2:** run → FAIL.
- [ ] **Step 3:** implement router + hooks (one line each where possible).
- [ ] **Step 4:** run new tests + the touched neighbours' test files → PASS; ruff clean.
- [ ] **Step 5:** commit `feat(onboarding): API and step hooks at real events`.

### Task 4: Help store, manifest, content loader

**Files:**
- Create: `frontend/src/features/help/helpStore.ts`, `frontend/src/features/help/manifest.ts`, `frontend/src/features/help/content.ts`, `frontend/src/features/help/manifest.test.ts`.
- Modify: `frontend/vite.config.ts` (alias `@docs` → `../docs`; `server.fs.allow: [".", "../docs"]`), `frontend/Dockerfile` (build stage: `COPY docs/user /docs/user`, `COPY docs/glossary.md /docs/glossary.md`, `COPY docs/admin /docs/admin`, `COPY docs/assets/screens /docs/assets/screens` before `pnpm build`; `/app/../docs` = `/docs`), `frontend/tsconfig*.json` paths for `@docs` if needed.

**Interfaces — Produces:**
- `helpStore`: `createStore<{ open: boolean; topic: string | null; section: string | null }>`; `openHelp(topic?: string, section?: string)`, `closeHelp()`, `useHelp()`.
- `type Group = "Start here" | "Using rOracle" | "Agents" | "Reference" | "Admin"`; `GROUPS: { name: Group; icon: LucideIcon }[]`.
- `interface Topic { id: string; group: Group; file: string; routes?: string[]; section?: string; admin?: boolean; pats?: boolean }` and `TOPICS: Topic[]`. id = file basename without `.md` (admin files prefixed `admin-`). Order and groups: Start here: getting-started, first-hour, sharing-help. Using rOracle: library-search (`/library`), reader (`/d/`), chat (`/ask`), notes-annotations, labels, uploads (`/uploads`), collections-sharing, writing-drafts (`/drafts`). Agents: agents-and-tokens (`/settings`), agents-mcp-setup (pats), handoffs-inbox (`/proposals`, `/notifications`, `/h/`), agents-troubleshooting (pats). Reference: keyboard-shortcuts, glossary (`docs/glossary.md`). Admin (admin): admin-README (`/admin`), admin-labels, admin-runtime-settings, admin-users-groups.
- `visibleTopics(isAdmin: boolean, patsEnabled: boolean): Topic[]`; `topicForPath(pathname: string): string` (longest matching route prefix, else `"getting-started"`); `findTopic(id: string, isAdmin: boolean, patsEnabled: boolean): Topic | undefined`.
- `content.ts`: `loadTopic(id): Promise<string>` and `loadAll(): Promise<Record<string,string>>` via non-eager `import.meta.glob(["@docs/user/*.md","@docs/glossary.md","@docs/admin/*.md"], { query: "?raw", import: "default" })`; `screenshot(file: string): string | undefined` via eager `import.meta.glob("@docs/assets/screens/*.png", { query: "?url", import: "default", eager: true })` keyed by basename; `docsUrl(relPathFromDocsRoot: string): string` → `/docs/<path without .md>/` (README → directory).
- `topicTitle(md: string): string` (H1 text).

- [ ] **Step 1: failing tests**: every TOPICS file exists in the glob; `visibleTopics(false,true)` has no Admin; `visibleTopics(true,false)` lacks pats topics; `topicForPath("/library")` = library-search, `"/d/x/v/2"` = reader, `"/zzz"` = getting-started; `findTopic("admin-README", false, true)` undefined.
- [ ] **Step 2-4:** fail → implement → pass (`pnpm exec vitest run src/features/help`).
- [ ] **Step 5:** commit `feat(help): manifest, store and bundled doc content`.

### Task 5: Markdown segment parser + HelpBody renderer

**Files:**
- Create: `frontend/src/features/help/parse.ts`, `frontend/src/features/help/parse.test.ts`, `frontend/src/features/help/HelpBody.tsx`, `frontend/src/features/help/Snippet.tsx`, `frontend/src/styles/help.css` (import it where the other feature CSS is imported).

**Interfaces — Consumes:** Task 4 (`screenshot`, `docsUrl`, `TOPICS`). **Produces:**
- `slugify(text: string): string` (python-markdown toc: NFKD, strip non `[\w\s-]`, lowercase, `[-\s]+` → `-`).
- `type Segment = { kind: "md"; text: string } | { kind: "callout"; tone: "tip" | "warning" | "note"; title: string; text: string } | { kind: "dl"; items: { term: string; def: string }[] } | { kind: "steps"; steps: { title: string; id: string; body: Segment[] }[] } | { kind: "tabs"; tabs: { name: string; caption: string; lang: string; code: string }[] } | { kind: "try"; action: TryAction; label: string; hint?: string; q?: string }`; `type TryAction = "search" | "ask" | "uploads" | "handoff" | "token" | "card" | "docs"`.
- `parseTopic(md: string): { title: string; lede: string; segments: Segment[]; sections: { id: string; title: string; depth: 2 | 3 }[] }` — ignores markers inside fenced code; callout default titles: tip "Tip", warning "Heads up", note "Note".
- `<HelpBody md: string; onGo(topic: string, section?: string): void; onTry(seg): void />`: react-markdown (reuse `MarkdownBody`) with components: h2/h3 with `id={slugify}`; `img` → `<figure className="shot">` using `screenshot(basename)` (unknown → alt text only), figcaption = alt; `a` → same-folder `x.md#y` that is a topic → button-like link calling `onGo`, other relative `.md` → `docsUrl` with `target="_blank"`, else `safeHref`; `pre` with fence meta `title="…"` → `<Snippet label code />` (Copy button, toast "Copied." / "Copying isn't available here. Select the text instead."). All text and code: replace `https://roracle.example.internal` with `location.origin`. Steps → `<ol className="walk">`, tabs → Radix-free `role="tablist"` buttons + Snippet. Lede rendered `.lede` under `<h1>`.
- CSS: port the mockup's `.snav/.tree/.secs/.body/.lede/.dl/.snip/.shot/.call/.walk/.tabs/.try/.pager/.crumb` rules into `help.css` using existing tokens (`--muted-bg`, `--primary-soft`, `--tray`, `--f-read`…); add `--guide` / `--warn-bg` / `--code-bg` to `tokens.css` light+dark with the mockup values.

- [ ] **Step 1: failing parse tests**: lede extraction; callout `!!! warning` → tone warning, title "Heads up"; dl of 2 items; tabs split on ` | `; steps with 2 `###` → 2 steps with ids; try attrs incl. `q='"a b" -c'`; `!!! tip` inside a ``` fence stays code; sections list h2+h3 with slugs (`"401 Unauthorized"` → `401-unauthorized`).
- [ ] **Step 2: failing render test** (`HelpBody.test.tsx`): image renders `<img>` with a bundled src and caption; Copy button writes the snippet (with origin substituted) to `navigator.clipboard`; link `labels.md#x` calls `onGo("labels","x")`; link `../reference/mcp.md` has `href="/docs/reference/mcp/"`.
- [ ] **Step 3-4:** implement → pass.
- [ ] **Step 5:** commit `feat(help): render doc Markdown with callouts, snippets, steps and tabs`.

### Task 6: Help dialog, tree nav, open/hash/keys, Try-it actions

**Files:**
- Create: `frontend/src/features/help/HelpDialog.tsx`, `HelpTree.tsx`, `HelpHost.tsx`, `useHelpHash.ts`, `actions.ts`, `help.test.tsx`.
- Modify: `frontend/src/app/Shell.tsx` (render `<HelpHost />`, call `useHelpHash()`), `frontend/src/app/TopBar.tsx` (`?` icon button, `CircleHelp`, aria-label "Help", title "Help (?)", before the Command button), `frontend/src/app/Sidebar.tsx` (Docs link → `<button>` "Help" with `CircleHelp`, `onClick={() => openHelp()}`), `frontend/src/lib/keys.ts` (`?` → `openHelp()`, same guards as `/`), `frontend/src/features/agents/CreateTokenDialog.tsx` (optional prop `initialWrite?: boolean`).

**Interfaces — Consumes:** Tasks 4-5, `useMe`, `useOnboarding`/`setDismissed` from Task 7 (Task 7 lands after; for `card` use a `reopenCard()` export defined in Task 7 — implement Task 6 with a local `apiFetch("/me/onboarding", {method:"PUT", body:{dismissed:false}})` + `queryClient.invalidateQueries({queryKey:["onboarding"]})` and Task 7 reuses it), `HandoffDialog`, `CreateTokenDialog`, reader store, `unignore`, `focusComposer` (from ask context).
**Produces:**
- Hash format `#help-<topic>` or `#help-<topic>--<section>`. `useHelpHash()`: on mount and `hashchange`, parse → `openHelp(topic, section)`; while help is open, `go()` writes `#help-<topic>` via `history.replaceState(history.state, "", pathname + search + hash)`; `closeHelp()` clears the hash the same way.
- `runTry(seg, ctx)` in `actions.ts`: `search` → close, navigate `/library` with `search: { q }`; `ask` → close, if a reader tab is active `unignore(id)`, navigate `/ask`, `focusComposer()`; `uploads` → close, navigate `/uploads`; `handoff` → close, set `pendingDialog = { kind: "handoff", source }` for the active reader doc (button disabled with hint when no doc is open); `token` → close, `pendingDialog = { kind: "token" }` rendered as `<CreateTokenDialog initialWrite />`; `card` → PUT dismissed false, invalidate, close, navigate `/library`; `docs` → `window.open("/docs/", "_blank", "noopener")`.
- Dialog: Radix `Dialog` + `DialogContent bare` with class `help-sheet` (1140px × min(84vh, 800px), phone full-screen), `DialogTitle` "Help", search input "Search help" filtering topics by title + raw text (loaded with `loadAll()` on open; case-insensitive), empty text `No topic matches “q”. Try fewer words.`, "Open full docs" (`/docs/`, new tab, hidden on phone), close button aria-label "Close help (Esc)". Tree: `<details>` per group (icon, name, count); topic buttons with `aria-current`, "this page" pill when `topicForPath(location.pathname) === id`; current topic expands its sections (dashed) and clicking one scrolls `#<id>` into view in the body; Arrow Up/Down move focus between tree buttons. Phone: `<select aria-label="Help topic">` with optgroups. Body: crumb `Help / <group>` + "Copy link to this topic" (copies `${origin}${pathname}#help-<id>`, toast "Link copied. Anyone who opens it lands on this topic."), H1, lede, HelpBody, pager Previous/Next over visible topics. Open topic default: `topicForPath(pathname)`; unknown or not visible topic → that default.

- [ ] **Step 1: failing tests** (`help.test.tsx`, render the app router like `shell.a11y.test.tsx`): `?` key opens help on the route's topic and `?` inside the search input does not; TopBar Help button opens; Sidebar Help opens; `location.hash = "#help-chat"` opens "Asking questions"; `#help-admin-README` for a non-admin opens the route default; clicking a group summary collapses it; clicking a section button calls `scrollIntoView` (mock it); typing "troubleshoot" leaves only the troubleshooting topic; admin user sees the Admin group, non-admin does not; Copy link writes `…#help-<id>`; Esc closes and clears the hash; Try "Open My uploads" navigates to `/uploads` and closes.
- [ ] **Step 2-4:** fail → implement → pass; `pnpm exec tsc --noEmit` clean.
- [ ] **Step 5:** commit `feat(help): help modal with topic tree, deep links and try-it actions`.

### Task 7: Get started card + mocks

**Files:**
- Create: `frontend/src/features/onboarding/api.ts`, `frontend/src/features/onboarding/GetStarted.tsx`, `frontend/src/features/onboarding/onboarding.test.tsx`, `frontend/src/mocks/onboarding.ts`.
- Modify: `frontend/src/mocks/handlers.ts` (register handlers), `frontend/src/features/library/SearchPage.tsx` (render `<GetStarted />` above results), `frontend/src/features/help/actions.ts` (use `reopenCard`).

**Interfaces — Consumes:** Task 3 API, `openHelp`. **Produces:**
- `useOnboarding()` (queryKey `["onboarding"]`, `refetchOnWindowFocus: true`), `setDismissed(dismissed: boolean): Promise<void>`, `reopenCard()`.
- Steps (exact copy): Basics — `search` "Search the library" / "Try a phrase in quotes." / topic library-search; `ask` "Open a paper and ask about it" / "Ask about this, in the reader." / chat; `upload` "Upload a paper" / "PDF or Word. Duplicates are caught." / uploads; `collect` "Save a collection" / "Group papers for a project." / collections-sharing. Agents — `token` "Create an access token" / "Your agent signs in with it." / agents-mcp-setup (section create-a-token); `mcp` "Connect an agent with MCP" / "Paste the config into your client." / agents-mcp-setup; `handoff` "Hand a chat to an agent" / "Continue in agent, from any chat." / handoffs-inbox. When `pats_enabled` is false drop `token` and `mcp`.
- Card: heading "Get started" until all Basics done, then "Connect your agents"; `n of m` progress, bar, ticks are status icons (not buttons) with sr text "Done:"/"Not done yet:"; "Show me" → `openHelp(topic, section)`; footer while in Basics: "Next: connect your agents. It opens when these four are done."; ✕ aria-label "Hide Get started. Reopen it from Help." → `setDismissed(true)` + toast "Hidden. Reopen it from Help, Your first hour."; hidden when dismissed, when all steps done, or while loading/error.

- [ ] **Step 1: failing tests**: renders 4 basics with 1 done → "1 of 4"; all basics done → "Connect your agents" with 3 steps; pats disabled → 1 agent step; Show me opens help on the right topic; ✕ sends PUT `{dismissed:true}` and hides; dismissed → not rendered.
- [ ] **Step 2-4:** fail → implement → pass.
- [ ] **Step 5:** commit `feat(onboarding): Get started checklist card on the library`.

### Task 8: Merge main, full verification

- [ ] **Step 1:** `git fetch origin && git merge origin/main`; if another `0021`/`0022` migration landed, renumber ours to the next free number with `down_revision` = current head; re-run `test_onboarding.py`.
- [ ] **Step 2:** backend ruff + unit + integration (flock), frontend tsc + vitest + `pnpm build && pnpm check:dist && pnpm check:size`, `scripts/docs/check.sh`. All green.
- [ ] **Step 3:** commit any fixes (`fix:`/`chore:`).
