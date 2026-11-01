# Decouple SessionView from URL projectId — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Inside `SessionView` and its subtree, separate "the session's project" (driven by `session.project_id`, used for API calls and data lookups) from "the sidebar filter project" (driven by `route.params.projectId`, used for router.push calls that preserve the current URL filter).

**Architecture:** Introduce two refs in `SessionView.vue`: `projectId` (now a `computed` reading `session.value?.project_id`) and `filterProjectId` (a frozen `ref` capturing `route.params.projectId` at creation time). Every `router.push` that rebuilds the current URL uses `filterProjectId`; everything else continues to reference `projectId`, whose meaning quietly shifts from URL to data. `SessionList.vue` inserts cross-filter sessions at the right sort position by re-applying the existing comparator (now exported from `data.js`).

**Tech Stack:** Vue 3 Composition API (SFC), Vue Router 4, Pinia.

**Spec:** `docs/superpowers/specs/2026-04-22-decouple-sessionview-from-url-projectid-design.md`

**Important constraints from CLAUDE.md:**
- No tests and no linting are required for this project; verification is **manual browser testing**.
- **Do not restart the dev server.** If the dev server is not running, ask the user to start it via `uv run ./devctl.py start` before starting the task. Claude must not run `devctl.py restart`.
- Check Vite compile errors via the dev log: `uv run ./devctl.py logs front --lines 40`.

---

## Pre-flight

- [ ] **Verify dev server is running**

```bash
uv run ./devctl.py status
```

Expected: both `frontend` and `backend` marked running on their configured ports (default 5173 / 3500 or worktree-specific). If either is stopped, ask the user to start them — **do not start them yourself** unless the user explicitly asks.

- [ ] **Open the TwiCC UI in a browser tab** at the frontend URL shown by `devctl status`, and pick one project with several sessions for testing throughout the plan.

---

## Task 1: Export `sessionSortComparator` from `data.js`

**Files:**
- Modify: `frontend/src/stores/data.js` (the `sessionSortComparator` factory, currently at ~line 50)

**Why:** `SessionList.vue` needs to re-sort a list that includes an out-of-filter session. The comparator currently lives module-level in `data.js` and is only used by the store getters. Exporting it lets `SessionList` call it directly.

- [ ] **Step 1: Add the `export` keyword**

In `frontend/src/stores/data.js`, locate the function declaration:

```js
function sessionSortComparator(processStates) {
```

Change it to:

```js
export function sessionSortComparator(processStates) {
```

Nothing else in the function body changes. The function remains a **factory**: it takes `processStates` and returns `(a, b) => ...`.

- [ ] **Step 2: Check for Vite compile error**

```bash
uv run ./devctl.py logs front --lines 20
```

Expected: no error trace after the save; typically an HMR update line like `[vite] hmr update /src/stores/data.js`.

- [ ] **Step 3: Smoke test in browser**

Reload the page. The sidebar should still list sessions exactly as before (the export adds a symbol but does not change any caller). If the sidebar is empty or throws, revert and investigate.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/stores/data.js
git commit -m "$(cat <<'EOF'
refactor(front): export sessionSortComparator for SessionList reuse

Prerequisite for letting SessionList.vue insert an out-of-filter session
at the correct sorted position instead of appending it at the end.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: Insert out-of-filter session at sorted position in `SessionList.vue`

**Files:**
- Modify: `frontend/src/components/SessionList.vue` (the `baseSessions` and `allSessions` computed around lines 49-94)

**Why:** The existing fallback at the end of `allSessions` appends the selected session to the list when it is outside the pagination window. After the SessionView decoupling (Task 3), the same fallback also catches sessions that don't match the sidebar filter (cross-filter bookmarks; future pin cross-filter). Appending at the end breaks the normal sort order (active process → pinned → mtime). Moving the fallback into `baseSessions` and re-applying the comparator fixes both the pre-existing pagination-window case and the new cross-filter case.

