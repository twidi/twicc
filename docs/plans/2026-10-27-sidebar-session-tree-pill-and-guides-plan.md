# Sidebar Session Tree — Pill and Guide Lines Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the tree rows' toggle column and collapsed summary with a pill (the toggle, with the aggregated activity indicator when collapsed) and file-explorer guide lines on child rows, with no space reserved on rows that have no children.

**Architecture:** The tree nodes expose their descendants; a pure helper folds them into the existing `summarizeProcessActivity` result; `flattenTree` rows carry guide data; `SessionList.vue` builds the row metadata; `SessionListItem.vue` renders the pill and the guides. Everything built by the first plan stays; only the row visuals and their data change.

**Tech Stack:** Vue 3 `<script setup>`, Web Awesome 3, `node --test` (`cd frontend && node --test <file>`).

**Spec:** `docs/plans/2026-10-27-sidebar-session-tree-spec.md` §9 (rows: pill, guide lines). Earlier plan (done): `docs/plans/2026-10-27-sidebar-session-tree-plan.md`. A mockup of the chosen option (B) was shown to the owner in the session that wrote this plan.

## Global Constraints

- A row without children shows no tree control and reserves no space: it renders exactly like a flat row. Root rows are flush left. The indent and guides exist only on child rows (§9).
- The pill is on rows that have children only. Collapsed `▸ N`, expanded `▾ N`; `N` = descendants the tree shows under the node, any depth, loaded so far. The pill is the toggle: operable by keyboard, `aria-expanded`, an accessible label, placed OUTSIDE the row's `wa-button` (no nested buttons); its click must not select the session.
- Collapsed: the pill also shows the aggregated activity indicator (the project/workspace badge cascade) when it has one; expanded: count only. One tooltip on the whole pill (count, pinned count when any, subtree date when it differs from the node's own date, activity text when any); the indicator's own tooltip is not shown separately.
- The aggregated activity covers the same set as `N`: the node's descendants in the tree. Synthetic, ephemeral and hidden sessions never count. Unread = the unread rule of the session row; the session currently viewed never counts.
- Guide lines: a vertical line from the parent and an elbow into each child; an ancestor's vertical line continues past a child while that ancestor has more children below and stops at its last child. Indentation and guide width are capped at `TREE_MAX_INDENT_LEVEL` (3) levels.
- The gliding ink measures the open row's button base box: indentation stays on the wrapper (`.session-item-wrapper`), never on the button.
- Firefox only for the owner: any prefixed CSS property goes BEFORE the standard one. All text in English. Pure modules import with explicit `.js` extensions.
- The owner asked for one commit per task, staging only that task's files (explicit `git add <files>`). No `npm install`, no server restart. Stay on `main`.

## Review Focus

1. A pill that overlaps the row's text or the row's action menu trigger (Task 4; checked in the browser by the controller in Task 5).
2. The aggregated activity must use the tree's descendants, exclude the viewed session from unread, and skip synthetic/ephemeral/hidden (Task 1 tests).
3. A child deeper than the cap keeps the third level's indent and guide width (Task 2 test, Task 4 check).
4. Row-metadata retention must include the new fields, or every active-session write re-renders every row (Task 4).
5. Two stacked tooltips (the pill's and the indicator's) (Task 3, Task 4).

---

## File Structure

| File | Change |
|---|---|
| `frontend/src/utils/sessionTree.js` | nodes get `descendantIds`; `summary` shrinks to `{ descendants, pinned, latestDate }`; new `descendantActivity`; `flattenTree` rows get `isLast` and `through` |
| `frontend/src/components/ui/ProcessActivityIndicator.vue` | new prop `tooltip` (default true) |
| `frontend/src/components/session/list/SessionList.vue` | `treeRowMeta` new shape and retention |
| `frontend/src/components/session/list/SessionListItem.vue` | pill, guides; toggle column, spacer and summary removed |
| tests | `sessionTree.test.js`, `SessionList.tree.test.js`, `SessionListItem.tree.test.js`, and the existing tests that read the changed files |

---

### Task 1: Descendants and the aggregated activity

**Files:**
- Modify: `frontend/src/utils/sessionTree.js`
- Test: `frontend/src/utils/sessionTree.test.js`

**Interfaces:**
- Produces on each `TreeNode`: `descendantIds: string[]` (the node's descendants in final display order, i.e. the depth-first order the code already builds for `ordered`); `summary: { descendants: number, pinned: number, latestDate: ?number }` (the `active` and `unread` counters are removed: nothing reads them any more).
- Produces: `descendantActivity(descendantIds: string[], opts: { processStates: Object, sessions: (id: string) => ?Session, viewedId: ?string, isUnread: (session, processState) => boolean }): ReturnType<typeof summarizeProcessActivity>` — builds the list of live process states of the descendants (skipping a missing state, `ps.synthetic`, `ps.extra?.ephemeral`, and sessions whose row is `hidden`), counts the unread ones (`id !== viewedId && isUnread(session, ps)`, ephemeral/hidden skipped), and returns `summarizeProcessActivity(states, unreadCount)` imported from `./processActivity.js`.
- Consumes: `summarizeProcessActivity` (`frontend/src/utils/processActivity.js`, pure).

- [ ] **Step 1: Write the failing tests** in `sessionTree.test.js`: `descendantIds lists descendants in display order, depth-first`; `summary is { descendants, pinned, latestDate } and no longer has active/unread` (update the existing summary assertions accordingly); `descendantActivity counts live processes of the descendants only` (a parent's own process is not counted); `descendantActivity skips synthetic, ephemeral and hidden sessions`; `descendantActivity does not count the viewed session as unread`; `descendantActivity counts unread through isUnread`; `descendantActivity with no live process and no unread yields processCount 0 and unreadCount 0`.
- [ ] **Step 2: Run** `cd frontend && node --test src/utils/sessionTree.test.js`. Expected: new tests FAIL.
- [ ] **Step 3: Implement** the `descendantIds` field, the reduced `summary`, and `descendantActivity` in `sessionTree.js` (explicit `.js` imports only).
- [ ] **Step 4: Run** the same command. Expected: PASS.

---

### Task 2: Guide data in the flattened rows

**Files:**
- Modify: `frontend/src/utils/sessionTree.js`
- Test: `frontend/src/utils/sessionTree.test.js`

**Interfaces:**
- Produces: `flattenTree(tree, isExpanded): Array<{ id: string, depth: number, isLast: boolean, through: boolean[] }>`. `isLast`: the node is the last among its parent's children (roots: `true`). `through`: for a row at depth `d`, an array of length `max(d - 1, 0)` where entry `i` is `true` when the ancestor at depth `i + 1` is NOT the last child of its parent (its vertical guide continues through this row). Roots and depth-1 rows have `through: []`.

- [ ] **Step 1: Write the failing tests:** forest `a(b(d), c)` expanded: `a` depth 0 `isLast` true `through []`; `b` depth 1 `isLast` false `through []`; `d` depth 2 `isLast` true `through [true]`; `c` depth 1 `isLast` true `through []`. A chain whose depth-1 ancestor is the last child gives `through [false]` for its depth-2 child. A depth-4 row has `through` of length 3. Collapsed nodes still produce correct `isLast` for the visible rows.
- [ ] **Step 2: Run** `cd frontend && node --test src/utils/sessionTree.test.js`. Expected: FAIL.
- [ ] **Step 3: Implement** the new row fields in `flattenTree` (the existing `{ id, depth }` consumers keep working).
- [ ] **Step 4: Run** the same command. Expected: PASS.

---

### Task 3: A tooltip switch on the activity indicator

**Files:**
- Modify: `frontend/src/components/ui/ProcessActivityIndicator.vue`
- Test: a source check next to existing tests of that component if one exists (`grep` for `ProcessActivityIndicator` in `frontend/src` and `frontend/tests`), otherwise `frontend/src/components/ui/ProcessActivityIndicator.tooltip.test.js`.

**Interfaces:**
- Produces: prop `tooltip: { type: Boolean, default: true }`. When `false`, none of the three display modes renders its `AppTooltip`. The default keeps every current caller unchanged.

- [ ] **Step 1: Write the failing test** (source checks: the prop with default `true`; each `AppTooltip` of the template is guarded by the prop).
- [ ] **Step 2: Run** it. Expected: FAIL.
- [ ] **Step 3: Implement** the prop and the guards.
- [ ] **Step 4: Run** the new test plus any existing test reading the component. Expected: PASS.

---

### Task 4: Row metadata and row visuals

**Files:**
- Modify: `frontend/src/components/session/list/SessionList.vue`, `frontend/src/components/session/list/SessionListItem.vue`
- Test: `frontend/src/components/session/list/SessionList.tree.test.js`, `frontend/src/components/session/list/SessionListItem.tree.test.js`, and any existing test that reads either file (`SessionListItem.rail.test.js`, `sidebar-rows.test.js`, `glide.test.js`, `list-exit.test.js`, `SessionList.loading.test.js`; keep them passing).

**Interfaces:**
- Consumes: Task 1 (`descendantIds`, `summary`, `descendantActivity`), Task 2 (`isLast`, `through`), Task 3 (`tooltip` prop), `processActivityDisplayMode` and `processActivityTooltip` from `frontend/src/utils/processActivity.js`.
- Produces: the `treeRow` prop of `SessionListItem`: `{ depth, isLast, through, hasChildren, expanded, count, pinned, latestDate, activity, hint }` where `count = summary.descendants`, `pinned = summary.pinned`, `latestDate = summary.latestDate`, `activity` is `descendantActivity(...)` for a row that has children and is collapsed, else `null`, and `hint` is unchanged. `SessionListItem` emits `toggle-tree` with the session as today.
- `SessionList.vue`: `treeRowMeta` builds the new shape; its retention (reuse the previous object when nothing changed) compares every scalar field, `through` element by element, and the `activity` fields, so an mtime tick of an active session does not re-render every row. The `activity` computation reads `store.processStates`, `store.sessions` and `props.sessionId` (the viewed session) with `isSessionUnread`.
- `SessionListItem.vue`: remove the toggle column, the leaf spacer and the old collapsed summary (and their CSS). Add: (1) the guides, drawn only for child rows: one vertical segment per entry of `through` that is `true` (using only the last `TREE_MAX_INDENT_LEVEL - 1` entries, so a deeper row keeps the third level's width), plus the row's own elbow, whose vertical part stops at the elbow when `isLast`; (2) the indentation, still on the wrapper through `--tree-indent-level` (capped depth), unchanged for roots; (3) the pill, only when `treeRow.hasChildren`: a `<button>` OUTSIDE the row's `wa-button`, placed at the end of the row without covering the row's text or the row's action menu trigger (the row reserves right padding only when it carries a pill), containing a chevron icon (down when expanded), the count, and, when collapsed and `processActivityDisplayMode(activity)` is not null, `<ProcessActivityIndicator :summary="activity" :tooltip="false" size="small" />`; `aria-expanded`, an accessible label that includes the count ("Expand N child sessions" / "Collapse N child sessions"); a click stops propagation and emits `toggle-tree`; native `<button>` needs its forced height reset (WA `native.css`); (4) one `AppTooltip` for the pill with the count, the pinned count when `> 0`, the subtree date when `latestDate != null` (same date display branch as the row's own date, `useRelativeTime` honored), and `processActivityTooltip(activity, mode)` when there is an indicator. Keep the `↰ from` hint rendering. `treeRow === null` renders exactly as today.

- [ ] **Step 1: Write the failing tests** (source checks, the repo's style for these two files): `treeRowMeta` builds `isLast`, `through`, `count`, `pinned`, `latestDate`, `activity` and calls `descendantActivity`; the retention compares `through` and `activity`; `SessionListItem` declares `treeRow` with the new shape, renders the pill as a `<button class="tree-pill">` outside the `wa-button.session-item`, passes `:tooltip="false"` to `ProcessActivityIndicator`, renders the guides, binds `--tree-indent-level` on the wrapper, no longer contains `tree-toggle-spacer` or `tree-summary`; `treeRow === null` leaves the existing class bindings unchanged. Update the assertions of the two existing tree tests that pinned the old toggle/summary markup.
- [ ] **Step 2: Run** `cd frontend && node --test src/components/session/list/SessionList.tree.test.js src/components/session/list/SessionListItem.tree.test.js`. Expected: FAIL.
- [ ] **Step 3: Implement** `treeRowMeta` and the retention in `SessionList.vue`, then the pill, guides and cleanup in `SessionListItem.vue` as specified above. You cannot see the UI: keep the CSS conservative and say so in the report.
- [ ] **Step 4: Run** the two new tests plus `src/components/session/list/SessionListItem.rail.test.js src/components/session/list/SessionList.loading.test.js src/styles/sidebar-rows.test.js src/styles/glide.test.js src/styles/list-exit.test.js`. Expected: PASS. Then `cd frontend && npm test`: no failure caused by the change.

---

### Task 5: Verification

- [ ] **Step 1:** `cd frontend && npm test`. Expected: all pass.
- [ ] **Step 2 (controller, in the browser):** on the running dev server (do not restart anything), with "Show as tree" on and a wide window: leaf and root rows look exactly like flat rows (no indent, no gap); a parent shows the pill at the end of its row without covering the title or the row menu; clicking the pill expands and collapses and does not open the session; child rows show the guide lines (elbow, and the vertical line continuing past a child while siblings follow); a collapsed parent with a live descendant shows the indicator in its pill; one tooltip on hover; compact and normal views; light and dark. Report what was seen and what could not be checked.
