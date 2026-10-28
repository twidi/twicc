# Sidebar Recompute Cost Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** stop the sidebar derivations from scanning the whole session store on every live session update, with no visible behavior change outside the two exceptions the spec accepts.

**Architecture:** node-loadable modules hold every new derivation: a root-session index, one stable list per scope, two per-session unread memos, activity-free project inputs with a retaining workspace-membership factory, and the sidebar block stages with their reactive wiring factory. The Pinia stores and `SessionList.vue` only plug them in. A frozen copy of the baseline derivations, kept under `frontend/tests/helpers/`, is the parity oracle of the tests and the "before" of the benchmark.

**Tech Stack:** Vue 3.5 reactivity (`computed`, `toRaw`, `reactive`, `effect`), Pinia, `node --test`, `@vue/compiler-sfc` (existing SFC harness).

**Spec:** `docs/plans/2026-10-28-sidebar-recompute-cost-design.md` (commit `9951a0ef2`). Section numbers below (§N) refer to it. Read it before any task.

## Global Constraints

- Frontend only. No backend change. No new dependency; never run `npm install` / `npm ci`.
- Baseline: `main` at `5cf064fb4`. The frozen baseline copies (Task 1) come from that commit with `git show 5cf064fb4:<path>`.
- Every new or changed module listed in §6 loads under plain `node --test`: no Pinia import, explicit `.js` extensions on relative imports. Stateful logic (caches, retention) lives in those modules, never only in a store.
- No circular static import between stores, utils and composables; use a lazy `import()` if one appears.
- Vue 3.5 rule the design relies on: a computed with subscribers reruns only when a dependency it read changes, and stops propagation when its new value is identical (`Object.is`). **Every stage returns its previous value (array, `Set`, object) when its content is equal.** Every stage reads its inputs through per-field computeds, so a stage reruns only when a field it uses changes.
- Keep every behavior assertion of the existing frontend tests. A test may change its fixture or harness to supply the new inputs, never its expected behavior.
- Tests run with `cd /home/twidi/dev/twicc-poc/frontend && node --test <file>`; the whole suite with `cd /home/twidi/dev/twicc-poc/frontend && npm test`.
- Workspace scope ids use the real prefix `ws:` (`utils/workspaceIds.js`).
- **Test rules for this plan:**
  - **Scopes.** Every test of Tasks 3 and 6 runs in scope `p0` and in scope `ws:w`, unless it names its scopes; it also runs in `p2` when the main-repository path differs (scope resolution, cursor). Every test of Task 4 runs on `summary(['p0'])` and on `summary(<visible ids of w>)`, except the parity test, which compares `summary([pid])` for every fixture project and `totalUnread()` after each step.
  - **Options.** Block tests use `showActiveAcrossFilters: true` and `showArchivedProjects: false`, unless they name other values (with `false`, `crossFilterActive` and its memo never run).
  - **Ticks.** A "tick" of a session is a real batch: its `mtime`, `last_line` and `last_new_content_at` advance; for the open session `last_viewed_at` advances too (§5.5). A tick flips the unread state of a session that is not already unread, except an open tick (its `last_viewed_at` follows), so every test that ticks a session states which: it ticks an already-unread session when the test needs no flip, and a read session when it needs one.
  - **Own setup.** The fixture's roots are read and have no process, except the ones the fixture paragraph (Task 1) names. A test that needs another state (an unread in-scope session, a process, unread content on a draft or ephemeral root) sets it up itself, before it installs its observer or counters (or before mount for SFC tests), and its comment says so.
  - **Positive control also for renders.** A test that asserts 0 row renders first asserts, per scope, at least one counted render at mount, then applies a change to one visible row's session and asserts that this row re-renders once.
  - **Positive control.** A test that asserts 0 runs of a stage also applies, on the same fixture, a change that must run that stage, and asserts at least 1 run (for example a root add for `'rootSessions'` and `'unread:counts'`, a reorder inside "Pinned elsewhere" for `'blocks:pinnedOrder'`, a member archive for `'workspace:visible'`). A missing or misplaced `noteStageRun` call then fails the test instead of passing it.
  - **Probes that can fail.** A counting getter that proves a field is not read goes on the field the baseline code reads first in that path, so that a regression to the baseline would be caught:
    - scope lists in a single-project scope: `project_id` of out-of-scope roots and of their subagents (the baseline filter reads `project_id` first and stops there); `parent_session_id` and `hidden` of the subagents of in-scope roots;
    - scope lists in a multi-project or all-projects scope, `crossFilterActive`: `parent_session_id`;
    - `crossFilterPinned`: `pinned`;
    - unread index (`isSessionUnread`): `hidden`;
    - never a timestamp of a subagent, never the `mtime` of an out-of-scope root in a single-project scope (the baseline does not reach them).
- All code, comments and test names in English. Commits: Conventional Commit subject, lowercase summary, a body, and the trailer `Co-Authored-By: Claude MODEL <noreply@anthropic.com>` (or the Codex form) with the exact running model name. Stage files by explicit path, never a directory.

## Review Focus

1. **A workspace whose visible project set changes** (a member archived or unarchived with "show archived projects" off, a worktree added): the sidebar list must switch to the new scope list at once. Test in Task 6.
2. **A session older than the pagination cutoff that gets new activity**: it must appear in the scope list on its first `mtime` tick past the cutoff, and leave when the cursor moves past it. Test in Task 3.
3. **A session removed from the store while its memos exist** (`removeSession`, `unloadProject`): unread counts and "Active elsewhere" must drop it at once. Test in Task 4 (counts) and Task 6 (block).
4. **The selected session is a subagent, or is outside every block** (deep link): `extra` keeps today's rule (a subagent is never `extra`). Test in Task 6.
5. **Drafts and launched ephemeral sessions in the roots**: they keep their place (ephemeral first) and never enter "Active elsewhere". Test in Task 6.

---

## File Structure

