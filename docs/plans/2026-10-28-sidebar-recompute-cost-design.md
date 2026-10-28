# Sidebar Recompute Cost — Design

**Status:** reviewed by independent adversarial subagents (8 rounds); the last round had no Critical or Major finding, and its two Minor findings are fixed. Written with the user 2026-10-28.
**Date:** 2026-10-28
**Scope:** frontend only. The reactive derivations that a live session update wakes up: the sidebar session lists (`utils/sessionLists.js`, `utils/sidebarSessions.js`, `components/session/list/SessionList.vue`), the unread index (`utils/projectProcessActivity.js`) and the workspace project membership (`stores/workspaces.js`). No visible behavior change. No backend change.
**Baseline:** `main` at `5cf064fb4`. The measurements of sections 1 and 10 were taken at `0275df56e`; the commits in between add the unread background counts to the unread index (5.5) and do not change the scan structure measured here.

---

## 1. Why

Users report that the browser tab gets slow, and they suspect the session list in the sidebar. The DOM is not the cause: the `VirtualScroller` renders only the visible rows. The cause is the reactive work done on **every live update** of a session.

For each batch of JSONL lines, the watcher sends a `session_updated` and then a `project_updated` (`src/twicc/providers/sessions_watcher.py`, `sync_and_broadcast`). For a subagent batch it also sends the parent's `session_updated`. Several derivations then scan **every session in the store**, subagents included, although nothing in the list moves.

Measured cost of one batch (one `session_updated` flush, then one `project_updated` flush). Method: Node 22, Vue 3.5.43, the real list modules, computeds subscribed by an `effect` like a component, median of 5 runs. "Store" counts every entry of `state.sessions`; each root session has 2 subagents. The scenarios use `showActiveAcrossFilters: true` (the default is `false`; with it off, `crossFilterActive` is not computed and the cost is lower, but the other defects remain).

| Scenario | Store 3 000 | Store 9 000 |
|---|---|---|
| Project scope, active session | 25 ms | 83 ms |
| Project scope, session without a TwiCC process that moves to the top | 51 ms | 180 ms |
| Project scope, session without a process in "Active elsewhere" | 45 ms | 157 ms |
| All projects, active session | 22 ms | 81 ms |
| Workspace scope, session without a process outside the workspace | 56 ms | 196 ms |
| Tree mode, project scope, active session | 31 ms | 118 ms |

