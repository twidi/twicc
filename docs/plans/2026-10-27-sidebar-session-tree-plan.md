# Sidebar Session Tree Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a "Show as tree" sessions-sidebar option (off by default) that nests sessions under the session that spawned them (`spawned_by`), with placement, ordering and expansion rules from the spec.

**Architecture:** Pure, node-testable modules hold all tree logic (`utils/sessionTree.js`, `utils/sessionTreeExpansion.js`). A thin Pinia store holds the expansion state. `computeSidebarSessionBlocks` gets an opt-in `showAsTree` flag that extends the `natural` block. `SessionList.vue` flattens the tree into the rows it already renders and passes row metadata to `SessionListItem.vue`. Flat mode keeps its code path untouched.

**Tech Stack:** Vue 3 `<script setup>`, Pinia, Web Awesome 3, `node --test` (`cd frontend && node --test <file>`).

**Spec:** `docs/plans/2026-10-27-sidebar-session-tree-spec.md` (read it first; section numbers below, `§N`, refer to it).

## Global Constraints

- Option label: **"Show as tree"**. Default **off**. Local setting (localStorage), **not** in `SYNCED_SETTINGS_KEYS`.
- Parent edge is `session.spawned_by` only. `parent_session_id` sessions and `hidden` sessions never take part (§2).
- Ordering reuses `sessionSortComparator` (`frontend/src/utils/sessionSort.js`) on effective values: pinned, then active by highest `started_at`, then highest `mtime` (§6, §8). Never write a second comparator.
- Indentation is capped at about **3 levels** (§9). Rows moving between positions are **not** animated (§8).
- Pure modules import with explicit `.js` extensions (they are loaded by plain `node --test`). `sidebarSessions.js` keeps extensionless imports: its test harness strips them (see `utils/sidebarSessionsStability.test.js`).
- Flat mode rules do not change. The command palette and the switcher call `computeSidebarSessionBlocks` without `showAsTree`.
- All code, comments and UI strings in English. No `npm install`, no server restart, no migration (this feature is frontend-only).
- **No commit steps in this plan.** The owner commits on explicit request only. Stay on `main`; no branch or worktree. No CHANGELOG entry unless the owner asks.
- Web Awesome rules from `CLAUDE.md` apply to any `wa-*` markup (icon slots, imports in `main.js`, custom events bubble).

## Review Focus

Inputs the spec implies but that no happy-path test exercises. Each has a pinning test in the owning task.

1. **`spawned_by` cycle in loaded data** → both nodes become roots, no infinite loop (Task 2).
2. **Parent loaded in the store but `hidden`, a sub-agent, ephemeral or draft** → no hint, chain ends there (Task 2).
3. **Reload with a persisted collapse and an already-active descendant** → stays collapsed, no re-expansion (Task 5).
4. **Selected session under a collapsed node at first render (deep link)** → path expands once; a later manual collapse sticks (Task 5).
5. **Transient fetch failure of an ancestor** → retried on the next data change; 404 is final (Task 7).

---

## File Structure

| File | Responsibility |
|---|---|
| `frontend/src/utils/sessionTree.js` (new) | Build the tree, effective state, ordering, flatten, candidate extension, missing-ancestor ids |
| `frontend/src/utils/sessionTreeExpansion.js` (new) | Pure expansion state: explicit choices, sticky auto-expansion, "seen" descendants, reveal |
| `frontend/src/stores/sessionTreeExpansion.js` (new) | Pinia wrapper: reactive state, localStorage persistence of explicit choices |
| `frontend/src/composables/useSessionTreeAncestors.js` (new) | Fetch missing ancestors by id (in-flight dedupe, 404 final, transient retry) |
| `frontend/src/stores/settings.js` | New setting `showSessionsAsTree` |
| `frontend/src/utils/sidebarSessions.js` | `showAsTree` flag: extend `natural`, keep cross-filter blocks disjoint |
| `frontend/src/components/session/list/SessionList.vue` | Flatten tree into rows, section classification, switcher ids, reveal, wiring |
| `frontend/src/components/session/list/SessionListItem.vue` | Indentation, toggle, collapsed summary, parent hint |
| `frontend/src/components/session/SessionsSidebarControls.vue`, `frontend/src/views/ProjectView.vue` | The "Show as tree" menu item and prop plumbing |

Tests sit next to their modules (`*.test.js`).

---

### Task 1: The `showSessionsAsTree` setting

**Files:**
- Modify: `frontend/src/stores/settings.js` (defaults near `showActiveAcrossFilters: false`, validators near line 184, getters near line 401, setters near line 873, the persisted object near line 1275)
- Test: `frontend/src/stores/sessionTreeSetting.test.js`