| File | Status | Responsibility |
|---|---|---|
| `frontend/src/constants.js` | modify | adds `ALL_PROJECTS_ID` (moved from `stores/data.js`, which re-exports it) |
| `frontend/src/utils/startupPhase.js` | create | `hasActiveStartupPhase(startupProgress)` (moved from `stores/data.js`) |
| `frontend/src/utils/stageRuns.js` | create | leaf module: test-observable stage runs (`noteStageRun`, `observeStageRuns`) |
| `frontend/src/utils/retain.js` | create | leaf module: `retainSessionArray` (moved from `sessionLists.js`, which re-exports it), `retainIds`, `retainIdSet` |
| `frontend/src/utils/rootSessions.js` | create | root-session index (§5.1) |
| `frontend/src/utils/sessionLists.js` | modify | scope resolution and per-scope stable lists (§5.2, §5.3) |
| `frontend/src/utils/unreadMemos.js` | create | the two per-session unread memos (§5.5) |
| `frontend/src/utils/projectProcessActivity.js` | modify | `unreadCounts` over the roots and the `unread` memo (§5.5) |
| `frontend/src/utils/projectScopes.js` | create | archived-project set, activity-free worktree index, retaining workspace membership (§5.6) |
| `frontend/src/utils/sessionTree.js` | modify | `extendTreeCandidates` new options (§5.4) |
| `frontend/src/utils/sidebarSessions.js` | modify | natural membership, cross-filter membership, order stages, one-shot wrapper, wiring factory (§5.4) |
| `frontend/src/stores/data.js` | modify | plugs the new modules in as getters |
| `frontend/src/stores/workspaces.js` | modify | membership getters delegate to `projectScopes.js` |
| `frontend/src/components/session/list/SessionList.vue` | modify | uses the factory; `canonicalSessions` and `scopeProjectIds` changes (§5.2, §5.6) |
| `frontend/src/components/peer/PeerMessageReviewDialog.vue` | modify | its archived set uses `archivedProjectIds` |
| `frontend/tests/helpers/sidebarBaseline/` | create | frozen baseline copies (parity oracle, benchmark "before") |
| `frontend/tests/helpers/sidebarFixture.js` | create | store-state builder and the two data facades (baseline, shipped) shared by tests and benchmark |
| `frontend/scripts/benchmark_sidebar_recompute.mjs` | create | the benchmark of §7.2 |

## Shared Interfaces (decided here, used across tasks)

```js
// utils/startupPhase.js
export function hasActiveStartupPhase(startupProgress) // -> boolean; reads every phase entry (tracked when called in a computed); a missing startupProgress means "not in startup"

// utils/stageRuns.js (leaf module). Every stage of this plan calls noteStageRun(name) at the start of each run.
export function noteStageRun(name)          // no-op unless an observer is registered
export function observeStageRuns(callback)  // -> stop(); callback(name) on each run; tests only
// Stage names (fixed): 'rootSessions', 'scope:candidates', 'scope:members', 'scope:ordered',
// 'memo:unread', 'memo:unreadContent', 'unread:counts', 'blocks:natural', 'blocks:extension',
// 'blocks:crossFilter', 'blocks:naturalOrder', 'blocks:pinnedOrder', 'blocks:activeOrder',
// 'workspace:visible', 'workspace:all'. Tests count runs per name; a retained output is not proof that a stage did not run.

// utils/retain.js (leaf module: imports nothing from utils, so no cycle)
export function retainSessionArray(previous, next) // unchanged behavior; previous when same records in same order
export function retainIds(previous, next)          // previous when same ids in same order (either may be null), else next
export function retainIdSet(previous, next)        // previous when same size and members, else next

// utils/rootSessions.js
export function getRootSessions(owner) // -> Session[] (reactive records); owner = any object with .sessions and optional .startupProgress

// utils/sessionLists.js
export function resolveSessionScope({ effectiveProjectId, getProjectScopeIds, getVisibleProjectIds })
//   -> { key: string, projectIds: string[] | null, cursorId: string }
export function getScopeSessionList(state, scope) // -> { readonly members: Session[], readonly ordered: Session[] } (stable object per scope.key)
export function getStableSessionList(state, scopeId, allProjectsId) // kept: ordered list of the single-project / all-projects scope
export { retainSessionArray } from './retain.js'  // re-export kept for existing imports

// utils/unreadMemos.js
export function getUnreadMemos(owner) // -> { unread(session): boolean, unreadContent(session): boolean }

// utils/projectScopes.js
export function getArchivedProjectIds(owner)     // -> Set<string>, retained
export function getWorktreeIdsByParent(owner)    // -> Map<parentId, string[]> (ids sorted), retained
export function createWorkspaceMembership({ getWorkspaceById, getProject, getWorktreeIdsByParent, isShowArchived })
//   every parameter is a function, called on each evaluation (so the caller's computed tracks what it reads)
//   -> { visible(workspaceId): string[], all(workspaceId): string[] }  (each retained per workspace id)

// utils/sidebarSessions.js
export function computeSidebarNaturalMembership(options, previous)
//   -> { list, natural: Session[], naturalIds: Set<string>, extension: Session[] }
export function computeSidebarCrossFilterMembership(options, naturalIds, previous)
//   -> { crossFilterPinned: Session[], crossFilterActive: Session[] }   (unsorted, root order)
export function orderSidebarNatural(options, list, naturalIds, extension, previous) // -> Session[]
export function orderSidebarBlock(sessions, processStates, previous)               // -> Session[]
export function computeSidebarExtra(options, naturalIds, crossFilterPinned, crossFilterActive) // -> Session | null
export function computeSidebarSessionBlocks(options, previous) // one-shot wrapper -> { extra, crossFilterPinned, crossFilterActive, natural }
export function useSidebarSessionBlocks(getOptions)
//   -> { blocks: ComputedRef<Blocks>, stages: { natural, crossFilter, naturalOrder, pinnedOrder, activeOrder } }
```

`options` keeps today's `computeSidebarSessionBlocks` shape (`data`, `workspaces`, `effectiveProjectId`, `activeWorkspaceId`, `sessionId`, `showArchived`, `showArchivedProjects`, `showActiveAcrossFilters`, `showAsTree`). The `data` object must provide, besides today's fields: `rootSessions`, `getScopeSessionList(scope)`, `archivedProjectIds`, `hasUnreadContentMemo(session)`, `inStartupPhase()`. Each data-store getter that wraps a module function returns the module's value unchanged.

Scope key rule (§5.2): `key = cursorId + '|' + (projectIds ? [...projectIds].sort().join(',') : '*')`. Cursor: the project's own id for a single project; `ALL_PROJECTS_ID` for any workspace (whatever its member count), for a main repository whose `getProjectScopeIds` holds more than one id, for all projects, and for a falsy `effectiveProjectId`.