The store grows during the life of a tab: every page loaded by scrolling, every subagent seen through `session_updated` (the WebSocket handler calls `addSession` when the project's sessions are fetched), every session hydrated from a `process_state`. Entries leave only on `unloadProject` (reconciliation), `removeSession`, draft discard and orphan-draft cleanup, and ephemeral rekeying. The user's own database holds about 3 750 visible root sessions and 10 900 subagents (up to 413 for one session), so a long-lived tab can hold thousands of entries.

A prototype of this design brings these batches down by one to two orders of magnitude (section 10).

## 2. What already works (keep it)

- `updateSession` patches field by field with `jsonValuesEqual` and keeps the session object. Only the readers of a changed field wake up.
- `getStableSessionList` separates membership (`members`) from order (`ordered`). The comparator never reads the `mtime` of a session with a process (`utils/sessionSort.js`), so an active session's tick does **not** sort again.
- `retainSessionArray` and the `previous`-retaining computeds stop propagation when a result is equal.
- `isSessionUnread` returns before it reads timestamps while the process is outside `user_turn` (`utils/sessions.js`).
- `_patchProcessState` keeps the process-state object.
- Streaming deltas (`stream_block_*`) touch only `localState.streamingBlocks`. They never reach the sidebar.
- During a startup phase, the list getters read fields without tracking (commit `271e462a8`). This prevents an O(n²) freeze while the background compute floods `session_updated`.

## 3. The defects

Each defect is a **dependency the derivation does not need for its result**, in the same computed as a full scan of a large reactive map. A full scan costs about 1–2 µs per entry per read set: `Object.values` on a reactive object tracks every key, then each field read is tracked too.

### 3.1 `members` rescans the whole store on an `mtime` tick

`utils/sessionLists.js`, `members` computed: it calls `Object.keys(state.sessions)` and `Object.values(state.sessions)`, then filters on `project_id`, `parent_session_id`, `hidden` and, while `hasMoreSessions` is true, `session.mtime >= oldestSessionMtime`. Because the `mtime` of every in-scope root is read in the same computed as the full scan, one `mtime` change reruns the full scan (all sessions, subagents included). The result is retained, so nothing sorts again, but the scan itself is the cost. This applies to every scope that has more pages to load, which is the normal case.

### 3.2 Multi-project scopes go through the all-projects list

A workspace scope and a main repository with worktrees (`getProjectScopeIds` returns more than one id) build their natural list as `getAllSessions.filter(...)` (`utils/sidebarSessions.js`, natural scope; `SessionList.vue`, `canonicalSessions`). So in these scopes:

- a tick of an active session reads the `mtime` of every loaded root (the all-projects cutoff stage);
- a tick of **any** session without a process, anywhere in the store, re-sorts every loaded root, then filters the result again.

The pagination cutoff that applies to these scopes is the all-projects one (`localState.projects.__all__`), not the scope's own cursor. This design keeps that rule unchanged (section 5.2).

### 3.3 The archived-project filter depends on project activity

`utils/sidebarSessions.js`, `makeArchiveFilter`: with `showArchivedProjects` off (the default), it reads `data.getProjects`, which is sorted by activity (`stores/data.js`: `getProjects` → `projectActivityComparator` → `projectActivityById` → every project's `mtime`). Every `project_updated` creates a new sorted array, so `computeSidebarSessionBlocks` reruns in full: the natural filter, two `Object.values(data.sessions)` scans (`crossFilterPinned`, `crossFilterActive`), the `Set`s and the sorts.

### 3.4 Workspace membership depends on project activity

`stores/workspaces.js`, `getVisibleProjectIds` (and `getAllProjectIds`) walks `dataStore.getWorktreesOf(pid)`, which sorts worktrees by activity. Any caller therefore depends on every project's `mtime`. In workspace scope this causes, on every `project_updated`:

- a full rerun of `computeSidebarSessionBlocks` (it calls `getVisibleProjectIds` for the natural scope);
- a new `scopeProjectIds` array in `SessionList.vue`. That array is a prop of **every rendered `SessionListItem`**, so every visible row re-renders;
- a re-evaluation of the `scopeProjectIds.value?.join('|')` watcher. When two worktrees swap activity order, the joined string changes and `refreshPaginationScope` runs for nothing.

This defect is derived from the code, not measured.

### 3.5 Membership and order are mixed in `computeSidebarSessionBlocks`

- It sorts `crossFilterPinned`, `crossFilterActive` and, in tree mode with a non-empty extension (an ancestor beyond the pagination window), the merged natural list, all with `sessionSortComparator`. For two sessions without a process, the comparator reads `mtime`.
- It reads the **ordered** natural list, although its cross-filter and extension logic needs only the natural **id set**.

So an `mtime` tick of a session without a process reruns the whole function, with its full scans, whenever such a block holds two sessions without a process, or the natural order changes. In tree mode the rerun includes `extendTreeCandidates`, which walks `Object.values(sessionsById)` over the whole store.

### 3.6 Unread scans follow every timestamp change

- `utils/projectProcessActivity.js`, `unreadCounts`: it scans `Object.values(store.sessions)` and calls `isSessionUnread` for each entry. For any session outside `assistant_turn` (no process, `user_turn`), a `last_new_content_at` or `last_viewed_at` change reruns the full scan, even when the unread result does not change. The index is always subscribed: `useFavicon.js` reads `getGlobalUnreadCount`, and every `AggregatedProcessIndicator` reads `getProjectActivitySummary`.
- `utils/sidebarSessions.js`, `crossFilterActive`: it calls `hasUnreadContent(s)` on every out-of-scope root without a process. A timestamp change on such a session reruns the whole `computeSidebarSessionBlocks`.

Affected cases: sessions run outside TwiCC (a CLI in a terminal), sessions in `user_turn` that receive metadata, "mark as read".

### 3.7 Followers that stay (not addressed, see section 4)

- **Tree mode rebuild.** `buildSessionTree` reads every candidate's `mtime` (`dateKey`), so the tree rebuilds on each tick of an active session. Its followers rerun with it: `treeRowMeta` (with `treeActivity` for collapsed rows), `separatorBeforeIds` and `expansion.reconcile` (`SessionList.vue`). After the fixes of this design, the measured tree part (`buildSessionTree` plus `flattenTree`) costs about 1 ms at store 3 000 and 3–5 ms at store 9 000. The followers are not measured.
- **Tree mode switcher list and ancestors.** In tree mode, `switcherIds` (`SessionList.vue`) runs `flattenTree` again on each tree rebuild and returns a new array, so `setDisplayedSessionIds` compares the ids (O(n)) and the reveal watcher reruns. Each new `natural` array also reruns `useSessionTreeAncestors` (`findMissingAncestorIds` over natural).
- **Natural reorder followers.** When the natural order changes (flat mode included), these rerun over the displayed list (O(natural)): `allSessions`, `sessions`, the `JSON.stringify` watcher of the displayed ids, `switcherIds` with the `setDisplayedSessionIds` compare, and the selection prune when selection mode is on (`SessionList.vue`). A reorder is a visible change, so this work is legitimate.
- **Date-bucket separators.** `separatorBeforeIds` (`SessionList.vue`) reads the `mtime` of every natural member without a process and returns a new `Map` on each run. A tick of an in-scope session without a process reruns it (O(natural)) and re-renders `SessionList`; the rows do not re-render because their props stay equal.

## 4. Goals, non-goals, anti-goals

**Goals.**

- A `session_updated` or `project_updated` that changes no list membership, no order and no unread result triggers **no scan over `state.sessions`** and no re-render of rows other than the updated session's.
- The remaining per-update work is bounded by what the update can change:
  - a tick of an in-scope session: one `mtime` read per root of the scope (the pagination cutoff stage, section 5.2); in all-projects scope that is every root;
  - a tick of a session without a process: a re-sort of the scope list or the cross-filter block that contains it (O(k log k) for k members), plus the date-bucket separators (3.7); in all-projects scope that is every root (structural floor); when the order changes, the natural reorder followers (3.7);
  - a tick of a session outside the scope: nothing in the scope list;
  - an unread result that flips: `unread` reruns `unreadCounts` (one pass over the roots, one memo read each); `unreadContent` reruns the block membership stage (its passes over the roots, plus `extendTreeCandidates` in tree mode);
  - a subagent added or removed: one pass over the store (`rootSessions`, result retained); outside this design, the rail rebuilds its `sessionOrder` map and, in rail tree mode, its `childrenIndex` (`useRailActiveSessions.js`, `buildChildrenIndex`) over the store keys, as today;
  - a root session added or removed: the `rootSessions` pass, one `candidates` pass over the roots per subscribed scope, the block membership pass and the `unreadCounts` pass.
- No visible behavior change: same lists, same order, same sections, same badges and favicon, same pagination, same command palette results. Two accepted exceptions:
  - in tree mode, extension sessions whose sort keys are equal may come out in a different relative order (5.4);
  - during a startup phase, a change the lists do not track may show up earlier or later than today, within the guarantee of 5.3. After each progress message the lists match the data (today they can lag longer); at the end of startup they are the same as today.

**Non-goals.**

- The followers of 3.7 (tree mode rebuild, tree mode switcher list and ancestors, natural reorder followers, date-bucket separators).
- The mount cost of a row (each `SessionListItem` renders a full `wa-dropdown` with about 28 `wa-dropdown-item`). It costs on scroll, not on updates. Possible follow-up.
- Pruning `state.sessions`.
- Backend changes (fewer or throttled `session_updated` / `project_updated`).
- `updateProject` replacing nested project objects (`default_agent_settings`, `browser_urls`) on every `project_updated`. No sidebar derivation reads them.
- The command palette's own full scans (`buildProjectActivityMap` in `commands/staticCommands.js`). They run only while the palette is open.
- Changing which pagination cursor a multi-project scope uses (3.2).

**Anti-goals.**

- A list that shows a stale membership or order (for example a session older than the pagination cutoff that gets new activity and does not appear).
- An unread badge, favicon dot, "Active elsewhere" or "Pinned elsewhere" entry that lags behind the data.
- A startup that freezes again because list derivations track fields during the startup flood.
- Imperative caches kept in sync by hand from `updateSession` or WebSocket handlers. The fix stays inside reactive derivations, so no code path can forget to update a cache.

## 5. Design

The rule for every derivation in scope: **split it into a membership stage that reads only fields that rarely change, and narrow stages that read the frequently changing fields only on the already narrowed list or through a per-session memo.** Order is its own stage. Each stage retains its previous result when equal.

### 5.1 Root-session index (new, shared)

One lazily created computed per data store, like `getProjectActivityIndex` (a `WeakMap` keyed by the store or its state):

- `rootSessions`: the session records with no `parent_session_id`, in `Object.values(state.sessions)` order. That order must be kept: the stable sort relies on insertion order for ties, and the rail relies on it too (`utils/railActiveSessions.js`).
- It always yields the **reactive** records, also during startup.
- Reads per entry: `parent_session_id` only. During startup it reads that field without tracking, like the list getters today.
- Retains the previous array when the members are the same.

Cost: one pass over the store per membership change (a session or subagent added or removed). Outside startup, nothing else reruns it. During startup it may also rerun on each progress message (5.3).

### 5.2 Scope lists (`utils/sessionLists.js`)

One stable list per **scope**. A scope is identified by its project-id set **and** its cursor entry (below), never by the set alone: a workspace with a single visible project and the single-project scope of that project have the same set but different cursors, so they are two lists. The scope kinds are: a single project; a multi-project scope, which is any workspace (whatever its member count) or a main repository whose `getProjectScopeIds` holds more than one id; all projects. No project id (a falsy `effectiveProjectId`, as in the command palette on routes without a project) means all projects: the all-projects list and cursor, as today. Each list has three stages, each retained when equal:

1. `candidates`: `rootSessions` filtered on the scope's project-id set (no filter for all projects) and `!session.hidden`. Reads `project_id` and `hidden` of roots.
2. `members`: when the scope's cutoff applies (`hasMoreSessions` and `oldestSessionMtime` of its cursor entry), `candidates` filtered on `session.mtime >= oldestSessionMtime`; otherwise `candidates` itself. Reads one field (`mtime`) per candidate. The cursor entry is today's: the project's own entry for a single project, the all-projects entry (`__all__`) for a multi-project scope and for all projects (3.2).
3. `ordered`: `members` sorted with `sessionSortComparator`, as today.

`members` (insertion order, the membership) and `ordered` are both available to consumers. `getProjectSessions` and `getAllSessions` keep their results. The multi-project callers use the list of their scope instead of filtering `getAllSessions`. `SessionList.vue` `canonicalSessions` feeds only the pagination snapshot, which needs ids and not order, so it reads the scope's `members`, not `ordered`.

### 5.3 Startup phase

During a startup phase (`hasActiveStartupPhase(state.startupProgress)`), the background compute floods `session_updated`. Commit `271e462a8` stops the list getters from tracking session fields during that flood, to avoid an O(n²) freeze. As a side effect, a change the lists do not track today (an archive, a hide, a cutoff crossing, an order change) shows up only when some unrelated event happens to rerun them. This design keeps the protection and replaces those incidental refresh events with a guarantee:

- **No tracking.** During startup, the stages of 5.2, every part of the block membership stage that reads natural records (the archive filter, the tree extension), and, in tree mode with a non-empty extension, the natural order stage (it sorts raw records), read session fields **without tracking**. Their outputs hold raw records during startup, as today, and reactive records after it.
- **Refresh guarantee.** Every stage that reads session fields without tracking reruns on each progress message (`setStartupProgress` replaces the `startupProgress` object on each message; these stages read the phase from that object on each run, not from a boolean that changes only when the phase flips). So an untracked change shows up **at the latest at the next progress message**, and always when startup ends. Normal tracking comes back when startup ends (existing test "startup tracking resumes after metadata computation completes").
- **Not required.** The stages do not have to rerun on the other events that rerun them today by accident (a store key change, a project activity change). A change may therefore show up earlier or later than today between two progress messages.
- **Kept as today.** The cross-filter blocks (5.4) and the unread index (5.5) keep the tracking they have today during startup: they iterate `rootSessions`, which yields reactive records. The unread index keeps its `isStartupInProgress` gate. New sessions still appear when they arrive: a new root changes `rootSessions`, which reruns `candidates`.
- **Memos.** A per-session memo (5.5) always reads through the reactive record, whatever reference (raw or reactive) was used to look it up. A memo created during startup must still update after startup.

### 5.4 Sidebar blocks: membership apart from order

`computeSidebarSessionBlocks` (shared by `SessionList.vue`, the command palette in `commands/staticCommands.js`, and `components/peer/PeerMessageReviewDialog.vue`) becomes two pure stages. The command palette and the dialog run them once per evaluation and keep their results. For `SessionList.vue`, the reactive wiring of the stages (one retaining computed for membership, one for the natural order, one per cross-filter order) is built by a factory in a node-loadable module (section 6), so the rerun graph itself is the shipped code that the tests and the benchmark exercise.

1. **Membership:** which sessions go in `extra`, `crossFilterPinned`, `crossFilterActive` and `natural` (including the tree extension). It reads the scope list's `members` (5.2), never `ordered`, so an order change does not rerun it. The cross-filter blocks iterate `rootSessions` instead of `Object.values(data.sessions)`, so their `parent_session_id` checks become redundant; the `extra` lookup (`data.sessions[sessionId]`) keeps its guard. `crossFilterActive` reads unread content through the memo of 5.5. In tree mode, `extendTreeCandidates` works on the roots only (subagents are always blocking) and walks the archive-filtered natural membership, in `members` order, instead of the ordered list. No sort.
2. **Order:** `natural` is the scope list's `ordered` restricted to the membership, with the tree extension merged in and sorted when it is non-empty; `crossFilterPinned` and `crossFilterActive` are sorted with `sessionSortComparator`. Each output keeps its previous array when equal. The natural order and the cross-filter orders are independent: a change that only reorders a cross-filter block does not rerun the natural restriction (O(natural)), and a natural reorder does not re-sort the cross-filter blocks.

Accepted difference: today the extension enters the stable sort in the order of a walk over the ordered natural list. With the walk over `members`, extension sessions whose sort keys are equal (same `mtime` without a process, or same `started_at` with one) may come out in another relative order. Only those ties are affected.

### 5.5 Unread through per-session memos

- **Two** per-session memos, created lazily and kept in a `WeakMap` keyed by the raw record, inside a per-store map:
  - `unread`: the result of `isSessionUnread(session, processStates[session.id])`. It reads timestamps only when the predicate does: without a process, or in `user_turn`.
  - `unreadContent`: the result of `hasUnreadContent(session)`, for `crossFilterActive`.
  - Each memo's value is a boolean. A timestamp change that does not flip it stops at the memo. A single combined memo is not allowed: for the open session, `last_new_content_at` advances on each batch and `last_viewed_at` follows it (throttled to once per 30 s per session by `notifySessionViewed`), so `hasUnreadContent` flips back and forth while the process is in `assistant_turn`, and a combined value would change with it although `isSessionUnread` stays false.
  - `updateSession` keeps the record object, so a memo lives as long as its record. A removed record releases its memos.
- `unreadCounts` iterates `rootSessions` instead of `Object.values(store.sessions)` and reads the `unread` memo. It keeps its current per-project shape `{ count, background, shells }`: for each session whose memo is true, it still reads `unreadBackgroundKind(processState)`, so `summary()` keeps `unreadCount`, `unreadBackgroundCount` and `unreadShellCount` (the alternating unread eye). It keeps its `isStartupInProgress` gate and its retained `Map`.
- Callers that evaluate the predicates once per row or once per action (`SessionListItem.vue`, `SessionSwitcher.vue`, `SessionSelectionBar.vue`, `railActiveSessions.js`) may keep calling the predicates directly. They do not scan the store.

### 5.6 Activity-free project inputs

- New data-store getter `archivedProjectIds`: a `Set` of the ids of archived projects. It reads `archived` only, never `mtime`, and keeps the previous `Set` when the members are the same. `makeArchiveFilter` uses it instead of `data.getProjects`.
- New activity-free worktree index: parent project id → ids of its worktrees, in project-id order. It reads `worktree_of` only. `getVisibleProjectIds` and `getAllProjectIds` (`stores/workspaces.js`) use it instead of `getWorktreesOf`.
  - `getWorktreesOf` stays as it is (activity order) for the views that display worktrees.
  - The worktree order inside the result of `getVisibleProjectIds` / `getAllProjectIds` changes from activity order to project-id order. The member order (the workspace's own `projectIds`) does not change. At the baseline, the consumers that display a list remove worktrees first (`ProjectDetailNavList.vue`, `WorkspaceCard.vue`, `ProjectView.vue`); the others use the result as a set. The builder re-checks this with `grep -rn "getVisibleProjectIds\|getAllProjectIds" frontend/src`.
  - `getVisibleProjectIds(wsId)` returns the same array instance while its content is unchanged.
- `SessionList.vue`, `scopeProjectIds`: keeps the previous array when the ids are the same, in every scope mode. Rule for this component: **every prop given to `SessionListItem` keeps its identity while its value is unchanged.**

### 5.7 What an update wakes up after the change

| Event | Reruns | Does not rerun |
|---|---|---|
| `session_updated`, in-scope session with a process in `assistant_turn` | its row; the scope's `members` stage (one read per candidate, result retained) | `rootSessions`, `candidates`, `ordered`, both block stages, memos, `unreadCounts` |
| idem, process in `user_turn` | the above plus its memos; `unreadCounts` only if `unread` flips | full scans of `state.sessions` |
| `session_updated`, in-scope session without a process | the above plus `ordered` (re-sort of the scope) and the date-bucket separators (3.7); when the order changes, the natural order stage and the natural reorder followers (3.7) | the block membership stage, the cross-filter sorts, the pagination snapshot, full scans of `state.sessions` |
| `session_updated`, session outside the scope, without a process | its memos; the sort of the cross-filter block that holds it; the block membership stage and `unreadCounts` only if a memo flips | the scope list, the natural order stage, full scans of `state.sessions` |
| `project_updated` (activity only) | readers of project activity (project lists sorted by activity, the rail's recent projects) | `archivedProjectIds`, worktree index, `getVisibleProjectIds`, both block stages, `SessionListItem` rows |
| a subagent added or removed | `rootSessions` (result retained) | everything downstream |
| a root session added or removed | `rootSessions`, `candidates` of each subscribed scope, then the stages whose input changes | — |
| a startup progress message (startup only) | the stages that read session fields without tracking (5.3): the scope stages, the natural part of the block membership stage and, in tree mode with a non-empty extension, the natural order stage | the cross-filter passes and `unreadCounts`, unless their input changes |

In tree mode, add the tree rebuild and its followers (3.7, including the switcher list) to every row that changes `natural` or reads `mtime`.

## 6. Constraints

- Vue 3.5 reactivity rules. A computed with subscribers reruns only when a dependency it read changes, and stops propagation when its new value is identical. The design relies on this.
- The logic of the root index, the scope lists, the memos, the worktree index, the archived-project set, the workspace membership (`getVisibleProjectIds` / `getAllProjectIds`), both block stages and the block-stage wiring factory of 5.4 lives in modules that load under plain `node --test` (no Pinia import, explicit `.js` imports), like `utils/projectProcessActivity.js`. The stores and `SessionList.vue` only plug them in. `utils/sidebarSessions.js` must stop importing `../stores/data` for this. Reason: the tests and the benchmark (section 7) must run the shipped logic, not a copy.
- The command palette keeps the same results.
- The project rules apply (`CLAUDE.md`): no circular static imports between stores, utils and composables; lazy `import()` if a cycle appears.

## 7. Verification

### 7.1 Deterministic tests (`node --test`)

Extend `stores/sessionListsStability.test.js` and `utils/sidebarSessionsStability.test.js`, and add tests for the unread index. Use the existing technique: a counting getter installed with `Object.defineProperty` on a field, then assert the count after an update.

Every frontend test whose fixture or harness depends on a changed shape may change its fixture or harness to supply the new inputs (root index, scope lists, memos, archived set); **every behavior assertion it holds today must stay**. At the baseline this concerns at least `stores/sessionListsStability.test.js`, `utils/sidebarSessionsStability.test.js`, `components/session/list/SessionList.loading.test.js`, `components/session/list/SessionList.tree.test.js`, `utils/projectProcessActivity` tests, `AggregatedProcessIndicator` tests and `stores/processSnapshots.test.js`; the builder finds the full list by running the whole frontend suite.

The parity oracle of item 9 is a **frozen copy of the baseline derivations** (`getStableSessionList`, `computeSidebarSessionBlocks`, `unreadCounts`, the activity-sorted project inputs they read: `getProjects`, and `getVisibleProjectIds` through `getWorktreesOf`, and every helper they call whose code or signature the design changes, `extendTreeCandidates` included, at the baseline commit), kept with the tests, not shipped. The benchmark (7.2) uses the same copy for its "before" figures, so a `project_updated` costs in "before" what it costs today.

The tests must prove, in a single-project scope and in a multi-project scope:

1. A tick of an active in-scope session reads no field of a subagent and no field of an out-of-scope session. Only the `mtime` of in-scope candidates is read, and no stage after `members` reruns.
2. A tick of a session without a process outside the scope does not rerun the scope list.
3. A project `mtime` change does not rerun either block stage, and `getVisibleProjectIds(wsId)` returns the same instance. The store facade's project getters (`getProjects`, `getWorktreesOf`) are the activity-sorted ones (the frozen baseline copies serve), so a regression that reads them again is caught.
4. A tick of a session without a process that changes the natural order, or the order of a cross-filter block with two or more members, reruns the order stage only, not the membership stage.
5. A `last_new_content_at` change that does not flip the unread result reruns neither `unreadCounts` nor the membership stage; a change that flips it updates the counts and "Active elsewhere".
6. During startup: the scope stages, the natural part of the block membership stage and, in tree mode with an extension, the natural order stage do not track session fields; their entries are raw records during startup and reactive records after it; an untracked change (archive, hide, cutoff crossing, order change, and in tree mode an order or pin change among extension sessions) shows up at the next progress message; the cross-filter blocks react to a pin, archive or unread change at once; a memo first evaluated during startup still updates after startup ends.
7. In tree mode with a non-empty extension, an `mtime` tick of a session without a process does not rerun `extendTreeCandidates`, even when the order changes.
8. A `project_updated` that changes only project activity does not re-render any `SessionListItem`: `scopeProjectIds` and every other row prop keep their identity, in project, workspace and all-projects scopes (an SFC harness such as `SessionList.loading.test.js` can mount the list). This test changes a real project `mtime` and uses the shipped workspace membership, worktree index, `archivedProjectIds` and block stages, not stubs of them; the facade's `getProjects` and `getWorktreesOf` are activity-sorted, as in item 3.
9. For a given store state and update sequence, the new derivations produce the same blocks (ids and order) and the same unread summary (`unreadCount`, `unreadBackgroundCount`, `unreadShellCount` per project) as the frozen baseline copy, in project, main repository with worktrees, workspace (including a one-project workspace next to the same project's own scope, with different cursors), all-projects and no-project-id scopes, flat and tree. During startup, no comparison is made between progress messages. After each progress message, the blocks are compared with the blocks the frozen baseline gives on the same data outside startup (the state the data implies); at the end of startup, with the frozen baseline itself (5.3).
10. Adding or removing a subagent reruns only `rootSessions`: no scope stage, no block stage and no `unreadCounts` reruns. An unread flip (either memo) reads no field of a subagent (counting getter on a subagent field), so `unreadCounts` and the cross-filter passes iterate the roots, not `state.sessions`. Tree fixtures either have no exact sort-key ties among extension sessions, or compare those ties as a set (5.4).

### 7.2 Benchmark script

Commit the benchmark as `frontend/scripts/benchmark_sidebar_recompute.mjs`, run with `cd frontend && node scripts/benchmark_sidebar_recompute.mjs`. It builds a store state with N roots and 2 subagents per root, subscribes the derivations through an `effect`, and times one batch per scenario. It imports the shipped modules (section 6) for the "after" figures and the frozen baseline copy of 7.1 for the "before" figures; the harness supplies only the state and the store facade; the computed wiring comes from the shipped factory (5.4). Both run in the same process, so the ratio does not depend on the machine. It prints the median of 5 runs per scenario, at store sizes 3 000 and 9 000.

Scenarios: those of section 10. "Active elsewhere" and "Pinned elsewhere" hold at least two members. The "order changes" scenarios alternate two sessions without a process that each take the top in turn.

The benchmark is evidence, not a pass/fail gate: absolute values depend on the machine, and sub-millisecond values vary by up to 2× between runs. Expected ratios (before ÷ after), at store 3 000 and 9 000:

- single-project and workspace flat scenarios: at least **20×**;
- all-projects scenarios, whose structural floor is one `mtime` read per root (and a re-sort of every root when the order changes): at least **10×**;
- tree scenarios: at least **10×**.

The deterministic tests of 7.1 are the gate.

### 7.3 Manual check

In the real app (Firefox), with a long-lived tab (several projects scrolled, a session with subagents open) and an active session: record a profile during a turn. No sidebar derivation appears as a long task on each `session_updated` / `project_updated`. Then check by eye: order, sections ("Pinned", "Active", date buckets, "Active elsewhere", "Pinned elsewhere"), unread eyes and badges, favicon, pagination at the bottom of the list, in project, main repository with worktrees, workspace and all-projects scopes, flat and tree, the command palette's "Go to Session…", and the reply-target session picker of the peer message review dialog (`PeerMessageReviewDialog.vue`). Also restart the backend once with a large history and check that the startup does not freeze the tab.

## 8. Left to the builder

- Module and function names, and how the stores expose the new indexes, within the constraint of section 6.
- How a scope list is keyed and cached, and how the startup rule of 5.3 reads fields without tracking.
- Whether other `getProjects.filter(p => p.archived)` callers (`PeerMessageReviewDialog.vue`) move to `archivedProjectIds`.
- Test file layout.

## 9. Decisions made without the user

- Frontend only; backend broadcasts unchanged.
- Out of scope: tree mode rebuild, date-bucket separators, row mount cost, store pruning.
- Worktree order inside `getVisibleProjectIds` / `getAllProjectIds` becomes project-id order.
- Multi-project scopes keep the all-projects pagination cutoff (today's rule).
- During startup, the protection of commit `271e462a8` stays (untracked reads, raw records in the lists), and its incidental refresh events are replaced by the guarantee of 5.3: an untracked change shows up at the latest at the next progress message (user decision, 2026-10-28).
- In tree mode, extension sessions with equal sort keys may change relative order (5.4).
- The benchmark is committed as a script; it is evidence, not a gate.
- A frozen copy of the baseline derivations is kept with the tests, as the parity oracle and the benchmark's "before".

## 10. Appendix: prototype measurements

Prototype = 5.1, 5.2, 5.4, 5.5 and the archived set of 5.6, in a scratch copy of the modules (no repo change). Same method as section 1 (median of 5 runs). The state holds two unread sessions without a process in other projects ("Active elsewhere"), two `all` pins in other projects ("Pinned elsewhere") and one in-scope ancestor beyond the pagination window (tree extension). The workspace scope holds projects `p0` and `p1`. In the prototype one of the two unread sessions sits in `p1`, so "Active elsewhere" holds one member in the workspace scenarios, two in "without process outside the workspace" (the ticked `p5` session becomes unread). The committed benchmark (7.2) places two unread sessions outside the workspace instead. For every scenario, the prototype produced the same blocks (ids and order) as the baseline.

| Scenario | Store 3 000: baseline → prototype | Store 9 000: baseline → prototype |
|---|---|---|
| Project, active | 24.5 → 0.30 ms | 82.9 → 0.52 ms |
| Project, without process, order changes | 51.2 → 0.62 ms | 179.7 → 1.72 ms |
| Project, without process, "Active elsewhere" | 45.0 → 0.15 ms | 156.6 → 0.80 ms |
| All projects, active | 22.0 → 0.36 ms | 81.0 → 3.07 ms |
| All projects, without process, order changes | 52.1 → 2.47 ms | 190.3 → 15.64 ms |
| Workspace, active | 24.8 → 0.28 ms | 94.4 → 1.33 ms |
| Workspace, without process outside the workspace | 55.5 → 0.20 ms | 196.4 → 1.06 ms |
| Workspace, without process, order changes | 51.6 → 0.71 ms | 185.7 → 3.00 ms |
| Tree, project, active, with extension | 31.1 → 0.68 ms | 118.0 → 3.30 ms |
| Tree, project, without process, order changes, with extension | 66.8 → 1.49 ms | 245.7 → 4.93 ms |

Unread index alone, store 9 000: baseline 45 ms per timestamp change; memo over all sessions 0.44 ms (no flip) / 19 ms (flip); memo over `rootSessions` 0.09 ms (no flip) / 0.73 ms (flip).

Run-to-run variation on the same machine reaches about 2× for the sub-millisecond values.