**Interfaces:**
- Produces: `settingsStore.isShowSessionsAsTree` (getter, boolean), `settingsStore.setShowSessionsAsTree(enabled: boolean)`.

- [ ] **Step 1: Write the failing test.** Model it on `stores/sidebarRailSetting.test.js` (source regex checks). Assertions: default `showSessionsAsTree: false,`; validator `showSessionsAsTree: (v) => typeof v === 'boolean'`; getter `isShowSessionsAsTree: (state) => state.showSessionsAsTree`; setter `setShowSessionsAsTree(enabled)` using `SETTINGS_VALIDATORS.showSessionsAsTree(enabled)`; persisted `showSessionsAsTree: store.showSessionsAsTree,`; `SYNCED_SETTINGS_KEYS.has('showSessionsAsTree') === false`.
- [ ] **Step 2: Run** `cd frontend && node --test src/stores/sessionTreeSetting.test.js`. Expected: FAIL.
- [ ] **Step 3: Implement** the five registration points in `settings.js`, copying the `showActiveAcrossFilters` pattern. The setter comment says it is toggled only from the session list options dropdown.
- [ ] **Step 4: Run** the same command. Expected: PASS.

---

### Task 2: Tree structure (parents, roots, hints)

**Files:**
- Create: `frontend/src/utils/sessionTree.js`
- Test: `frontend/src/utils/sessionTree.test.js`

**Interfaces:**
- Produces:
  - `buildSessionTree(candidates: Session[], opts: { lookup: (id: string) => ?Session, processStates?: Object, viewedId?: ?string, isUnread?: (session, processState) => boolean }): Tree`
  - `Tree = { nodes: Map<string, TreeNode>, rootIds: string[] }`
  - `TreeNode = { id, session, parentId: ?string, childIds: string[], hintParent: ?Session, effective, triggerIds: string[], summary }` — this task fills `id, session, parentId, childIds, hintParent`; Task 3 fills the rest.
- Rules (§5):
  - A **participant** is a candidate that is not `ephemeral` and not `draft`. Non-participants are roots with no children and no hint; they are never an ancestor.
  - `parentId` = nearest ancestor reached by walking `spawned_by` through `lookup` that is a participant candidate. The walk passes through loaded sessions that are not candidates. It stops at an unknown session, a `hidden` one, one with `parent_session_id`, or an ephemeral/draft one. The walk keeps a visited-set so it terminates. Cycle detection is a **second pass over the resolved `parentId` graph**: every node on a cycle (including a self-reference) becomes a root with `parentId = null`, so no session disappears (§4).
  - `hintParent` is set only on a root whose **direct** parent (`lookup(session.spawned_by)`) exists, is **not a participant candidate** (§5.3: outside the candidate set; this also keeps cycle roots hint-free), and is not hidden, a sub-agent, ephemeral or draft. Otherwise `null`.
  - Input order of `candidates` is kept inside `childIds` and `rootIds` for now.

- [ ] **Step 1: Write the failing tests** (`node:test`, `assert/strict`). Helper: `const s = (id, o = {}) => ({ id, project_id: 'p', mtime: 100, spawned_by: null, ...o })` and `lookupOf = list => id => list.find(x => x.id === id) ?? null`. Tests, each asserting `tree.rootIds`, `nodes.get(id).parentId`, `.childIds`, `.hintParent?.id`:
  - `child nests under a present parent`
  - `grandparent adopts when the parent is loaded but not a candidate` (parent in `lookup` only)
  - `root with a loaded out-of-candidates parent carries hintParent` (hint id equals the parent id; child is a root)
  - `hidden, sub-agent, ephemeral and draft parents give no hint and end the chain` (four cases; child is a root, `hintParent === null`) — Review Focus 2
  - `unknown parent gives a root without hint`
  - `a spawned_by cycle yields roots, never an empty forest` — cases: two nodes (`a.spawned_by = 'b'`, `b.spawned_by = 'a'`), three nodes, a self-reference, and a cycle with a tail (the tail child stays nested under a cycle node that became a root); in every case `hintParent` is `null` on the cycle roots — Review Focus 1
  - `ephemeral and draft candidates are roots with no children even when a session points at them`
- [ ] **Step 2: Run** `cd frontend && node --test src/utils/sessionTree.test.js`. Expected: FAIL (module missing).
- [ ] **Step 3: Implement** `buildSessionTree` structure per the rules above in `sessionTree.js` (explicit `.js` imports only). Approach: index candidates by id, resolve each participant's `parentId` with the guarded walk, then derive `childIds`/`rootIds`.
- [ ] **Step 4: Run** the same command. Expected: PASS.