- [ ] **Step 1: Import the comparator**

At the top of the `<script setup>` block in `frontend/src/components/SessionList.vue`, update the existing data-store import. It currently reads:

```js
import { useDataStore, ALL_PROJECTS_ID } from '../stores/data'
```

Change to:

```js
import { useDataStore, ALL_PROJECTS_ID, sessionSortComparator } from '../stores/data'
```

- [ ] **Step 2: Move the fallback into `baseSessions`**

Locate the `baseSessions` computed (around line 50). It currently reads:

```js
const baseSessions = computed(() => {
    if (isWorkspaceProjectId(props.projectId)) {
        const wsId = extractWorkspaceId(props.projectId)
        const wsStore = useWorkspacesStore()
        const visibleIds = new Set(wsStore.getVisibleProjectIds(wsId))
        return store.getAllSessions.filter(s => visibleIds.has(s.project_id))
    }
    if (props.projectId === ALL_PROJECTS_ID) {
        return store.getAllSessions
    }
    return store.getProjectSessions(props.projectId)
})
```

Replace with:

```js
const baseSessions = computed(() => {
    const sessions = (() => {
        if (isWorkspaceProjectId(props.projectId)) {
            const wsId = extractWorkspaceId(props.projectId)
            const wsStore = useWorkspacesStore()
            const visibleIds = new Set(wsStore.getVisibleProjectIds(wsId))
            return store.getAllSessions.filter(s => visibleIds.has(s.project_id))
        }
        if (props.projectId === ALL_PROJECTS_ID) {
            return store.getAllSessions
        }
        return store.getProjectSessions(props.projectId)
    })()

    // Ensure the selected session is in the list, even when it falls outside the
    // sidebar filter or the pagination window. Re-sort with the same comparator so
    // it lands at the right position (active process / pinned / mtime order).
    if (props.sessionId && !sessions.some(s => s.id === props.sessionId)) {
        const s = store.sessions[props.sessionId]
        if (s && !s.parent_session_id) {
            return [...sessions, s].sort(sessionSortComparator(store.processStates))
        }
    }
    return sessions
})
```

- [ ] **Step 3: Remove the old append-at-end fallback from `allSessions`**

Locate the `allSessions` computed (currently lines 67-94 or nearby). Its current body ends with:

```js
    const filtered = baseSessions.value.filter(s =>
        (props.showArchived || !s.archived || s.id === props.sessionId) &&
        (!archivedProjectIds || !archivedProjectIds.has(s.project_id))
    )

    // Ensure the currently selected session is always in the list, even if it
    // falls outside the pagination window. The store getters filter by mtime bound
    // (only sessions >= oldestSessionMtime are returned while more pages exist).
    // A session navigated to from global search may be older than this bound but
    // still present in store.sessions from a previous project-scoped load.
    if (props.sessionId && !filtered.some(s => s.id === props.sessionId)) {
        const session = store.sessions[props.sessionId]
        if (session && !session.parent_session_id) {
            return [...filtered, session]
        }
    }

    return filtered
})
```

Remove the fallback block; keep only:

```js
    const filtered = baseSessions.value.filter(s =>
        (props.showArchived || !s.archived || s.id === props.sessionId) &&
        (!archivedProjectIds || !archivedProjectIds.has(s.project_id))
    )

    return filtered
})
```

Note: the `s.id === props.sessionId` exception inside the archived filter **stays** — that keeps an archived selected session visible when "show archived" is off.

- [ ] **Step 4: Check for Vite compile error**

```bash
uv run ./devctl.py logs front --lines 20
```

Expected: clean HMR update, no error.

- [ ] **Step 5: Smoke test — normal session list**

Reload the page. Pick a project with at least 5 sessions. Verify the sidebar still lists them in the expected order:
1. Sessions with active Claude processes (if any) at the very top
2. Pinned sessions next
3. Other sessions by mtime descending

Click between 2-3 different sessions. The order should stay stable; the active session remains highlighted.