---

### Task 1: Frozen baseline, shared fixture, node-loadable `sidebarSessions.js`

**Files:**
- Create: `frontend/tests/helpers/sidebarBaseline/sessionLists.js`, `sidebarSessions.js`, `sessionTree.js`, `projectProcessActivity.js`, `projectInputs.js`, `README.md`
- Create: `frontend/tests/helpers/sidebarFixture.js`
- Create: `frontend/src/utils/startupPhase.js`, `frontend/src/utils/retain.js`, `frontend/src/utils/retain.test.js`, `frontend/src/utils/stageRuns.js`, `frontend/src/utils/stageRuns.test.js`
- Modify: `frontend/src/constants.js`, `frontend/src/stores/data.js` (`ALL_PROJECTS_ID` and `hasActiveStartupPhase` moved, `ALL_PROJECTS_ID` re-exported), `frontend/src/utils/sessionLists.js` (`retainSessionArray` moved to `retain.js`, re-exported), `frontend/src/utils/sidebarSessions.js` (imports only)
- Modify: `frontend/src/utils/sidebarSessionsStability.test.js` (direct import; workspace scope `ws:w`)
- Test: `frontend/tests/helpers/sidebarFixture.test.js`

**Interfaces:**
- Produces: `hasActiveStartupPhase`; `ALL_PROJECTS_ID` from `constants.js`; `utils/retain.js` (all three functions, with unit tests in `src/utils/retain.test.js`); `utils/stageRuns.js` (with a unit test: an observer sees each `noteStageRun` call until `stop()`, and no observer means no work).
- Produces (`tests/helpers/sidebarFixture.js`):
  - `buildSidebarState({ roots, subagentsPerRoot = 2, projects = 10, cutoffRoots = null })` → a `reactive` state `{ sessions, projects, processStates, startupProgress, settings: { isShowArchivedProjects: false }, localState: { projects } }`, deterministic. Both facades read "show archived projects" from `state.settings.isShowArchivedProjects` (the frozen `getVisibleProjectIds` and the shipped `createWorkspaceMembership({ isShowArchived })` alike).
  - `FIXTURE_WORKSPACES` (the workspaces of the fixture) and `FIXTURE_SCOPES`: the scope ids of §7.1 #9 — `p0` (single project), `p2` (main repository with worktrees), `ws:w` (workspace `{p0, p1}`), `ws:w1` (one-project workspace `{p0}`), `__all__`, and `null`.
  - `startupPhase(state, active)` sets `state.startupProgress` to a real phase shape (`{ background_compute: { claude: { current: 1, total: 2, completed: !active } } }`); `progressMessage(state)` replaces the object the way `setStartupProgress` does.
  - `tick(state, sessionId, { open = false, top = false } = {})`: one real batch (see "Ticks" in Global Constraints); `top` gives the session an `mtime` above every other session.
  - `FIXTURE_UPDATE_SEQUENCE`: the shared update sequence, a list of named steps applied with `applyStep(state, step)`. Steps, in order: in-scope ticks with and without an order change; out-of-scope ticks with and without an order change; an unread flip in each direction, one of them on a `user_turn` session that has background work; a pin change; an archive; a hide; a cutoff crossing; a root add and a root removal; a subagent add and removal; a workspace member archived; a worktree added to `p2` and to `p0`; a process start and stop; a startup phase with one untracked write, then a progress message, then the end of startup. The §7.1 #9 parity tests (Tasks 4 and 6) and the stage-name coverage test (Task 6) use this sequence.
  - `createBaselineData(state)` → `{ data, workspaces }` facades over the frozen copies (activity-sorted `getProjects`, `getWorktreesOf`, `getVisibleProjectIds`).
  - `createShippedData(state)` → the same facades over the shipped modules; Tasks 2–6 fill it in as the modules appear.
  - In both facades, every field that is a Pinia getter in the real store (`projectActivityById`, `getProjects`, `getAllSessions`, `getProjectSessions`, and the new getters) is a `computed`, read through a property getter, so it caches like Pinia does and the "before" figures cost what they cost today.
- Produces (`tests/helpers/sidebarBaseline/`): `getStableSessionList`, `computeSidebarSessionBlocks`, `extendTreeCandidates`, `createBaselineActivityIndex(store)` (the baseline `getProjectActivityIndex`), `baselineProjectInputs(state, workspaces, isShowArchived = () => state.settings.isShowArchivedProjects)` → `{ getProjects, getWorktreesOf, getVisibleProjectIds, getAllProjectIds }` (the frozen `getVisibleProjectIds` reads `isShowArchived()` where the store read the settings store).

- [ ] **Step 1: Create the frozen copies.** `git show 5cf064fb4:frontend/src/utils/<file>` for `sessionLists.js`, `sidebarSessions.js`, `sessionTree.js`, `projectProcessActivity.js`; rewrite only their imports so they load under node and point to each other (`sessionSort.js`, `sessions.js`, `backgroundWork.js`, `processActivity.js`, `workspaceIds.js` come from `src/utils/`, which the design does not change). The frozen files define their own `ALL_PROJECTS_ID` and their own copy of the baseline `hasActiveStartupPhase` (from `stores/data.js` at `5cf064fb4`); they import nothing that this plan creates or moves. `projectInputs.js` holds verbatim copies of the baseline logic of `getProjects`, `projectActivityById`, `projectActivityComparator`, `getWorktreesOf` (`stores/data.js`) and `getVisibleProjectIds`, `getAllProjectIds` (`stores/workspaces.js`) as functions over a state and a workspace list. `README.md`: one paragraph saying these files are frozen at `5cf064fb4` and must never be edited.

- [ ] **Step 2: Write the fixture sanity test** `tests/helpers/sidebarFixture.test.js`:

```js
test('baseline facade gives non-empty root-only blocks in every fixture scope', () => {
    const state = buildSidebarState({ roots: 60, cutoffRoots: 50 })
    const { data, workspaces } = createBaselineData(state)
    for (const effectiveProjectId of FIXTURE_SCOPES) {
        const blocks = computeSidebarSessionBlocks({ data, workspaces, effectiveProjectId, activeWorkspaceId: null,
            sessionId: null, showArchived: false, showArchivedProjects: false, showActiveAcrossFilters: true })
        assert.ok(blocks.natural.length > 0, String(effectiveProjectId))
        assert.ok(blocks.natural.every(s => !s.parent_session_id))
    }
})
```