---

### Task 3: Effective state, ordering, triggers, summary

**Files:**
- Modify: `frontend/src/utils/sessionTree.js`
- Test: `frontend/src/utils/sessionTree.test.js`

**Interfaces:**
- Consumes: Task 2's `buildSessionTree` (extended in place), `sessionSortComparator` from `./sessionSort.js`.
- Produces on each `TreeNode`:
  - `effective: { pinned: ?string, active: boolean, activeKey: number, dateKey: number }` — own pin or the first descendant's pin; `active` if the node or a descendant has `processStates[id] != null`; `activeKey` = highest `started_at` among active nodes of the subtree (0 if none); `dateKey` = highest `mtime` of the subtree (§6).
  - `triggerIds: string[]` — **descendants** (not the node) that are active, or unread (`id !== viewedId && isUnread(session, processStates[id])`), or pinned (§9).
  - `summary: { descendants: number, active: number, unread: number, pinned: number, latestDate: ?number }` — counts over loaded descendants; `latestDate = dateKey` only when `dateKey > session.mtime`, else `null` (§9).
- After this task `rootIds` and every `childIds` are ordered by `sessionSortComparator` applied to **proxy sessions** built from the effective values: `{ id, pinned: effective.pinned, mtime: effective.dateKey, ephemeral, draft, ephemeralStartedAt }` with a proxy `processStates` `{ [id]: { started_at: activeKey } }` for active nodes. Do not reimplement the comparator.

- [ ] **Step 1: Write the failing tests** (same file). Assertions on `effective`, `triggerIds`, `summary` and on `tree.rootIds` / `childIds` order:
  - `idle root with an active child sorts before a newer idle root` (§7)
  - `a pinned descendant makes the root pinned and sorts it first`
  - `activeKey is the highest started_at of the subtree; two active subtrees order by it`
  - `a write to an active node (its mtime grows) does not reorder the siblings` — build, bump `mtime` of an active leaf, rebuild, assert same `rootIds` (§8 stability)
  - `idle subtrees order by the highest mtime of the subtree`
  - `children under one parent use the same effective ordering`
  - `triggerIds exclude the node itself and the viewed session counts as not unread`
  - `summary.latestDate is null when the subtree date equals the node's own mtime`
- [ ] **Step 2: Run** `cd frontend && node --test src/utils/sessionTree.test.js`. Expected: new tests FAIL.
- [ ] **Step 3: Implement** a post-order pass computing `effective`, `triggerIds`, `summary`, then sort `childIds` and `rootIds` with the proxy comparator. Non-participant roots (ephemeral) use their own fields so `isLaunchedEphemeral` ordering keeps working.
- [ ] **Step 4: Run** the same command. Expected: PASS.

---

### Task 4: Flatten into rows

**Files:**
- Modify: `frontend/src/utils/sessionTree.js`
- Test: `frontend/src/utils/sessionTree.test.js`

**Interfaces:**
- Consumes: `Tree` from Tasks 2-3.
- Produces:
  - `flattenTree(tree: Tree, isExpanded: (id: string) => boolean): Array<{ id: string, depth: number }>` — pre-order, roots at depth 0; children only under expanded nodes.
  - `export const TREE_MAX_INDENT_LEVEL = 3` (used by the row visuals).

- [ ] **Step 1: Write the failing tests:** `collapsed nodes hide their children`, `expanded nodes list children right after the parent with depth + 1`, `isExpanded = () => true lists the whole forest in display order` (this is the switcher order, §10).
- [ ] **Step 2: Run** `cd frontend && node --test src/utils/sessionTree.test.js`. Expected: FAIL.
- [ ] **Step 3: Implement** `flattenTree` and the constant.
- [ ] **Step 4: Run.** Expected: PASS.

---

### Task 5: Expansion rules (pure) and the Pinia store

**Files:**
- Create: `frontend/src/utils/sessionTreeExpansion.js`, `frontend/src/stores/sessionTreeExpansion.js`
- Test: `frontend/src/utils/sessionTreeExpansion.test.js`