- [ ] **Step 6: (Optional) Smoke test — older session out of pagination window**

If the test project has many sessions (more than a page), find an old session via global search or URL and navigate to it. Before this change, it would appear at the end of the sidebar list. After this change, it should appear at the correct mtime position. This is a nice-to-have check; skip if no such session is easily accessible.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/components/SessionList.vue
git commit -m "$(cat <<'EOF'
refactor(front): move selected-session fallback to SessionList.baseSessions

Previously the selected session was appended at the end of the list when it
fell outside the pagination window. Move this into baseSessions and re-apply
the sort comparator so it lands at the correct mtime / pinned / active-process
position.

Preparation for cross-filter session support: after the SessionView decoupling,
the same fallback will also handle sessions whose project does not match the
sidebar filter (direct bookmarks, future pin cross-filter clicks).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: Split `projectId` and `filterProjectId` in `SessionView.vue`

**Files:**
- Modify: `frontend/src/views/SessionView.vue` (refs around lines 143-170; router.push sites at ~259, 273, 373, 384, 641, 652, 862)

This task has three sub-steps, each safe to commit independently:

- **3a**: Add `filterProjectId` alongside the existing `projectId` ref — no behavior change.
- **3b**: Rewire every `router.push`/`router.replace` that rebuilds the URL for the current session to use `filterProjectId.value`. Still no behavior change (both refs hold the same value here).
- **3c**: Change `projectId` from a frozen `ref` into a `computed` reading `session.value?.project_id`. This is where the decoupling actually happens.

Splitting this way means every intermediate commit leaves the app in a working state.

---

### Task 3a: Introduce `filterProjectId` (no behavior change)

- [ ] **Step 1: Add the new ref and update the comment**

Locate the block around lines 143-151 of `frontend/src/views/SessionView.vue`:

```js
// Current session from route params
// IMPORTANT: projectId and sessionId are captured at creation time (not reactive
// computeds from route.params) because with KeepAlive, the route changes globally
// when switching sessions. If these were reactive, ALL cached SessionView instances
// would see the NEW session's params, breaking deactivation hooks and item lookups.
// The KeepAlive key (route.params.sessionId) ensures each instance gets the correct
// value at creation time and keeps it permanently.
const projectId = ref(route.params.projectId)
const sessionId = ref(route.params.sessionId)
```

Replace with:

```js
// Current session from route params
// IMPORTANT: these refs are captured at creation time (not reactive computeds
// from route.params) because with KeepAlive, the route changes globally when
// switching sessions. If they were reactive, ALL cached SessionView instances
// would see the NEW session's params, breaking deactivation hooks and item lookups.
// The KeepAlive key (route.params.sessionId) ensures each instance gets the correct
// value at creation time and keeps it permanently.
//
// filterProjectId is the project the sidebar filter was on when this SessionView
// was created. It is used only by router.push calls that rebuild the current
// URL, so that switching tabs (main / subagent / files / git / terminal) never
// changes the sidebar filter — even when the session lives in a different
// project than the filter (cross-filter bookmarks, future pin cross-filter).
//
// projectId (declared further down, after `session`) is the project the session
// belongs to, driven by `session.project_id`. It is used for API calls, code-
// comments lookups, and WS payloads.
const filterProjectId = ref(route.params.projectId)
const projectId = ref(route.params.projectId)
const sessionId = ref(route.params.sessionId)
```

At this stage both refs hold the same value; `projectId` is still the URL-bound ref it was before. We introduce `filterProjectId` first to give 3b a clean target.

- [ ] **Step 2: Check for Vite compile error**

```bash
uv run ./devctl.py logs front --lines 20
```

Expected: clean HMR update.

- [ ] **Step 3: Smoke test in browser**

Reload. Open a session, switch between tabs (main, files, git, terminal), open a subagent tab if available, close it. Everything should work exactly as before — no visible change.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/views/SessionView.vue
git commit -m "$(cat <<'EOF'
refactor(front): introduce filterProjectId ref in SessionView

