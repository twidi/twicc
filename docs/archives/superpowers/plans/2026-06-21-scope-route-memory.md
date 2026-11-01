# Per-Scope Last-Location Memory Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When re-entering a scope (session / project / worktree / workspace / All Projects) by navigating to its bare home route, restore the last location the user was at inside that scope instead of resetting to the entry screen.

**Architecture:** One new vue-router-layer module (`frontend/src/utils/scopeMemory.js`) holding pure route helpers + an in-memory `Map` + a `registerScopeMemory(router)` that installs a `beforeEach` (active restore) and an `afterEach` (passive recording). No view changes — the global guard upgrades every existing/future control that pushes a bare scope-home route. Spec: [docs/superpowers/specs/2026-06-21-scope-route-memory-design.md](../specs/2026-06-21-scope-route-memory-design.md).

**Tech Stack:** Vue 3, Vue Router 4 (`createWebHistory`), plain ESM JS. Reuses route-name/param builders from `frontend/src/utils/granularRoutes.js`.

---

## Conventions for this plan

- **No JS test runner exists** in this repo (no vitest/jest; the project's CLAUDE.md states tests are not mandatory). We do **not** introduce one. Pure functions are verified with a throwaway `node` ESM sanity script (the module is Vue-free and node-importable); the guard wiring is verified manually in the running app against the spec's scenarios.
- **Git:** commits target the **current branch** (the user controls isolation; do not create a branch/worktree on your own). Stage exact paths — never `git add -A`. Commit style: `feat(routing): …`, matching repo history.
- **Reuse, don't duplicate:** route names live in `router.js`; the module mirrors them in explicit `Set`s (small, testable). Route-name/param construction reuses `granularRoutes.js` helpers.

## File Structure

- **Create** `frontend/src/utils/scopeMemory.js` — the entire feature: pure helpers (`scopeKey`, `isScopeHome`, `tabOf`, `scopeRelative`, `isBase`, `rebuild`), the in-memory `Map`, and `registerScopeMemory(router)` (the two guards + Back/Forward detection). One cohesive, focused file (~120 lines).
- **Modify** `frontend/src/router.js` — import and call `registerScopeMemory(router)` **after** the existing `beforeEach`/`afterEach` guards (end of file). ~2 lines.

No other files change. That "zero per-button wiring" is the design's whole point.

---

### Task 1: Spike — confirm Back/Forward detection timing

The entire history-skip filter rests on one timing assumption: on Back/Forward the browser has **already** swapped `history.state` before `beforeEach` runs (so `history.state.position` differs from the last-seen value), but on a fresh programmatic push it has **not yet** (position still equals the last-seen value). Confirm this empirically **before** building on it. If it does not hold, switch the module to the `popstate`-flag fallback (see spec "Detecting Back/Forward").

**Files:**
- Temporarily modify: `frontend/src/router.js` (probe; reverted at the end of this task)

- [ ] **Step 1: Add a temporary probe guard**

At the end of `frontend/src/router.js` (after the existing guards), add:

```js
// TEMP PROBE — remove after Task 1
let _probePos = window.history.state?.position ?? 0
router.beforeEach((to) => {
    console.log('[probe] beforeEach', { name: to.name, statePos: window.history.state?.position, tracked: _probePos })
})
router.afterEach((to) => {
    _probePos = window.history.state?.position ?? _probePos
    console.log('[probe] afterEach', { name: to.name, newTracked: _probePos })
})
```

- [ ] **Step 2: Exercise it in the app**

Ensure the dev servers are running (ask the user to start/restart via `devctl.py` if needed — do not start them yourself). In the browser devtools console, observe the `[probe]` logs while:
1. clicking a sidebar session (fresh **push**),
2. clicking the browser **Back** button,
3. clicking the browser **Forward** button.

Expected:
- **push:** `beforeEach.statePos === tracked` (equal) → would be classified "fresh".
- **Back:** `beforeEach.statePos !== tracked` (lower) → "history".
- **Forward:** `beforeEach.statePos !== tracked` (higher) → "history".

- [ ] **Step 3: Decide and record**

If the expectations hold → keep the `history.state.position` approach in the module (Task 3). If not → note it and use the `popstate`-flag fallback in Task 3 instead. Write the decision in one line in the plan's task or the commit message of Task 3.

- [ ] **Step 4: Revert the probe**

Remove the TEMP PROBE block from `frontend/src/router.js`. Confirm `git diff frontend/src/router.js` is empty.

No commit for this task (probe is reverted).

---

### Task 2: scope-memory module — pure helpers

**Files:**
- Create: `frontend/src/utils/scopeMemory.js`
- Sanity (throwaway): a `node` ESM script under the scratch dir

- [ ] **Step 1: Create the module with pure helpers + the memory map**

Create `frontend/src/utils/scopeMemory.js`:

```js
// frontend/src/utils/scopeMemory.js
//
// Per-scope last-location memory. Each "scope" — a session, a project/worktree page,
// a workspace page, or the All Projects page — remembers the last location the user
// was at inside it. When the user RE-ENTERS a scope by navigating to its bare
// home/entry route, the guard redirects to that remembered location instead, so a
// scope reopens where it was left (like switching back to a window).
//
// Strictly secondary to the URL (spec: 2026-06-21-scope-route-memory-design.md): the
// memory is read at exactly one moment — a fresh (non Back/Forward) navigation that
// ENTERS a scope home from a DIFFERENT scope. It never overrides a displayed route,
// never reconciles, never background-syncs. Volatile: lost on a full page reload.
//
// No per-button wiring: a single global guard upgrades every control that pushes a
// bare scope-home route (sidebar rows, the project button, the workspace / All
// Projects selectors, the deselect toggle).

import { buildTabRouteName, buildSubagentRouteName, pickDefined } from './granularRoutes'

// --- Route-name families (authoritative; mirrors router.js) ---

const SESSION_NAMES = new Set([
    'session', 'session-files', 'session-git', 'session-terminal',
    'session-artifacts', 'session-orchestration', 'session-subagent',
    'projects-session', 'projects-session-files', 'projects-session-git',
    'projects-session-terminal', 'projects-session-artifacts',
    'projects-session-orchestration', 'projects-session-subagent',
])
const PROJECT_NAMES = new Set([
    'project', 'project-files', 'project-git', 'project-terminal', 'project-artifacts',
])
const ALL_PROJECTS_NAMES = new Set([
    'projects-all', 'projects-files', 'projects-git', 'projects-terminal', 'projects-artifacts',
])
// Bare entry routes (homes) per scope family.
const HOME_NAMES = new Set(['session', 'projects-session', 'project', 'projects-all'])

// --- Pure helpers ---

// Stable per-scope key, or null for non-scope routes (home '/', login, …).
export function scopeKey(route) {
    const name = route?.name
    if (!name) return null
    if (SESSION_NAMES.has(name)) return `session:${route.params.sessionId}`
    if (PROJECT_NAMES.has(name)) return `project:${route.params.projectId}`
    if (ALL_PROJECTS_NAMES.has(name)) {
        const ws = route.query?.workspace
        return ws ? `workspace:${ws}` : 'all-projects'
    }
    return null
}

// True only for a scope's bare entry route (Chat / project stats / All Projects list).
export function isScopeHome(route) {
    return HOME_NAMES.has(route?.name)
}

// The sub-screen id within a scope: '' for the base, else the tool tab / 'subagent'.
export function tabOf(name) {
    if (HOME_NAMES.has(name)) return ''
    return name.split('-').pop()
}

// The framing-independent part of a route: which tab + its granular params, dropping
// the context params (projectId/sessionId) that are re-supplied on rebuild.
export function scopeRelative(route) {
    const { projectId, sessionId, ...rest } = route.params || {}
    return { tab: tabOf(route.name), params: rest }
}

// "Nothing better than the home is remembered" — absent, or the base screen itself.
export function isBase(saved) {
    return !saved || saved.tab === ''
}

// Rebuild a concrete route location from a stored {tab, params}, in the framing of the
// navigation being intercepted (`to`): its route-name family, its projectId, and its
// live query (e.g. the current ?workspace=). The stored params are already URL-encoded
// (captured verbatim from route.params), so no re-encoding is needed.
export function rebuild(saved, to) {
    const isAllProjectsMode = to.name.startsWith('projects')
    const isSessionRoute = SESSION_NAMES.has(to.name)
    const name = saved.tab === 'subagent'
        ? buildSubagentRouteName(isAllProjectsMode)
        : buildTabRouteName({ isAllProjectsMode, isSessionRoute, tab: saved.tab })
    const params = {
        ...saved.params,
        ...pickDefined({ projectId: to.params.projectId, sessionId: to.params.sessionId }),
    }
    return { name, params, query: to.query }
}

// --- The memory + guard installer ---

const memory = new Map() // scopeKey -> { tab, params }

export function _resetForTest() { memory.clear() } // test/sanity only

export function registerScopeMemory(router) {
    // (filled in Task 3)
}
```

- [ ] **Step 2: Write the throwaway sanity script**

Create a temp file (scratch dir, not the repo), e.g. `scope-sanity.mjs`:

```js
import {
    scopeKey, isScopeHome, tabOf, scopeRelative, isBase, rebuild,
} from '/home/twidi/dev/twicc-poc/frontend/src/utils/scopeMemory.js'

const eq = (a, b, msg) => {
    const A = JSON.stringify(a), B = JSON.stringify(b)
    if (A !== B) { console.error('FAIL', msg, '\n  got ', A, '\n  want', B); process.exitCode = 1 }
    else console.log('ok  ', msg)
}

// scopeKey
eq(scopeKey({ name: 'session-files', params: { sessionId: 's1' } }), 'session:s1', 'scopeKey session')
eq(scopeKey({ name: 'projects-session', params: { sessionId: 's1' } }), 'session:s1', 'scopeKey session (all-projects framing) same key')
eq(scopeKey({ name: 'project-git', params: { projectId: 'p1' } }), 'project:p1', 'scopeKey project')
eq(scopeKey({ name: 'projects-all', params: {}, query: {} }), 'all-projects', 'scopeKey all-projects')
eq(scopeKey({ name: 'projects-files', params: {}, query: { workspace: 'w1' } }), 'workspace:w1', 'scopeKey workspace')
eq(scopeKey({ name: 'home', params: {} }), null, 'scopeKey non-scope → null')

// isScopeHome
eq(isScopeHome({ name: 'session' }), true, 'isScopeHome session base')
eq(isScopeHome({ name: 'session-files' }), false, 'isScopeHome tool tab false')
eq(isScopeHome({ name: 'projects-all' }), true, 'isScopeHome all-projects base')

// tabOf
eq(tabOf('session'), '', 'tabOf base')
eq(tabOf('projects-session-files'), 'files', 'tabOf nested files')
eq(tabOf('session-orchestration'), 'orchestration', 'tabOf orchestration')
eq(tabOf('projects-session-subagent'), 'subagent', 'tabOf subagent')

// scopeRelative drops projectId/sessionId
eq(scopeRelative({ name: 'projects-session-git', params: { projectId: 'p1', sessionId: 's1', rootKey: 'session', commitRef: 'index' } }),
   { tab: 'git', params: { rootKey: 'session', commitRef: 'index' } }, 'scopeRelative drops context params')

// isBase
eq(isBase(undefined), true, 'isBase absent')
eq(isBase({ tab: '', params: {} }), true, 'isBase base tab')
eq(isBase({ tab: 'files', params: {} }), false, 'isBase tool tab')

// rebuild reframes session single ↔ all-projects, carries live query
const saved = { tab: 'files', params: { rootKey: 'git-root', filePath: 'src|App.vue' } }
eq(rebuild(saved, { name: 'projects-session', params: { projectId: 'p1', sessionId: 's1' }, query: { workspace: 'w1' } }),
   { name: 'projects-session-files', params: { rootKey: 'git-root', filePath: 'src|App.vue', projectId: 'p1', sessionId: 's1' }, query: { workspace: 'w1' } },
   'rebuild all-projects framing + workspace query')
eq(rebuild(saved, { name: 'session', params: { projectId: 'p1', sessionId: 's1' }, query: {} }),
   { name: 'session-files', params: { rootKey: 'git-root', filePath: 'src|App.vue', projectId: 'p1', sessionId: 's1' }, query: {} },
   'rebuild single-project framing (reframed from same saved)')
// rebuild subagent
eq(rebuild({ tab: 'subagent', params: { subagentId: 'a1' } }, { name: 'session', params: { projectId: 'p1', sessionId: 's1' }, query: {} }),
   { name: 'session-subagent', params: { subagentId: 'a1', projectId: 'p1', sessionId: 's1' }, query: {} }, 'rebuild subagent')
// rebuild all-projects scope (no projectId/sessionId)
eq(rebuild({ tab: 'git', params: { rootKey: 'home' } }, { name: 'projects-all', params: {}, query: { workspace: 'w1' } }),
   { name: 'projects-git', params: { rootKey: 'home' }, query: { workspace: 'w1' } }, 'rebuild all-projects scope')

console.log(process.exitCode ? 'SANITY FAILED' : 'SANITY OK')
```

- [ ] **Step 3: Run the sanity script**

Run: `node /path/to/scratch/scope-sanity.mjs`
Expected: every line `ok  …` and final `SANITY OK` (exit 0).

- [ ] **Step 4: Delete the sanity script**

Remove the throwaway file (it lives in scratch, not the repo).

- [ ] **Step 5: Commit**

```bash
git add frontend/src/utils/scopeMemory.js
git commit -m "feat(routing): scope-memory pure helpers (scopeKey/rebuild/…)"
```

---

### Task 3: guard installer + Back/Forward detection

**Files:**
- Modify: `frontend/src/utils/scopeMemory.js` (fill `registerScopeMemory`)

- [ ] **Step 1: Implement `registerScopeMemory`**

Replace the stub body with (this uses the `history.state.position` approach confirmed in Task 1; if Task 1 invalidated it, implement the `popstate`-flag fallback instead and say so in the commit):

```js
export function registerScopeMemory(router) {
    // Back/Forward detection via Vue Router's monotonic history.state.position:
    // on a pop the browser swaps history.state to the destination BEFORE beforeEach
    // runs (position differs from our tracked value); on a fresh push it has not yet.
    let currentPosition = window.history.state?.position ?? 0
    const isHistoryNav = () => {
        const pos = window.history.state?.position
        return pos != null && pos !== currentPosition
    }

    // Active restore: rewrite an enter-scope navigation to the scope's last location.
    router.beforeEach((to, from) => {
        if (isHistoryNav()) return true                       // Back/Forward: never touch
        if (!isScopeHome(to)) return true                     // only intercept entry routes
        const toKey = scopeKey(to)
        if (!toKey || toKey === scopeKey(from)) return true   // intra-scope move: leave alone
        const saved = memory.get(toKey)
        if (isBase(saved)) return true                        // nothing better than the home
        return rebuild(saved, to)
    })

    // Passive recording: remember where the user is in each scope. Skip aborted /
    // redirected intermediates so we only record settled screens.
    router.afterEach((to, from, failure) => {
        if (failure) return
        currentPosition = window.history.state?.position ?? currentPosition
        const key = scopeKey(to)
        if (key) memory.set(key, scopeRelative(to))
    })
}
```

- [ ] **Step 2: Lint-check the file compiles**

Run: `cd frontend && node --check src/utils/scopeMemory.js`
Expected: no output (syntax OK). (Full behavior is verified in Task 5.)

- [ ] **Step 3: Commit**

```bash
git add frontend/src/utils/scopeMemory.js
git commit -m "feat(routing): scope-memory guard + Back/Forward detection"
```

---

### Task 4: wire into router.js

`registerScopeMemory` must run **after** the existing auth + workspace-propagation guards, so our restore guard sees the workspace-finalized `to` and our recording runs on settled routes. Register it at the very end of `router.js`.

**Files:**
- Modify: `frontend/src/router.js`

- [ ] **Step 1: Import the installer**

At the top of `frontend/src/router.js`, with the other imports:

```js
import { registerScopeMemory } from './utils/scopeMemory'
```

- [ ] **Step 2: Call it after the existing guards**

At the **end** of `frontend/src/router.js` (after the workspace-cleanup `afterEach`):

```js
// Per-scope last-location memory — restore a scope's last screen when re-entering it.
// Registered last so it sees the workspace-finalized `to` and records settled routes.
registerScopeMemory(router)
```

- [ ] **Step 3: Verify it loads**

Run: `cd frontend && node --check src/router.js`
Expected: no output (syntax OK).

- [ ] **Step 4: Commit**

```bash
git add frontend/src/router.js
git commit -m "feat(routing): register scope-memory guard in the router"
```

---

### Task 5: manual integration verification

No automated harness covers the guard end-to-end, so verify against the spec's scenarios in the running app. Ask the user to start/restart the dev servers via `devctl.py` if they aren't running (do not start them yourself). Walk each scenario; if any fails, debug with `superpowers:systematic-debugging` and fix the root cause before continuing.

- [ ] **Step 1: Sidebar A→B→A restores the tab/file**
  1. Open session A, go to its Files tab, open a specific file.
  2. Click session B in the sidebar.
  3. Click session A in the sidebar.
  Expected: A reopens on Files showing that file (not Chat).

- [ ] **Step 2: Back/Forward stays inert**
  1. From session A's Chat, navigate to A's Files (same scope), then to session B.
  2. Press browser Back twice.
  Expected: lands on A's Chat (the exact A1 history entry), **not** bounced to A's Files.
  3. Press Forward.
  Expected: replays forward through the exact entries.

- [ ] **Step 3: Chat tab stays reachable (intra-scope)**
  While in session A on Files, click the Chat tab.
  Expected: shows Chat (not bounced back to Files). Leaving and re-entering A now reopens on Chat.

- [ ] **Step 4: Header "go to project" restores the project's last screen**
  1. In a project, open its Git tab on a specific commit.
  2. Open a session in that project.
  3. Click the header button that goes to the project.
  Expected: the project reopens on Git at that commit.

- [ ] **Step 5: Deselect toggle → project's last screen**
  Click the currently-active session row in the sidebar.
  Expected: returns to the project scope at its last location (consistent with Step 4).

- [ ] **Step 6: Workspace / All Projects**
  Navigate into a tool tab under a workspace, leave to a session, then re-enter that workspace (its selector/button).
  Expected: the workspace view reopens on that tool tab; switching to a different workspace or to All Projects restores their own last screens independently.

- [ ] **Step 7: Self-heal on a now-absent tab**
  Remember a Git tab on a session, then make git unavailable for it (or pick a session that lost its repo) and re-enter.
  Expected: graceful fallback to Chat (the existing absent-tab `replace`), no redirect loop, no console errors. (The absent-tab `replace` keeps the same `sessionId`/`projectId`, so `scopeKey(to) === scopeKey(from)` holds and the restore guard skips it — confirm there is no second redirect.)

- [ ] **Step 8: Reload resets memory (by design)**
  Be deep in a scope, reload the page, then re-enter a different scope and come back.
  Expected: after reload the current URL renders unaffected; cross-scope memory starts fresh (volatile).

- [ ] **Step 9 (if any fix was needed): Commit**

```bash
git add frontend/src/utils/scopeMemory.js frontend/src/router.js
git commit -m "fix(routing): <specific issue found during verification>"
```

---

## Done when

All of Task 5's scenarios pass, `git diff` shows only `frontend/src/utils/scopeMemory.js` (new) and `frontend/src/router.js` (2-line wiring), and no view/component files were touched.