**Interfaces:**
- Consumes: `Tree` (`nodes.get(id).childIds`, `.triggerIds`, `.parentId`).
- Produces (pure module):
  - `createExpansionState(explicit: Object<string, boolean> = {}): ExpansionState` with `{ explicit, auto: Set<string>, seen: Set<string>, revealed: Set<string>, seededScopes: Set<string> }`. `explicit` is the only persisted part. `seen` is a **global set of session ids** that already met a trigger condition (active, unread or pinned): "seen" belongs to the descendant session, not to a node. `seededScopes` holds the scope keys whose initial pass already ran.
  - `isExpanded(state, id): boolean` — `revealed.has(id)`, or `explicit[id] === true`, or `explicit[id] === undefined && auto.has(id)`.
  - `reconcileExpansion(state, tree, opts: { settled: boolean, scopeKey: string, seedTriggerIds: () => string[] }): void` — does **nothing** while `settled` is false (the initial load is not over: sessions or process states are still arriving).
  - `setExpanded(state, id, value: boolean, tree: Tree): void` — user action. Collapse sets `explicit[id] = false`, removes `id` from `auto` and from `revealed`, and adds the node's current `triggerIds` to `seen`. Expand sets `explicit[id] = true`.
  - `revealPath(state, tree, id, previousPath: string[] = []): string[]` — adds to `revealed` every ancestor of `id` that is not in `previousPath` (session-only, not persisted; wins over a persisted collapse) and returns the full current ancestor path. Passing the path returned by the previous call for the same selected session reveals only ancestors that are new, so an ancestor the user collapsed in between is not forced open again (§10).
- Produces (store): `useSessionTreeExpansionStore()` with state `{ state: ExpansionState }`, and actions `isExpanded(id)`, `reconcile(tree, settled, scopeKey, seedTriggerIds)`, `toggle(id, tree)`, `reveal(tree, id, previousPath)` (returns the new path). Only `explicit` is persisted, under `localStorage` key `twicc.sessionTree.explicit` (JSON, parse errors fall back to `{}`).
- `reconcileExpansion` rules (§9):
  - **Initial pass of a scope** (the first call with `settled: true` for a `scopeKey` not yet in `seededScopes`): `seen` gains every id returned by `seedTriggerIds()` (all loaded sessions that currently meet a condition), so the seed does not depend on the tree shape. For every node with children and `explicit[id] === undefined` with triggers, add to `auto`. Never un-collapse. Add `scopeKey` to `seededScopes`. Scopes load on navigation, so each scope gets its own initial pass when it first settles: a persisted collapse in a scope visited after a reload is not undone by the sessions that scope loads (§9). Only later pages and fetches count as new.
  - **Later passes**, per node with children, with `fresh` = the node's `triggerIds` not in `seen`: if `explicit[id] === undefined` and triggers exist → `auto.add(id)` (sticky); if `explicit[id] === false` and `fresh` is not empty → delete `explicit[id]` and `auto.add(id)`. After **all** nodes are evaluated, `seen` gains every node's `triggerIds` (so a descendant shared by several ancestors counts for each of them in the same pass).
  - Consequence: a late-arriving ancestor whose active child was already seen at load stays collapsed; a session first loaded after the initial pass (a later page) that already meets a condition is new and expands the collapsed ancestors.

- [ ] **Step 1: Write the failing tests** (build trees with `buildSessionTree`; `processStates`/`pinned` fixtures; helper `seed = () => ['w']` style):
  - `a node with an active child auto-expands at first render`
  - `auto-expansion is sticky after the child stops`
  - `a node whose subtree has no trigger starts collapsed`
  - `user collapse is kept when the same child changes state` (active → unread: `isExpanded` stays false)
  - `a different descendant that becomes active re-expands a user-collapsed node`
  - `after a reload a persisted collapse with an already-active child stays collapsed` — `createExpansionState({ root: false })`, `reconcile(..., { settled: true, scopeKey: 'A', seedTriggerIds })` once, assert collapsed — Review Focus 3
  - `a session first loaded after the initial pass (a later page) that is already pinned or active re-expands a persisted collapse` (decision: it is a different descendant, §9)
  - `an ancestor fetched after the initial pass keeps its persisted collapse when its active child was already loaded at the initial pass` (the common orchestration reload)
  - `a persisted collapse in another scope survives the first view of that scope` — scope A is seeded first; scope B's sessions load only afterwards (they are absent from A's seed); the first settled call for scope B seeds again and the collapsed node with an active child stays collapsed
  - `reconcile with settled false changes nothing, and a persisted collapse survives an active child that appears only after the first unsettled call` — reconcile with `settled: false` on a tree without process states, then `settled: true` on the tree with the active child: still collapsed
  - `a user expand is kept until the user collapses`
  - `revealPath expands every collapsed ancestor, also over a persisted collapse; a later user collapse sticks; the reveal never changes explicit` — Review Focus 4
  - `revealPath with the previous path reveals only the new ancestor: an ancestor the user collapsed after the first reveal stays collapsed when a higher ancestor arrives`
  - `revealPath on a tree where the node was later re-parented under a collapsed parent expands the new ancestor`, and `a 3-level chain loaded top-down: calling revealPath after each arrival ends with parent and grandparent both expanded`