The fixture holds: worktree projects `p2-wt1`, `p2-wt2` (`worktree_of: 'p2'`, ids not in activity order); workspaces `w` = `{p0, p1}` and `w1` = `{p0}`; two unread sessions without a process outside `p0`, outside `w` and outside the `p2` scope; two `pinned: 'all'` roots outside `p0`, outside `w` and outside the `p2` scope (so "Active elsewhere" and "Pinned elsewhere" hold at least two members in `p0` and in every multi-project scope: exactly two in `p0`, `ws:w` and `ws:w1`; in `p2`, "Active elsewhere" also holds the `p0` sessions with a process). Every other root is read and has no process; the three `p0` sessions with a process (read in the fixture) are above the `p0` and `__all__` cutoffs; the `p0` ancestor of the tree extension is read. Cutoff placement: at least one read root without a process in `p0` has an `mtime` between the `p0` cutoff and the `__all__` cutoff, and one in the `p2` scope between the `p2` cutoff and the `__all__` cutoff, and one `p2`-scope root is below the `__all__` cutoff, so the single-project and the multi-project lists of the same projects differ. The draft and the launched ephemeral root are in project `p3` (outside `p0`, `w` and `p2`); one `p0` ancestor older than every cutoff (`p0` and `__all__`), so the tree extension is non-empty in `p0`, `ws:w` and `ws:w1`; one process in `assistant_turn` and two in `user_turn` in `p0` (one with `background_work_in_progress: { shells: 1 }`, one with `active_crons` holding one entry, so the unread background and shell counts are non-zero when they read as unread); one draft and one launched ephemeral root; `localState.projects` entries for `__all__`, `p0`, `p2`, `ws:w`, `ws:w1` with `hasMoreSessions: true` and distinct `oldestSessionMtime`.

- [ ] **Step 3: Run it.** `cd /home/twidi/dev/twicc-poc/frontend && node --test tests/helpers/sidebarFixture.test.js` → PASS.

- [ ] **Step 4: Move the constants and the retain helpers.** `ALL_PROJECTS_ID` goes to `constants.js`; `hasActiveStartupPhase` to `utils/startupPhase.js` (it returns `false` for a missing `startupProgress`); `retainSessionArray` to `utils/retain.js` with the two new helpers and their tests (`retainIds(null, null)` returns `null`); `stores/data.js` imports both and keeps `export const ALL_PROJECTS_ID` as a re-export. `utils/sidebarSessions.js` imports `sessionSortComparator` from `./sessionSort.js`, `ALL_PROJECTS_ID` from `../constants.js`, and uses `.js` on every relative import. No logic change.

- [ ] **Step 5: Switch `utils/sidebarSessionsStability.test.js`** to `import { computeSidebarSessionBlocks } from './sidebarSessions.js'`, with its workspace scope written `ws:w`. Its assertions stay.

- [ ] **Step 6: Run** `cd /home/twidi/dev/twicc-poc/frontend && npm test` → all PASS.

- [ ] **Step 7: Commit** (`test(sidebar): freeze the baseline derivations and make sidebarSessions node-loadable`).

### Task 2: Root-session index

**Files:**
- Create: `frontend/src/utils/rootSessions.js`
- Modify: `frontend/src/stores/data.js` (getter `rootSessions`), `frontend/tests/helpers/sidebarFixture.js` (`createShippedData().data.rootSessions`)
- Test: `frontend/src/utils/rootSessions.test.js`

**Interfaces:**
- Consumes: `hasActiveStartupPhase` (Task 1).
- Produces: `getRootSessions(owner)`; data-store getter `rootSessions: (state) => getRootSessions(state)`.

One lazily created computed per raw `owner.sessions` object (`WeakMap` keyed by `toRaw(owner.sessions)`). Outside startup: `Object.values(owner.sessions).filter(s => !s.parent_session_id)`. During startup (phase read from `owner.startupProgress` on each run): iterate `Object.keys(owner.sessions)`, read `parent_session_id` on `toRaw(owner.sessions)[id]`, push `owner.sessions[id]` (reactive). Retain with `retainSessionArray` from `./retain.js`. Call `noteStageRun('rootSessions')` at the start of each run.

- [ ] **Step 1: Write the failing tests:**

```js
test('roots keep insertion order and exclude subagents', ...)            // ids === ['a', 'b', 'c']
test('a subagent add or removal keeps the same array instance', ...)     // strictEqual after add, then after delete
test('a root add returns a new array with the root at the end', ...)
test('roots are reactive records also during startup', ...)              // isReactive(...) with startupPhase(state, true)
test('during startup parent_session_id is read without tracking', ...)   // counting getter on the raw record; a write reruns nothing
```

- [ ] **Step 2: Run** `node --test src/utils/rootSessions.test.js` → FAIL (module missing).
- [ ] **Step 3: Implement `getRootSessions(owner): Session[]`** and the `rootSessions` getter.
- [ ] **Step 4: Run** the test file → PASS; then `npm test` → PASS.
- [ ] **Step 5: Commit** (`perf(sidebar): add a shared root-session index`).

### Task 3: Per-scope stable lists

**Files:**
- Modify: `frontend/src/utils/sessionLists.js`, `frontend/src/stores/data.js` (getters `getScopeSessionList`, `getProjectSessions`, `getAllSessions`)
- Modify: `frontend/src/stores/sessionListsStability.test.js` (harness only), `frontend/tests/helpers/sidebarFixture.js`
- Test: `frontend/src/utils/sessionLists.test.js`

**Interfaces:**
- Consumes: `getRootSessions`, `hasActiveStartupPhase`.
- Produces: `resolveSessionScope`, `getScopeSessionList`, `getStableSessionList(state, scopeId, allProjectsId)` (the old fourth parameter is dropped; the phase is read from `state.startupProgress`). Data-store getters: `getScopeSessionList: (state) => (scope) => getScopeSessionList(state, scope)`; `getProjectSessions` and `getAllSessions` return `getStableSessionList(...)` as today.