Step 1/3 of decoupling SessionView from the URL projectId: add a new ref
filterProjectId that captures route.params.projectId at creation time, alongside
the existing projectId ref. No behavior change — both refs hold the same value.
Next commits will rewire router.push calls to filterProjectId and turn projectId
into a computed reading session.project_id.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 3b: Point navigation calls at `filterProjectId`

This step locates every `router.push` / `router.replace` inside `SessionView.vue` that rebuilds the current session's URL and switches its `projectId` param from `projectId.value` to `filterProjectId.value`.

The sites are listed in the spec (section *Navigation that preserves the sidebar filter*). Line numbers below are approximate — search for the patterns rather than relying strictly on the numbers.

- [ ] **Step 1: Git-tab redirect watcher (around line 259)**

Find the block that looks like:

```js
router.replace({
    name: route.name?.startsWith('projects-')
        ? 'projects-session'
        : 'session',
    params: { projectId: projectId.value, sessionId: sessionId.value }
})
```

Change `projectId: projectId.value` to `projectId: filterProjectId.value`.

- [ ] **Step 2: `navigateInTab` helper (around line 273)**

The helper uses `projectId.value` when constructing route params for internal tab navigation. Change the single use of `projectId.value` in that function to `filterProjectId.value`.

- [ ] **Step 3: `switchToTab` — navigate to `main` (around line 373)**

Find:

```js
router.push({
    name: route.name?.startsWith('projects-') ? 'projects-session' : 'session',
    params: { projectId: projectId.value, sessionId: sessionId.value }
})
```

Change `projectId: projectId.value` to `projectId: filterProjectId.value`.

- [ ] **Step 4: `switchToTab` — navigate to a subagent (around line 384)**

Find:

```js
router.push({
    name: route.name?.startsWith('projects-') ? 'projects-session-subagent' : 'session-subagent',
    params: { projectId: projectId.value, sessionId: sessionId.value, subagentId: agentId }
})
```

Change `projectId: projectId.value` to `projectId: filterProjectId.value`.

- [ ] **Step 5: `closeTab` — navigate to previous subagent (around line 641)**

Same pattern: change `projectId: projectId.value` to `filterProjectId.value`.

- [ ] **Step 6: `closeTab` — navigate to `main` (around line 652)**

Same pattern: change `projectId: projectId.value` to `filterProjectId.value`.

- [ ] **Step 7: `delete-draft` command palette action (around line 862)**

Find:

```js
router.push({ name: 'project', params: { projectId: projectId.value } })
```

Change to:

```js
router.push({ name: 'project', params: { projectId: filterProjectId.value } })
```

This is the one call inside a session-data-oriented command that uses the filter-role value, because after deleting the draft it returns to the sidebar's current project.

- [ ] **Step 8: Search for any remaining `projectId.value` in `router.push` or `router.replace`**

Use the Grep tool with a multiline pattern to catch calls that span several lines:

- Pattern: `router\.(push|replace)[\s\S]*?projectId: projectId\.value`
- Path: `frontend/src/views/SessionView.vue`
- Multiline: true