- [ ] **Step 2: Run** `cd frontend && node --test src/utils/sessionTreeExpansion.test.js`. Expected: FAIL.
- [ ] **Step 3: Implement** the pure module (explicit `.js` imports, no Vue import), then the store as a thin wrapper: `state` in a `ref`/`reactive`, a deep watcher writing `explicit` to localStorage, load on creation.
- [ ] **Step 4: Run** the same command. Expected: PASS.

---

### Task 6: Candidate extension in `computeSidebarSessionBlocks`

**Files:**
- Modify: `frontend/src/utils/sessionTree.js`, `frontend/src/utils/sidebarSessions.js`
- Test: `frontend/src/utils/sessionTree.test.js`, `frontend/src/utils/sidebarSessionsStability.test.js`

**Interfaces:**
- Produces:
  - `extendTreeCandidates(natural: Session[], opts: { sessionsById: Object<string, Session>, isInScope: (s) => boolean, passesFilter: (s) => boolean }): Session[]` — loaded sessions **not** in `natural`, in scope, passing the archive filter, not hidden / sub-agent / ephemeral / draft, and linked to a **participant** member of `natural` (not ephemeral, not draft: only participants start a walk) by a `spawned_by` chain (ancestor or descendant). The chain may pass through any loaded session, **except** that the up and down walks stop at a `hidden` or `parent_session_id` session, and at ephemeral or draft sessions, exactly like `buildSessionTree` (§5.2, §5.6, §12: a hidden link cuts the chain; the extension must never pull in a session the builder would then orphan).
  - `computeSidebarSessionBlocks({ ..., showAsTree = false }, previous)`: when `showAsTree`, `natural` becomes `[...natural, ...extension]` sorted with `sessionSortComparator(data.processStates)`, and the extension ids join `naturalIds`, so they appear in no cross-filter block and not in `extra` (§2).
- Consumes: `isWorkspaceProjectId`, `extractWorkspaceId`, `data.getProjectScopeIds` already used in `computeSidebarSessionBlocks`. Build one `isInScope(session)` predicate from the same three scope cases (workspace visible ids, all projects, project + worktree ids).

- [ ] **Step 1: Write the failing tests.** In `sessionTree.test.js`: `extension adds an old in-scope ancestor`, `extension adds an old in-scope descendant`, `extension skips out-of-scope, hidden, sub-agent and draft sessions`, `extension links through an out-of-scope middle session`, `extension does not cross a hidden middle session`, `extension does not cross a draft or ephemeral middle session`. In `sidebarSessionsStability.test.js` (extend the harness: inject `extendTreeCandidates` into the `new Function` list): `with showAsTree an in-scope pinned ancestor beyond the pagination window is in natural and not in crossFilterPinned`, and `without showAsTree the same data leaves it in crossFilterPinned` (flat mode unchanged).
- [ ] **Step 2: Run** `cd frontend && node --test src/utils/sessionTree.test.js src/utils/sidebarSessionsStability.test.js`. Expected: FAIL.
- [ ] **Step 3: Implement** `extendTreeCandidates` in `sessionTree.js` (children index over all loaded sessions by `spawned_by`; walk up and down from each member, visited-set guarded). Wire the flag in `sidebarSessions.js`; add `sessionTree.js` to its imports (extensionless, like the others) and document the new option in the JSDoc.
- [ ] **Step 4: Run** the same command. Expected: PASS, and the existing stability tests still pass.

---

### Task 7: Ancestor fetch

**Files:**
- Modify: `frontend/src/utils/sessionTree.js`
- Create: `frontend/src/composables/useSessionTreeAncestors.js`
- Test: `frontend/src/utils/sessionTree.test.js`

**Interfaces:**
- Produces:
  - `findMissingAncestorIds(candidates: Session[], lookup, skipIds: Set<string>): string[]` — walks `spawned_by` upward from every non-ephemeral, non-draft candidate; collects ids for which `lookup` returns null and that are not in `skipIds`; stops at hidden, sub-agent, ephemeral and draft sessions (same stops as `buildSessionTree`); cycle-safe.
  - `useSessionTreeAncestors(candidates: Ref<Session[]>, enabled: Ref<boolean>): void` — a `watch` on a computed `findMissingAncestorIds(...)`. For each id: skip if in flight; call `useDataStore().loadSessionById(id)`. A `null` result (404) adds the id to a `notFound` set (final, no hint). A thrown error only clears the in-flight mark. The composable calls `watch([missingKey, candidateCount], run, { immediate: true })` in multi-source form (elementwise comparison, so unrelated store updates do not re-trigger it), where `missingKey = missingIds.join(',')`, `candidateCount = candidates.length`, and `missingIds` is `[]` while `enabled` is false (turning the option on changes the key and fetches): a change of the missing-id list or of the candidate count (a page or a session loaded) retries; nothing else does (no timer, no loop).