Per scope key, three retaining computeds (§5.2), each calling `noteStageRun` with its fixed name (`'scope:candidates'`, `'scope:members'`, `'scope:ordered'`) at the start of each run: `candidates` (roots on the project-id set and `!hidden`), `members` (cutoff of `state.localState.projects[scope.cursorId]` when `hasMoreSessions`), `ordered` (`sessionSortComparator(state.processStates)`). During startup (§5.3), each stage reads `hasActiveStartupPhase(state.startupProgress)` on each run, reads fields through `toRaw`, and outputs raw records; after startup they output reactive records. `getStableSessionList(state, id, all)` = `getScopeSessionList(state, { key, projectIds: id === all ? null : [id], cursorId: id }).ordered`.

- [ ] **Step 1: Write the failing tests** (use `buildSidebarState`; rerun counts from `observeStageRuns`; field reads from counting getters installed with `Object.defineProperty` on records; the scope stages read no unread value, so every tick in this task uses an already-unread session and causes no flip; a test first makes its session unread by setting `last_new_content_at` above `last_viewed_at`, without an `mtime` change, before it installs its observer or counters; no tick in this task is `open: true`):

```js
test('an active in-scope tick reads only in-scope candidate mtimes and reruns nothing after members', ...)  // 7.1 #1, scopes p0 and ws:w
test('a tick of a session without a process outside the scope does not rerun the scope list', ...)        // 7.1 #2, scopes p0 and ws:w
test('a subagent add or removal reruns no scope stage', ...)                                               // 7.1 #10 (scope part)
test('a one-project workspace and the same project scope are two lists with their own cursors', ...)     // ws:w1 vs p0: keys differ; members differ
test('a main repository with worktrees uses the all-projects cursor', ...)                                // p2
test('a falsy project id resolves to the all-projects list and cursor', ...)
test('a session crossing the cutoff joins and leaves the members', ...)                                    // Review Focus 2
test('hide and unhide move a session out of and back into the candidates', ...)
test('during startup the stages track no session field, rerun on each progress message and output raw records', ...) // 7.1 #6 (scope part); a hide, a cutoff crossing and an order change show up after progressMessage(state)
test('after startup the lists output reactive records and track fields again', ...)
```

- [ ] **Step 2: Run** `node --test src/utils/sessionLists.test.js` → FAIL.
- [ ] **Step 3: Implement** `resolveSessionScope`, `getScopeSessionList`, the new `getStableSessionList`, and the store getters.
- [ ] **Step 4: Adapt `stores/sessionListsStability.test.js`.** Its getter extraction supplies the new names, and its startup fixture uses a real phase shape (`{ background_compute: { x: { completed: false } } }` instead of `{ running: true }`, since the predicate now comes from `startupPhase.js`). Every assertion stays. Run both files → PASS; `npm test` → PASS.
- [ ] **Step 5: Commit** (`perf(sidebar): give each scope its own stable session list`).

### Task 4: Unread memos and the unread index over the roots

**Files:**
- Create: `frontend/src/utils/unreadMemos.js`
- Modify: `frontend/src/utils/projectProcessActivity.js`, `frontend/src/stores/data.js` (getter `hasUnreadContentMemo`), `frontend/tests/helpers/sidebarFixture.js`
- Modify (harness only if needed): `frontend/src/utils/projectProcessActivity.test.js`, `frontend/src/components/ui/AggregatedProcessIndicator.activity.test.js`, `frontend/src/stores/processSnapshots.test.js`
- Test: `frontend/src/utils/unreadMemos.test.js`

**Interfaces:**
- Consumes: `getRootSessions`.
- Produces: `getUnreadMemos(owner)`; data-store getter `hasUnreadContentMemo: (state) => (session) => getUnreadMemos(state).unreadContent(session)`. `getProjectActivityIndex(store)` keeps its public API (`summary`, `unread`, `totalUnread`) and its per-project shape `{ count, background, shells }`.

Memos: two `WeakMap`s keyed by `toRaw(session)`, per raw `owner.sessions`; each memo run calls `noteStageRun('memo:unread')` / `noteStageRun('memo:unreadContent')`, and each `unreadCounts` run calls `noteStageRun('unread:counts')`. Each memo computes through `reactive(toRaw(session))`: `unread` = `isSessionUnread(rec, owner.processStates[rec.id])`, `unreadContent` = `hasUnreadContent(rec)`. `unreadCounts` iterates `getRootSessions(store)`, reads `unread`, and for a true memo reads `unreadBackgroundKind(processState)` as today.

- [ ] **Step 1: Write the failing tests** (rerun counts from `observeStageRuns`):

```js
test('a timestamp change that does not flip the result does not rerun unreadCounts', ...)        // 7.1 #5 (index part): the changed session is an out-of-scope root without a process that is already unread, and its last_new_content_at advances (the baseline reads timestamps there)
test('a flip updates unreadCount and the background and shell counts', ...)                    // keeps { count, background, shells }; flips the two p0 user_turn sessions (read in the fixture, one with a shell, one with a cron) to unread
test('an unread flip reads no field of a subagent', ...)                                        // 7.1 #10 (unread part): counting getters on a subagent's parent_session_id, hidden and pinned
test('a subagent add or removal does not rerun unreadCounts', ...)                              // 7.1 #10
test('a memo first evaluated with a raw record during startup still updates after startup', ...) // 7.1 #6
test('removing a session drops it from the counts at once', ...)                                // Review Focus 3; first makes a p0 root unread (last_new_content_at above last_viewed_at), then removes it and asserts the count drops by 1
test('the summary equals the frozen baseline index along an update sequence', ...)              // 7.1 #9 (unread part), along FIXTURE_UPDATE_SEQUENCE; summary([pid]) for every fixture project and totalUnread() after each step
```

- [ ] **Step 2: Run** `node --test src/utils/unreadMemos.test.js` → FAIL.
- [ ] **Step 3: Implement** `getUnreadMemos(owner)`, the new `unreadCounts`, and the store getter.
- [ ] **Step 4: Run** the new test file and the three existing test files above → PASS; `npm test` → PASS.
- [ ] **Step 5: Commit** (`perf(sidebar): count unread sessions through per-session memos`).

### Task 5: Activity-free project inputs