Expected result: zero matches. If matches remain, update each to `filterProjectId.value`. Non-router reads of `projectId.value` (code-comments counters, `handleNeedsTitle`'s `pid` capture, archive/pin command palette actions) must be **left alone** — those stay `projectId.value` because Task 3c will turn that ref into the data-driven computed.

- [ ] **Step 9: Check for Vite compile error**

```bash
uv run ./devctl.py logs front --lines 20
```

Expected: clean HMR update.

- [ ] **Step 10: Smoke test — tab switching**

Reload. Open a session. Click between tabs: **main → files → git → terminal → main**. Open a subagent tab, switch to it, close it. The URL should update correctly on each transition, and the sidebar should keep the current session highlighted. No behavior change should be visible compared to Task 3a (both refs still hold the same value).

- [ ] **Step 11: Smoke test — draft delete**

Create a new draft session (click "New session" on any project). Open the command palette (Cmd/Ctrl+K or equivalent), run the "Delete draft" command. You should be redirected to the project's root page — the same project the sidebar was filtered on. No behavior change expected here either.

- [ ] **Step 12: Commit**

```bash
git add frontend/src/views/SessionView.vue
git commit -m "$(cat <<'EOF'
refactor(front): route SessionView navigation via filterProjectId

Step 2/3: every router.push / router.replace inside SessionView that rebuilds
the URL for the current session now uses filterProjectId.value instead of
projectId.value. This makes tab navigation (main, subagent, files, git,
terminal) and the delete-draft action preserve the sidebar filter even when
projectId diverges from it. At this point projectId still equals the URL, so
there is no user-visible change.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 3c: Turn `projectId` into a computed from `session.project_id`

This is the step that actually decouples SessionView from the URL projectId.

- [ ] **Step 1: Move `projectId` below `session` and change it to a computed**

The `session` computed is currently at line 170:

```js
// Session data
const session = computed(() => store.getSession(sessionId.value))
```

Two changes:

1. At the block introduced in Task 3a (around lines 143-170), **delete** the line:
   ```js
   const projectId = ref(route.params.projectId)
   ```
   Keep `filterProjectId` and `sessionId`.

2. **Add** right after the `const session = ...` line (before `hasGitRepo` and any other reactive reader):
   ```js
   // Session's project (data-driven). Stable per KeepAlive instance because
   // sessionId is frozen and session.project_id is immutable for a given session.
   // Used for API calls, code-comments lookups, WS payloads, and template props
   // that identify the session's project (not the sidebar filter).
   const projectId = computed(() => session.value?.project_id)
   ```

**Ordering note:** The `projectId` declaration must appear before the first code that reads `projectId.value` in this `<script setup>`. The first reader today is `filesCommentsCount` (around line 182). Inserting the new computed immediately after `session` (line 170) keeps it above every reader. Do **not** leave the old `const projectId = ref(...)` anywhere else in the file (Task 3a placed one at the top; make sure you delete it as specified in instruction 1 above).

After this change, `projectId` is a Vue computed whose value is `session.value?.project_id`. Every existing reference to `projectId.value` that was left in the code after Task 3b (code-comments counters, `handleNeedsTitle`'s `pid = projectId.value`, the command palette archive/pin actions) automatically switches to the data-driven value. Template bindings like `:project-id="projectId"` (passed to `SessionItemsList` and `SessionContent`) auto-unwrap and forward the data-driven value — no template edit needed.

- [ ] **Step 2: Verify there is no other `ref(route.params.projectId)` left**

Search `frontend/src/views/SessionView.vue` for `ref(route.params.projectId)` — there must be exactly one match (the `filterProjectId` declaration). Remove any duplicate left over from Task 3a if present.

- [ ] **Step 3: Check for Vite compile error**

```bash
uv run ./devctl.py logs front --lines 20
```

Expected: clean HMR update.

- [ ] **Step 4: Smoke test — normal flow (unchanged UX)**

Reload. Click a session in the sidebar. The session view loads, tabs switch correctly, messages can be sent, files/git/terminal panels open. Everything should look identical to before the refactor.

- [ ] **Step 5: Smoke test — cross-filter URL (the decoupling test)**

This is the new capability the refactor unlocks. Construct a URL by hand:

1. Pick two projects, A and B, each with at least one session.
2. Note a session ID from project B (open it normally; copy the session ID from the URL).
3. In the browser address bar, navigate to `/project/<A-id>/session/<B-session-id>`.

Expected:

- The session view loads session B correctly. The header (`SessionHeader`) displays project B's name/color via the `ProjectBadge`.
- The sidebar stays on project A — the list shows project A's sessions (plus session B inserted at its mtime position, thanks to Task 2).
- Switching to tabs (files, git, terminal) keeps the URL at `/project/<A-id>/session/<B-session-id>/<tab>/...` — the projectId in the URL does not change to B.
- Code-comments counters in the tab headers show the right counts for session B (the ones you would see if you had opened it normally).

If any of these fail, do not commit. Re-check that (a) the router.push sites all use `filterProjectId.value` (Task 3b) and (b) every non-router use of `projectId.value` is the data-driven value now.

- [ ] **Step 6: Smoke test — archive / pin / rename**

From the cross-filter URL (session B displayed while sidebar is on A), open the command palette and run "Archive session". The session should archive in project B (check the sidebar after switching to project B — the session should be archived there). Then unarchive it via the command palette. Also try "Pin" / "Unpin" and verify the pin state round-trips by navigating to project B normally.

- [ ] **Step 7: Smoke test — send a message in a normal session**

Back to a normal (non-cross-filter) session, send a text message to Claude. Verify the message is sent (the Claude process starts, a reply streams back). This validates that the `send_message` WS payload still carries the correct `project_id` (now data-driven instead of URL-driven).

- [ ] **Step 8: Commit**

```bash
git add frontend/src/views/SessionView.vue
git commit -m "$(cat <<'EOF'
refactor(front): decouple SessionView projectId from the URL param

Step 3/3: projectId is now a computed reading session.value?.project_id.
Every remaining reference to projectId.value inside SessionView (API calls,
code-comments counters, WS payloads, archive/pin actions, auto-rename
captures) becomes data-driven. Template props :project-id="projectId" on
SessionItemsList and SessionContent auto-forward the data-driven value.

After this commit a URL like /project/A/session/sessionX where sessionX
belongs to project B renders correctly: sidebar stays on A, SessionView
loads sessionX against project B.

Spec: docs/superpowers/specs/2026-04-22-decouple-sessionview-from-url-projectid-design.md

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Final verification

- [ ] **End-to-end regression pass**

Run through the following sequence without reloading between steps (unless prompted):

1. Navigate to `/project/<A-id>` — sidebar lists project A's sessions.
2. Click one of A's sessions — the session view loads, tabs switch fine, a message can be sent.
3. Open a subagent tab (if the session has subagents) — it loads, switching to/from it works.
4. Navigate to `/projects?workspace=<W-id>` (All Projects filtered by a workspace you have). Click a session listed there — URL becomes `/projects/<sessionProjectId>/session/...`, sidebar stays on the workspace.
5. Type the cross-filter URL by hand (`/project/<A-id>/session/<B-session-id>` as in Task 3c Step 5) — session B loads with sidebar on A.
6. From the cross-filter state, reload the page. Everything should still render correctly (no hydration/router glitch).

- [ ] **Check the dev console for runtime errors**

Open browser dev tools → Console. Look for any red error specific to `SessionView`, `SessionList`, or the store. Ignore pre-existing warnings that are not caused by this change.

- [ ] **Final git state**

```bash
git log --oneline -6
```

Expected (most recent first, approximately):

```
refactor(front): decouple SessionView projectId from the URL param
refactor(front): route SessionView navigation via filterProjectId
refactor(front): introduce filterProjectId ref in SessionView
refactor(front): move selected-session fallback to SessionList.baseSessions
refactor(front): export sessionSortComparator for SessionList reuse
docs: address spec review feedback for SessionView projectId decoupling
```

Five code commits plus the existing doc commits. If any commit was squashed or the order differs, that is fine as long as the code ends up in the same state.

---

## Out of scope (do not attempt in this plan)

- Introducing a pin enum (scope / workspace / always) — covered by a follow-up spec.
- Modifying `useReconciliation` or `useWebSocket` to prioritize the session's project when it differs from the filter — deferred to the pin follow-up.
- Adding a visual indicator in the sidebar for cross-filter sessions — deferred.
- Backend changes — none are required or expected.