- Consumes: `store.loadSessionById` (`frontend/src/stores/data.js`, returns `null` on 404 and throws otherwise).

- [ ] **Step 1: Write the failing tests** for `findMissingAncestorIds`: `returns the unloaded parent id`, `follows loaded out-of-scope ancestors upward until an unloaded one`, `skips ids in skipIds`, `stops at a hidden, ephemeral or draft loaded ancestor`, `terminates on a cycle`.
- [ ] **Step 2: Run** `cd frontend && node --test src/utils/sessionTree.test.js`. Expected: FAIL.
- [ ] **Step 3: Implement** `findMissingAncestorIds`, then the composable. The composable is thin and not unit-tested; Review Focus 5 is its behavior (404 final, error retried when the id list or the candidate count changes) and is checked in the manual pass of Task 10.
- [ ] **Step 4: Run** the same command. Expected: PASS.

---

### Task 8: `SessionList.vue` integration

**Files:**
- Modify: `frontend/src/components/session/list/SessionList.vue`, `frontend/src/stores/data.js` (new state flag `activeProcessesReceived`, set to `true` at the end of `setActiveProcesses`), `frontend/src/components/session/list/SessionList.loading.test.js` (its compiled-SFC harness mocks every import), `frontend/src/styles/list-exit.test.js` (test 2 pins the literal line `isEligible: (s) => !!store.sessions[s.id]?.archived,`; relax it to the new `isEligible` that adds the tree-node exclusion)
- Test: `frontend/src/components/session/list/SessionList.tree.test.js` (source checks, same style as `SessionList.loading.test.js`)