**Files:**
- Create: `frontend/src/utils/projectScopes.js`
- Modify: `frontend/src/stores/data.js` (getters `archivedProjectIds`, `worktreeIdsByParent`), `frontend/src/stores/workspaces.js` (`getVisibleProjectIds`, `getAllProjectIds` delegate to one `createWorkspaceMembership` instance per store), `frontend/src/components/peer/PeerMessageReviewDialog.vue` (its `archivedProjectIds` computed reads `dataStore.archivedProjectIds`), `frontend/tests/helpers/sidebarFixture.js`
- Modify (harness only if needed): `frontend/src/components/project/NewSessionProjectPicker.test.js`, `frontend/src/components/sidebar/SidebarRail.recents.test.js`, `frontend/src/components/project/projectActivityUi.test.js`, `frontend/src/components/timeline/TimelinePanel.test.js`
- Modify: `frontend/src/composables/useRailRecentProjects.test.js`: it runs the real getter block of `stores/workspaces.js` (source extraction) over Pinia, so its fake data store must now supply `worktreeIdsByParent` and the `createWorkspaceMembership` import. It is the proof of the store glue. Add a test that reads `getVisibleProjectIds('w')` and `getAllProjectIds('w')` inside a `computed`, then adds `projects.wt = { id: 'wt', worktree_of: 'p' }`, and asserts through the same `computed` that both results now hold `'wt'`; then that a project `mtime` change keeps both array instances **and** reruns neither the `computed` (count its evaluations) nor `'workspace:visible'` / `'workspace:all'` (§7.1 #3, store side). (A `recentWorkspaces` assertion cannot prove this: recent projects exclude worktrees.)
- Test: `frontend/src/utils/projectScopes.test.js`

**Interfaces:**
- Produces: the `projectScopes.js` exports (signatures above); data-store getters `archivedProjectIds: (state) => getArchivedProjectIds(state)`, `worktreeIdsByParent: (state) => getWorktreeIdsByParent(state)`. `getVisibleProjectIds(wsId)` and `getAllProjectIds(wsId)` keep their signatures and return `membership.visible(wsId)` / `membership.all(wsId)`. `createShippedData` and the Task 7 harness use `createWorkspaceMembership` too.

`createWorkspaceMembership` holds the per-workspace retention (`retainIds`) for each mode, and calls `noteStageRun('workspace:visible')` / `noteStageRun('workspace:all')` on each evaluation. Every parameter is a function called on each evaluation (the store passes `() => dataStore.worktreeIdsByParent`, never the `Map` itself), so a caller computed tracks what they read and a worktree added later joins at once. Rules, copied from the baseline:
- **visible mode:** members in `workspace.projectIds` order; a member is skipped when `getProject(id)` is unknown, or archived while `isShowArchived()` is false; a kept member is immediately followed by its worktrees (project-id order), each skipped under the same two conditions; no duplicates.
- **all mode:** every member in order, known or not, each followed by all its worktrees (project-id order); no duplicates.

- [ ] **Step 1: Write the failing tests:**

```js
test('archivedProjectIds reads no mtime and keeps its instance on an activity change', ...)
test('the worktree index ignores activity and lists worktrees in id order', ...)
test('visible and all modes equal the frozen baseline, member order exact and worktrees as a set', ...) // includes an unknown member id, archived members, and a member with two worktrees (ids not in activity order, one archived); run with isShowArchived() true and false
test('a project mtime change reruns no membership evaluation', ...)          // 7.1 #3 (membership part): a computed over visible('w') is not re-evaluated; no 'workspace:*' run
test('archiving a member changes the visible ids at once', ...)               // Review Focus 1
test('a worktree added to a member appears in the visible ids at once', ...)  // Review Focus 1; new project { worktree_of: 'p0' }
```

- [ ] **Step 2: Run** `node --test src/utils/projectScopes.test.js` → FAIL.
- [ ] **Step 3: Implement** `projectScopes.js`, the data-store getters, the workspaces delegation, and the dialog change.
- [ ] **Step 4: Audit** `grep -rn "getVisibleProjectIds\|getAllProjectIds" frontend/src`: confirm no consumer displays worktrees in the order of that result (§5.6); note the result in the commit body. Run `npm test` → PASS.
- [ ] **Step 5: Commit** (`perf(workspaces): resolve workspace membership without project activity`).

### Task 6: Sidebar block stages and their wiring factory

**Files:**
- Modify: `frontend/src/utils/sidebarSessions.js`, `frontend/src/utils/sessionTree.js` (`extendTreeCandidates`), `frontend/src/stores/data.js` (getter `inStartupPhase`), `frontend/tests/helpers/sidebarFixture.js`
- Modify: `frontend/src/utils/sidebarSessionsStability.test.js` (harness only: the base `fixture()` supplies `rootSessions`, `getScopeSessionList`, `archivedProjectIds`, `hasUnreadContentMemo` and `inStartupPhase` from `createShippedData`; `paginatedAncestorFixture` sets the pagination window with a real cutoff, `localState.projects.__all__ = { hasMoreSessions: true, oldestSessionMtime: … }`, instead of overriding `getProjectSessions` / `getAllSessions`, which the natural stage no longer reads), `frontend/src/utils/sessionTree.test.js` (harness only)
- Test: `frontend/src/utils/sidebarBlocks.test.js`

**Interfaces:**
- Consumes: `rootSessions`, `getScopeSessionList`, `resolveSessionScope`, `archivedProjectIds`, `hasUnreadContentMemo`, `getVisibleProjectIds`, `hasActiveStartupPhase`.
- Produces: the `sidebarSessions.js` exports (signatures above); data-store getter `inStartupPhase: (state) => () => hasActiveStartupPhase(state.startupProgress)` (a getter returning a function, so each call reads `startupProgress` and no action wrapper runs in hot computeds); `extendTreeCandidates(natural, { roots, lookup, isInScope, passesFilter })`, where `roots` is iterated to build the child index and `lookup(id)` resolves ancestors (they replace `sessionsById`).

Rules (§5.4, §5.3, §5.7):
- `computeSidebarNaturalMembership` resolves the scope with `resolveSessionScope`, reads `list.members` (never `ordered`), applies the archive filter (`data.archivedProjectIds` when `showArchivedProjects` is off), and builds the tree extension from the filtered natural members in `members` order. It calls `data.inStartupPhase()` on each run; while that is true it reads natural records, the `roots` given to `extendTreeCandidates` and the tree lookups through `toRaw`.
- `naturalIds` = the archive-filtered members ∪ the tree extension (as the baseline builds it after the merge), so a pinned extension ancestor never also lands in "Pinned elsewhere".
- `computeSidebarCrossFilterMembership` builds both cross-filter blocks from `data.rootSessions` (no sort), excluding `naturalIds`; `crossFilterActive` uses `data.hasUnreadContentMemo`. It never reads the startup phase, so it keeps today's tracking during startup.
- `orderSidebarNatural` returns `list.ordered` restricted to `naturalIds`; when `extension` is non-empty it merges and sorts with `sessionSortComparator`, and calls `data.inStartupPhase()` so it reruns on each progress message.
- `orderSidebarBlock` sorts a copy with `sessionSortComparator(processStates)`.
- `computeSidebarExtra` keeps today's rule: the selected session when it is in none of the three sets, read through `data.sessions[sessionId]`, never a subagent.
- Each stage calls `noteStageRun` with its fixed name; `computeSidebarNaturalMembership` calls `noteStageRun('blocks:extension')` just before it runs `extendTreeCandidates`.
- `useSidebarSessionBlocks(getOptions)` wires: `natural` (natural membership) → per-field computeds `list`, `naturalIds`, `extension` → `crossFilter` (reads `naturalIds` only from the natural side) → per-field computeds for the two cross-filter lists → `naturalOrder`, `pinnedOrder`, `activeOrder` → `blocks` (adds `extra`; returns the previous object when its four fields are identical). Each stage returns its previous output when equal. `stages` exposes the stage computeds for the tests.
- `computeSidebarSessionBlocks(options, previous)` runs the same functions once, for the command palette and `PeerMessageReviewDialog.vue`.

- [ ] **Step 1: Write the failing tests** (shipped facade from `createShippedData`, oracle from `createBaselineData`; rerun counts from `observeStageRuns`; field reads from counting getters installed with `Object.defineProperty` on records):

```js
test('an active in-scope tick reruns no block stage, no unreadCounts and none of its memos', ...)  // 7.1 #1 (block part), scopes p0 and ws:w; tick(state, id, { open: true }) on the assistant_turn session; no 'blocks:*', no 'unread:counts', and no 'memo:unread' / 'memo:unreadContent' run for that session
test('a project mtime change reruns no block stage', ...)                                          // 7.1 #3; scopes p0 and ws:w with showArchivedProjects: false (the only path where the baseline reads getProjects); facade getProjects is the activity-sorted baseline one
test('a natural reorder reruns the natural order stage only', ...)                                 // 7.1 #4, scopes p0 and ws:w; the ticked session is a natural member in both scopes (above both cutoffs) and is read before the tick, so the tick also flips its unread state (a cross-filter stage that reads the memo before the naturalIds exclusion is then caught)
test('a reorder inside Active elsewhere with two members reruns only that block sort', ...)       // 7.1 #4; the ticked member is already unread (no flip)
test('a non-flipping timestamp change reruns no membership stage; a flip updates Active elsewhere', ...) // 7.1 #5 (block part), scopes p0 and ws:w; the changed session is an out-of-scope root without a process (the baseline cross-filter reads its timestamps; an in-scope one returns before)
test('an out-of-scope memo flip reruns the cross-filter membership but not the natural stages', ...)    // §5.7 row 4
test('during startup a progress message reruns the natural stages but not the cross-filter membership', ...) // §5.7 last row
test('during startup the cross-filter blocks react at once to a pin, an archive and an unread flip', ...)  // 7.1 #6
test('during startup an archive, a hide, a cutoff crossing, a natural reorder and an extension order or pin change show up at the next progress message', ...) // 7.1 #6
test('block outputs hold raw natural records during startup and reactive records after it', ...)      // 7.1 #6
test('during startup, writes between two progress messages rerun no natural stage', ...)              // 7.1 #6 (no tracking), flat and tree with an extension:
//   write archived, hidden, mtime, pinned, spawned_by, tree_detached on natural and extension roots;
//   no 'blocks:natural', 'blocks:extension' or 'blocks:naturalOrder' run until progressMessage(state)
test('in tree mode an mtime tick without a process never reruns extendTreeCandidates', ...)      // 7.1 #7, with and without an order change; once on a root without a process that is a natural member in both scopes (above both cutoffs) and that the test first makes unread (last_new_content_at above last_viewed_at, no mtime change) before its observer (no flip), and once on a read root that is a natural member in both scopes (flip)
test('a subagent add or removal reruns no block stage; an unreadContent flip reads no subagent field', ...) // 7.1 #10; counting getters on a subagent's parent_session_id, hidden and pinned
test('every fixed stage name runs at least once along the shared update sequence', ...)            // positive control for the whole stageRuns vocabulary (all 15 names), along FIXTURE_UPDATE_SEQUENCE; subscribed: the blocks flat and tree in p0 and ws:w, the unread index summary, and a computed reading getAllProjectIds('w')
test('the blocks equal the frozen baseline in every fixture scope, flat and tree, along an update sequence', ...) // 7.1 #9, along FIXTURE_UPDATE_SEQUENCE; extension ties compared as sets
test('during startup the blocks after each progress message equal the baseline outside startup, and equal the baseline at the end', ...) // 7.1 #9 (startup)
test('a workspace whose member gets archived switches to the new scope list', ...)                 // Review Focus 1; scope ws:w only; asserts stages.natural.value.list changes, not only the blocks
test('a worktree added to a workspace member brings its sessions into the ws:w natural block', ...)  // Review Focus 1; scope ws:w only; first adds roots with project_id 'p0-wt', hidden false and an mtime above the __all__ cutoff, then the project { id: 'p0-wt', worktree_of: 'p0' }
test('a removed session leaves Active elsewhere at once', ...)                                      // Review Focus 3
test('a selected subagent is never extra', ...)                                                     // Review Focus 4
test('drafts and ephemeral roots keep their place and stay out of Active elsewhere', ...)           // Review Focus 5; first gives the draft and the ephemeral root (both in p3) unread content, and the ephemeral root a process; asserts their place in scope p3 (ephemeral first), and their absence from Active elsewhere in p0 and ws:w, so the draft/ephemeral exclusion is what keeps them out
```

- [ ] **Step 2: Run** `node --test src/utils/sidebarBlocks.test.js` → FAIL.
- [ ] **Step 3: Implement** the stages, the factory, the wrapper, the `extendTreeCandidates` options, and `inStartupPhase`.
- [ ] **Step 4: Run** the new file, `sidebarSessionsStability.test.js` and `sessionTree.test.js` → PASS; `npm test` → PASS.
- [ ] **Step 5: Commit** (`perf(sidebar): split the sidebar blocks into membership and order stages`).

### Task 7: `SessionList.vue` on the factory

**Files:**
- Modify: `frontend/src/components/session/list/SessionList.vue`
- Modify (harness only): `frontend/src/components/session/list/SessionList.loading.test.js`, `frontend/src/components/session/list/SessionList.tree.test.js`
- Test: `frontend/src/components/session/list/SessionList.rowStability.test.js`

**Interfaces:**
- Consumes: `useSidebarSessionBlocks`, `resolveSessionScope`, `store.getScopeSessionList`, `retainIds` (from `utils/retain.js`), `createWorkspaceMembership`.

Changes:
- `sessionBlocks` becomes `useSidebarSessionBlocks(() => ({ ...the same options as today })).blocks`; `natural`, `extraSessionId`, `separatorBeforeIds`, `switcherIds` read it as today.
- `canonicalSessions` returns `store.getScopeSessionList(resolveSessionScope({ effectiveProjectId: props.projectId, getProjectScopeIds: store.getProjectScopeIds, getVisibleProjectIds: workspacesStore.getVisibleProjectIds })).members`.
- `scopeProjectIds` returns `retainIds(previous, next)`.

The test file uses the SFC harness (`compileComponent`, `makeRenderer`, `virtualScroller` from `tests/helpers/scrollerComponentHarness.js`). It maps `utils/sidebarSessions`, `utils/sessionLists.js`, `utils/retain.js`, `utils/projectScopes.js`, `utils/workspaceIds` (the real module, so `ws:` ids are workspaces) and the workspace store to the shipped modules. Its store facade's `getProjectScopeIds` is the real logic (it iterates `state.projects` for `worktree_of`, as `stores/data.js` does), never `id => [id]` (the workspace store facade delegates to `createWorkspaceMembership`). The store facade uses the activity-sorted baseline `getProjects` / `getWorktreesOf` and the shipped getters from `createShippedData`. A `SessionListItem` stub counts its renders.

- [ ] **Step 1: Write the tests:**

```js
// Red before this task:
test('an out-of-scope reorder of a session without a process reads no root field in workspace and main-repository scopes', ...)
//   counting getter on an out-of-scope root's project_id; tick(state, <an already-unread out-of-scope session without a process>, { top: true }), so it moves to the top without an unread flip; reads === 0
//   (today canonicalSessions refilters getAllSessions on that reorder)
test('adding an unrelated project keeps the scope project ids and re-renders no row in project scope', ...)
//   today getProjectScopeIds returns a new array, so every row re-renders
test('an in-scope reorder in the mounted list reads no out-of-scope root field', ...)
//   counting getter on an out-of-scope root's `pinned`; the ticked in-scope session has no process and is made unread before mount, before the counters are installed (no flip at the tick); today the single blocks computed reruns its cross-filter scans
// Guard (already passes after Tasks 5 and 6, keeps §7.1 #8 pinned):
test('a project activity change re-renders no row in project, workspace and all-projects scopes', ...)
//   positive control per scope: at least one counted render at mount, and a title change on one visible row's session re-renders that row once
```

- [ ] **Step 2: Run** `node --test src/components/session/list/SessionList.rowStability.test.js` → the three first tests FAIL, the guard PASSES.
- [ ] **Step 3: Implement** the three changes.
- [ ] **Step 4: Adapt** the two existing harnesses so they map the new imports; their assertions stay. Run the three files → PASS; `npm test` → PASS.
- [ ] **Step 5: Commit** (`perf(sidebar): wire the session list to the staged sidebar blocks`).

### Task 8: Benchmark script

**Files:**
- Create: `frontend/scripts/benchmark_sidebar_recompute.mjs`

**Interfaces:**
- Consumes: `buildSidebarState`, `createBaselineData`, `createShippedData`, `useSidebarSessionBlocks`, the frozen `computeSidebarSessionBlocks`, both activity indexes.

For each implementation, the script subscribes the blocks, the unread index and (tree scenarios) `buildSessionTree` + `flattenTree` through one `effect` with a scheduler. One batch = one `session_updated` flush, then one `project_updated` flush. It takes the median of 5 runs of 20 batches after 10 warm-up batches, at store sizes 3 000 and 9 000 (1 000 and 3 000 roots, 2 subagents each). Scenarios: the rows of spec §10, with "Active elsewhere" and "Pinned elsewhere" at two members each in the project and workspace scenarios (the all-projects scenarios have no cross-filter block); in the "without process outside the scope" scenarios the ticked session is one of the two "Active elsewhere" members (already unread, so the block keeps two members); "order changes" alternates two sessions without a process that each take the top. Output: one line per scenario and size with before, after and the ratio, then one line per threshold group (§7.2: single-project and workspace flat ≥ 20×, all-projects ≥ 10×, tree ≥ 10×) marked `ok` or `below`. Exit code 0 in every case (evidence, not a gate).

- [ ] **Step 1: Write the script.**
- [ ] **Step 2: Run** `cd /home/twidi/dev/twicc-poc/frontend && node scripts/benchmark_sidebar_recompute.mjs` (several minutes; run it in the background). Expected: every threshold group `ok`. If a group is `below`, stop and report the numbers; do not tune the thresholds.
- [ ] **Step 3: Commit** the script, with the measured table in the commit body (`test(sidebar): add the sidebar recompute benchmark`).

### Task 9: Final verification

- [ ] **Step 1: Run** `cd /home/twidi/dev/twicc-poc/frontend && npm test` → all PASS.
- [ ] **Step 2: Run** `cd /home/twidi/dev/twicc-poc/frontend && npm run build` → success (the app still builds with the moved constants and new modules).
- [ ] **Step 3: Hand the manual check of spec §7.3 to the user** as a checklist in the final report: a Firefox profile during a turn with a long-lived tab; order, sections, unread eyes and badges, favicon and pagination in project, main repository with worktrees, workspace and all-projects scopes, flat and tree; the command palette "Go to Session…"; the reply-target picker of the peer message review dialog; one backend restart with a large history (the user restarts the backend). Do not restart any server.