**Interfaces:**
- Consumes: Tasks 2-7 exports; `useSessionTreeExpansionStore`.
- Produces: new prop `showAsTree: { type: Boolean, default: false }`; passes `treeRow` to `SessionListItem` (Task 10 defines the shape) and handles its `toggle-tree` event.
- Behavior, all only when `showAsTree`:
  - `sessionBlocks` receives `showAsTree`.
  - `treeActive = props.showAsTree && !props.searchQuery.trim()`. With a query the list is flat and every existing path runs unchanged: results are classified and sorted by each session's **own** state (§10).
  - `tree = computed(() => treeActive ? buildSessionTree(natural, { lookup: id => store.sessions[id] ?? null, processStates: store.processStates, viewedId: props.sessionId, isUnread: isSessionUnread }) : null)` (`isSessionUnread` from `utils/sessions`).
  - `tree` reads every candidate's `mtime` (for `dateKey` and the summary date), so it is recomputed on each write of an active session. That is accepted: the order does not change (§8), and the row-meta retention below limits the re-renders. Do not add a second cache layer.
  - `watch([tree, settled], ([t, ok]) => t && expansion.reconcile(t, ok, props.projectId, seedTriggerIds), { immediate: true })` where `settled` is `false` while `!treeActive` without reading any store field (the watcher, `tree` and the reveal watchers are inert in flat mode), else `store.localState.projects[props.projectId]?.sessionsFetched === true && store.activeProcessesReceived`, and `seedTriggerIds = () => Object.values(store.sessions).filter(s => store.processStates[s.id] != null || s.pinned || isSessionUnread(s, store.processStates[s.id])).map(s => s.id)` (called at the initial pass of each scope) (reuse the existing `sessionsFetched` getter family in `data.js` rather than reading `localState` inline if one fits). Call `useSessionTreeAncestors(natural-ref, treeActive-ref)`.
  - `allSessions` = `[extra, ...crossFilterPinned, ...crossFilterActive, ...flattenTree(tree, expansion.isExpanded).map(row => store.sessions[row.id])]`. `treeRowMeta`: a `computed(previous => ...)` `Map<id, { depth, hasChildren, expanded, summary, hint }>` from the rows, tree and expansion state (`hint` = `hintParent` title, only for roots). It reuses the previous meta object for an id when its fields (summary included) are shallow-equal, so an mtime tick of an active session does not re-render every row (same idea as `retainSessionArray`).
  - `separatorBeforeIds`: for natural sessions, classify **roots only** from `effective` (`pinned` → Pinned, `active` → Active, else the date bucket from `dateKey`); ephemeral and draft keep today's rules. Child rows carry no classification (the existing `if (!c) continue` already skips them).
  - Switcher: replace the `watch(sessions, …)` publish by a `watch` (with `immediate`) on a computed `switcherIds` derived from `sessionBlocks`, `tree` and `sessions`, so a descendant loading under a collapsed node refreshes it. `switcherIds` is `sessions` ids without a tree; with a tree it is the `extra` and cross-filter ids plus `flattenTree(tree, () => true)` ids, so descendants under collapsed nodes are offered (§10).
  - Selection prune keeps using the visible `sessions`, so collapsing drops hidden descendants from the multi-selection (§10).
  - Reveal: a pre-flush watcher on `[props.sessionId, tree]` calls `expansion.reveal(tree, props.sessionId)` when the selected node exists in the tree (also at first render), before `scrollToSession` needs the row. The "already revealed" mark is a **single last-revealed value** `(sessionId, ancestor path)` (the selected node's ancestor ids in order), overwritten on each selection change (so re-selecting a session reveals again). For an unchanged `sessionId` with a changed path the watcher passes the stored path to `expansion.reveal(tree, id, previousPath)`, so only the new ancestors are revealed. It is keyed on the whole path, not on `sessionId` or the direct parent alone: a deep-linked child first renders as a root (§11), then ancestors arrive one fetch at a time (parent, then grandparent, …) and each arrival changes the path and must reveal again (§10). When the reveal acts on a changed path the scroll is re-run for the still-selected session, since `scrollToSession` gives up after 5 retries and the row has moved. The re-run lives in a **separate `flush: 'post'` watcher** keyed on the same ancestor-path mark and calling `cascade.holdTarget(id, scrollToSession(id))`: the scroller's `scrollToKey` searches its `items` prop, which only updates after the render, so a pre-flush call would silently do nothing (the existing scroll watcher is `flush: 'post'` for the same reason). `expansion.reveal` itself stays pre-flush.
  - `useListExit`: `scopeKey` includes `props.showAsTree`, and while the tree is active `isEligible` also requires `!tree.value.nodes.has(s.id)`. A row that merely leaves the list because its parent collapsed is still a tree node and must not play the archive-exit animation (§8: collapse needs no animation).
  - Handler `onToggleTree(session)` → `expansion.toggle(session.id, tree.value)`.

- [ ] **Step 1: Write the failing test** (source assertions): prop `showAsTree`, `computeSidebarSessionBlocks` call includes `showAsTree: props.showAsTree`, imports of `buildSessionTree`, `flattenTree` and `useSessionTreeExpansionStore`, the template binds `:tree-row=` and `@toggle-tree=`, the switcher publish uses `flattenTree(` with `() => true`, the reveal mark is keyed on the ancestor path (the reveal watcher's key is built from the selected node's ancestor ids), `SessionList.vue` reads `activeProcessesReceived` while `data.js` declares and sets it, and a second watcher with `flush: 'post'` calls `scrollToSession` again.
- [ ] **Step 2: Run** `cd frontend && node --test src/components/session/list/SessionList.tree.test.js`. Expected: FAIL.
- [ ] **Step 3: Implement** the behaviors above. Keep every flat-mode branch byte-for-byte equivalent in behavior (the anti-goal in §4).
- [ ] **Step 4: Extend the mock map** in `SessionList.loading.test.js` (`frontend/tests/helpers/scrollerComponentHarness.js` throws `Missing dependency <path>` for any unmapped import): add inert stubs for `utils/sessionTree.js` (`buildSessionTree`, `flattenTree`), `utils/sessions` (`isSessionUnread`), `stores/sessionTreeExpansion` and `composables/useSessionTreeAncestors`. `showAsTree` is false there, so the stubs are never exercised.
- [ ] **Step 5: Run** the tree test plus `cd frontend && node --test src/components/session/list/SessionList.loading.test.js src/utils/sidebarSessionsStability.test.js src/styles/list-exit.test.js src/styles/glide.test.js`. Expected: PASS. `glide.test.js` requires `sources: [..., sessions, ...]` in `SessionList.vue`: keep the computed named `sessions`.

---

### Task 9: The menu option and prop plumbing

**Files:**
- Modify: `frontend/src/components/session/SessionsSidebarControls.vue`, `frontend/src/views/ProjectView.vue`
- Test: `frontend/src/components/session/SessionsSidebarControls.tree.test.js` (source checks)

**Interfaces:**
- Consumes: `settingsStore.isShowSessionsAsTree`, `setShowSessionsAsTree` (Task 1); `SessionList` prop `showAsTree` (Task 8).
- Produces: a checkbox item `value="show-as-tree"`, label "Show as tree", placed right after "Compact view" in the dropdown; prop `showAsTree` on the controls component.

- [ ] **Step 1: Write the failing test** (source checks): controls declare prop `showAsTree`, contain `value="show-as-tree"` with `:checked="showAsTree"`; `ProjectView.vue` contains `value === 'show-as-tree'` calling `settingsStore.setShowSessionsAsTree(item.checked)`, a computed `showAsTree` from `settingsStore.isShowSessionsAsTree`, and passes `:show-as-tree="showAsTree"` to both the controls (near line 1976) and every `SessionList` usage (near line 2033).
- [ ] **Step 2: Run** `cd frontend && node --test src/components/session/SessionsSidebarControls.tree.test.js`. Expected: FAIL.
- [ ] **Step 3: Implement** in `handleSessionOptionsSelect` (near line 892) and the templates, following the `compact-view` pattern. Pass `showAsTree` to the `SessionList` instance that `ProjectView` renders in each layout.
- [ ] **Step 4: Run** the same command. Expected: PASS.

---

### Task 10: Row visuals

**Files:**
- Modify: `frontend/src/components/session/list/SessionListItem.vue`
- Test: `frontend/src/components/session/list/SessionListItem.tree.test.js` (source checks, same style as `SessionListItem.rail.test.js`)

**Interfaces:**
- Consumes: `TREE_MAX_INDENT_LEVEL` (Task 4); the `treeRow` value built in Task 8.
- Produces: optional prop `treeRow: { depth: number, hasChildren: boolean, expanded: boolean, summary: ?{ descendants, active, unread, pinned, latestDate }, hint: ?string }` (default `null`; `null` renders exactly as today); emit `toggle-tree`.
- Visual rules (§9): indentation is `min(depth, TREE_MAX_INDENT_LEVEL)` steps, carried by the **wrapper** (`.session-item-wrapper`) as a CSS variable `--tree-indent-level` used for its `padding-inline-start`, never by the button, so the ink keeps measuring the unshifted button box; a toggle button for rows with children, **outside** the row's `wa-button` (no nested buttons) and not selecting the session on click; a collapsed row shows the summary: descendant count and the existing process, unread and pin indicators for active, unread and pinned descendants, plus `latestDate` (formatted with the row's own date formatter) only when non-null; a root with `hint` shows "↰ from <hint>". The row's own date stays the node's own date. Compact view applies to nested rows. Do not break the gliding ink: it measures the open row's button base box (`sidebar-rows.css`, `useGlideInk`).

- [ ] **Step 1: Write the failing test** (source checks): prop `treeRow`, emit `'toggle-tree'`, import of `TREE_MAX_INDENT_LEVEL`, the toggle element exists outside the `wa-button.session-item`, the wrapper binds `--tree-indent-level`, and `treeRow === null` leaves `class` bindings unchanged (assert the wrapper `:class` object still contains the existing keys).
- [ ] **Step 2: Run** `cd frontend && node --test src/components/session/list/SessionListItem.tree.test.js`. Expected: FAIL.
- [ ] **Step 3: Implement** the markup and scoped CSS. Native `<button>` elements get a forced height from WA's `native.css`: reset `height` and `display` on the toggle. Reuse existing indicator components and tooltip patterns from the row.
- [ ] **Step 4: Run** the test above plus `cd frontend && node --test src/components/session/list/SessionListItem.rail.test.js src/styles/sidebar-rows.test.js`. Expected: PASS.

---

### Task 11: Full verification

- [ ] **Step 1: Run the whole frontend suite:** `cd frontend && npm test`. Expected: all pass, no new failures versus a run before Task 1.
- [ ] **Step 2: Manual pass in the running dev UI** (do not restart servers; the Vite dev server hot-reloads `frontend/src/`). The owner does the browser pass in Firefox. Checklist, each item from the spec:
  - Option off: sidebar identical to before (compare with `main`).
  - Option on with an orchestration tree: an idle root with an active child sits in **Active**, expanded; collapsing it keeps it collapsed when the child writes.
  - A root with a pinned descendant sits in **Pinned**; an old root with a day-old child sits in **Last 24 hours** and shows the subtree date when collapsed.
  - Project scope and workspace scope: a child outside the scope becomes a root with the "↰ from" hint.
  - Reload with a collapsed node whose child is active: stays collapsed.
  - Deep link to a session under a collapsed node: path expands, row visible.
  - Search: flat list, results classified by each session's own state.
  - Switcher (Ctrl+Shift+`): lists sessions under collapsed nodes.
  - Multi-select: collapsing a node drops its hidden descendants from the selection.
  - Ancestor older than the loaded pages: the child nests once the ancestor is fetched; a 404 ancestor leaves a plain root without hint.
- [ ] **Step 3: Report** the test output and the manual results to the owner. Do not commit.
