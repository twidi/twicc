# Unread Session With Background Work: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** An unread `user_turn` session with a background shell or cron alternates the unread eye with the background-work icon, per session and in the aggregates.

**Architecture:** `ProcessIndicator` gets an `unread` input and owns the eye / alternation. A pure helper decides what background work a process state has. The activity summary gets two new figures scoped to unread sessions with work, and a new display mode `unread_background`. Each surface that hand-draws the eye next to a process state renders `ProcessIndicator` instead.

**Tech Stack:** Vue 3 (`<script setup>`), plain CSS keyframes, `node --test` (logic tests on pure utils, source-pattern tests on `.vue`/`.css` files, as in `glow.test.js` and `SessionListItem.tree.test.js`).

**Spec:** `docs/plans/2026-10-28-unread-background-work-indicator-design.md`

All commands run from `/home/twidi/dev/twicc-poc/frontend`. Test command: `npm test` (all) or `node --test src/<path>.test.js` (one file).

## Global Constraints

- Language: all code, comments and docs in English.
- Cycle: 2.4 s, 1.2 s per icon, cross-fade, opacity only (kept under reduced motion).
- Icon choice: shell wins over cron (terminal over clock), for one session and in the aggregate.
- During the alternation only the cross-fade animates. The eye's own pulse and the terminal's own pulse do not run.
- Out of scope, unchanged: command palette, favicon and global unread count, orchestration tab indicator, pending-request hand precedence on every surface, `isSessionUnread`, tooltips text.
- A surface keeps its own rules for which sessions are unread (viewed session, startup).
- No CHANGELOG entry. Commit trailer: `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`; list files in `git add`, never a directory.

## Review Focus

Failure modes the tasks' tests pin (each line has its test in the owning task):

1. Unread session with a cron only (no shell): clock, not terminal (Tasks 1, 4).
2. Aggregate: unread cron-only session + read session with a shell: icon is the clock, not the terminal (Tasks 2, 5).
3. Aggregate: one unread session without work among unread sessions with work: eye alone (Task 2).
4. `unread` given to `ProcessIndicator` outside `user_turn`: ignored, normal indicator (Task 4).
5. Project summary refreshes when only the new figures change, and stays referentially equal when nothing changes (Task 3).

---

## File Structure

| File | Responsibility |
|---|---|
| `src/utils/backgroundWork.js` | New pure helper `unreadBackgroundKind` |
| `src/utils/processActivity.js` | Summary figures, display mode `unread_background` |
| `src/utils/sessionTree.js` | `treeActivity` fills the new figures |
| `src/utils/projectProcessActivity.js` | Project index fills the new figures |
| `src/composables/useRailActiveSessions.js`, `src/components/session/list/SessionList.vue` | Summary comparison keys |
| `src/styles/motion.css` | Two alternation keyframes |
| `src/components/ui/ProcessIndicator.vue` | `unread` input, eye alone / alternation |
| `src/components/ui/ProcessActivityIndicator.vue` | Renders `unread_background` |
| `SessionListItem.vue`, `SidebarRail.vue`, `SessionSwitcher.vue` | Use `ProcessIndicator` with `unread` |

---

### Task 1: `unreadBackgroundKind` helper

**Files:**
- Modify: `src/utils/backgroundWork.js` (after `userTurnBackgroundShellCount`)
- Test: `src/utils/backgroundWork.test.js`

**Interfaces:**
- Produces: `unreadBackgroundKind(processState: ?Object): 'shell' | 'cron' | null`. `'shell'` when `userTurnBackgroundShellCount(processState) > 0`; else `'cron'` when `processState?.state === PROCESS_STATE.USER_TURN` and `activeCronCount(processState) > 0`; else `null`. A shell wins over a cron. Outside `user_turn` it is always `null`.

- [ ] **Step 1: Write the failing tests** in `backgroundWork.test.js`, using the file's existing process-state fixture style:
  - `unreadBackgroundKind` returns `'shell'` for `user_turn` with 1 shell.
  - returns `'shell'` for `user_turn` with 1 shell and 2 crons.
  - returns `'cron'` for `user_turn` with 0 shells and 1 cron.
  - returns `null` for `user_turn` with neither.
  - returns `null` for `assistant_turn` with a shell and a cron.
  - returns `null` for `null` and `undefined`.
- [ ] **Step 2:** Run `node --test src/utils/backgroundWork.test.js`. Expected: FAIL (`unreadBackgroundKind` is not exported).
- [ ] **Step 3:** Implement and export `unreadBackgroundKind` in `backgroundWork.js` with the contract above. Reuse `userTurnBackgroundShellCount` and `activeCronCount`.
- [ ] **Step 4:** Run the same command. Expected: PASS.
- [ ] **Step 5: Commit**

```bash
git add src/utils/backgroundWork.js src/utils/backgroundWork.test.js
git commit -m "feat(frontend): add unreadBackgroundKind helper" -m "Decides which background work (shell, cron or none) a user_turn process state shows behind an unread session. A shell wins over a cron." -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

### Task 2: Summary figures and `unread_background` mode

**Files:**
- Modify: `src/utils/processActivity.js`
- Test: `src/utils/processActivity.test.js`

**Interfaces:**
- Consumes: nothing new (the figures are passed in by callers, Tasks 3 and 4 fill them).
- Produces:
  - `summarizeProcessActivity(processStates, unreadCount = 0, unreadBackground = { sessions: 0, shells: 0 })`. The result gains `unreadBackgroundCount` (= `unreadBackground.sessions`: unread sessions with a shell or cron) and `unreadShellCount` (= `unreadBackground.shells`: those of them with a shell).
  - `processActivityDisplayMode` returns `'unread_background'` when `unreadCount > 0` and `unreadBackgroundCount === unreadCount`. It returns `'unread'` when `unreadCount > 0` otherwise. The new mode sits at the `unread` position in the cascade (after `pending_request`, before `assistant_turn`).
  - `processActivityTooltip(summary, mode)` treats `'unread_background'` exactly like `'unread'`.

- [ ] **Step 1: Write the failing tests** in `processActivity.test.js` (the file has helper `ps(state, {pending, crons, shells})` and `modeOf(states, unread)`; add an optional third argument `unreadBackground` to `modeOf` and pass it through):
  - `unreadCount 2`, `unreadBackground {sessions: 2, shells: 1}` → mode `'unread_background'`.
  - `unreadCount 3`, `unreadBackground {sessions: 2, shells: 2}` → mode `'unread'` (one unread session without work wins).
  - `unreadCount 1`, `unreadBackground {sessions: 0, shells: 0}` → `'unread'`.
  - `unreadCount 0` → never `'unread_background'`.
  - pending request still beats `'unread_background'`.
  - `'unread_background'` beats an `assistant_turn` session.
  - the existing "counts add up across the set" `deepEqual` gains `unreadBackgroundCount: 0, unreadShellCount: 0`; a new test passes `{sessions: 1, shells: 0}` and expects `unreadBackgroundCount: 1, unreadShellCount: 0`.
  - `processActivityTooltip(summary, 'unread_background')` equals the `'unread'` tooltip of the same summary.
- [ ] **Step 2:** Run `node --test src/utils/processActivity.test.js`. Expected: FAIL.
- [ ] **Step 3:** Implement in `processActivity.js`. Update the JSDoc return type and the cascade comment.
- [ ] **Step 4:** Run the same command. Expected: PASS.
- [ ] **Step 5: Commit** (`feat(frontend): add the unread_background display mode`), files: `processActivity.js`, `processActivity.test.js`.

### Task 3: Tree aggregates fill the figures

**Files:**
- Modify: `src/utils/sessionTree.js` (`treeActivity`), `src/composables/useRailActiveSessions.js` (`ACTIVITY_KEYS`), `src/components/session/list/SessionList.vue` (`ACTIVITY_KEYS`)
- Test: `src/utils/sessionTree.test.js`, `src/utils/railActiveSessionsTree.test.js`

**Interfaces:**
- Consumes: `unreadBackgroundKind` (Task 1), the three-argument `summarizeProcessActivity` (Task 2).
- Produces: `treeActivity(...)` results carry `unreadBackgroundCount` and `unreadShellCount`. Both `ACTIVITY_KEYS` arrays end with `'unreadBackgroundCount', 'unreadShellCount'`.

- [ ] **Step 1: Write the failing tests.** In `sessionTree.test.js` (helpers `act`, `s`, `sessionsMap` exist):
  - one unread session, process `user_turn` with a shell → `unreadBackgroundCount 1`, `unreadShellCount 1`.
  - one unread session with a cron only → `unreadBackgroundCount 1`, `unreadShellCount 0`.
  - one unread session without process → `unreadCount 1`, `unreadBackgroundCount 0`.
  - one unread cron-only session plus one **read** session with a shell → `unreadBackgroundCount 1`, `unreadShellCount 0`, `backgroundShellCount 1` (Review Focus 2).
  - the viewed session is excluded from both new figures.
  In `railActiveSessionsTree.test.js`, next to the `cumulative.unreadCount` assertions: a collapsed node with an unread child that has a shell exposes `cumulative.unreadBackgroundCount === 1`.
- [ ] **Step 2:** Run `node --test src/utils/sessionTree.test.js src/utils/railActiveSessionsTree.test.js`. Expected: FAIL.
- [ ] **Step 3:** In `treeActivity`, where a session is counted unread, also compute `unreadBackgroundKind(ps)`; count sessions whose kind is non-null and sessions whose kind is `'shell'`; pass them as the third argument. Append the two keys to both `ACTIVITY_KEYS`.
- [ ] **Step 4:** Run the same command, then `npm test`. Expected: PASS.
- [ ] **Step 5: Commit** (`feat(frontend): count unread sessions with background work in tree aggregates`). Files: the five above.

### Task 4: `ProcessIndicator` `unread` input and alternation

**Files:**
- Modify: `src/styles/motion.css`, `src/components/ui/ProcessIndicator.vue`
- Test: `src/styles/glow.test.js` (new test `18d`, same style as `18b`; `18c` is taken by the upload strip test)

**Interfaces:**
- Consumes: nothing from earlier tasks. `ProcessIndicator` has no process state, only its props `backgroundShells` and `hasActiveCrons`, so it derives the background work itself: `backgroundShells > 0` gives the terminal icon, else `hasActiveCrons` gives the clock icon, else none (the same rule as its current `effectiveIconName`).
- Produces:
  - Global keyframes `motion-alternate-eye` and `motion-alternate-work` in `motion.css`, 2.4 s cycle, opacity only. The eye keyframes hold opacity 1 up to 40%, 0 from 50% to 90%, and 1 at 100%. The work keyframes are the mirror.
  - `ProcessIndicator` prop `unread: Boolean` (default `false`). Behaviour, only when `state === 'user_turn'`: `unread` with no background work (no shells and no active crons) renders the eye alone with the current unread look (colour `var(--process-unread-color, var(--wa-color-warning-60))`, `animation: motion-status-pulse 2.4s ease-in-out infinite`). `unread` with background work renders two stacked icons in one cell: the eye (class `process-indicator__alt--eye`, `animation: motion-alternate-eye 2.4s ease-in-out infinite`) and the work icon (terminal if shells, else clock; class `process-indicator__alt--work`, `animation: motion-alternate-work 2.4s ease-in-out infinite`, colour `--process-color`, no own pulse). Outside `user_turn`, `unread` is ignored.
  - A parent can override the eye colour with the custom property `--process-unread-color`.

- [ ] **Step 1: Write the failing test `18d`** in `glow.test.js`, reading `motion.css` and `ProcessIndicator.vue` as `18b` does:
  - `motion.css` defines both keyframes; every frame of both has the single declaration `opacity`; frame values match the contract above.
  - `ProcessIndicator.vue` has a Boolean prop `unread` defaulting to `false`.
  - its styles contain `.process-indicator__alt--eye` with `motion-alternate-eye 2.4s ease-in-out infinite` and `.process-indicator__alt--work` with `motion-alternate-work 2.4s ease-in-out infinite`.
  - neither alternation class carries `motion-status-pulse` nor `motion-status-pulse-deep`.
  - the eye-alone class `.process-indicator__icon--unread` carries `motion-status-pulse 2.4s ease-in-out infinite` (name the eye-alone icon with this class). The unread colour rules (`--process-unread-color`) must be declared after `.process-indicator__icon` in the style block, because they have the same specificity; assert that order in the test.
  - the file has no `:root.reduce-motion` rule that removes or overrides the alternation animations (they stay under reduced motion).
  - the template guards the unread rendering with `state === 'user_turn'` (Review Focus 4).
- [ ] **Step 2:** Run `node --test src/styles/glow.test.js`. Expected: FAIL on `18d` only.
- [ ] **Step 3:** Add the keyframes next to `motion-status-pulse-deep`. Implement the prop, the computed icon choice and the template in `ProcessIndicator.vue`. Update the component's header comment. Keep the existing non-unread rendering untouched.
- [ ] **Step 4:** Run `node --test src/styles/glow.test.js src/styles/motion.test.js`. Expected: PASS (test 18 still holds: the hand-drawn eyes that stay keep their pulse).
- [ ] **Step 5: Commit** (`feat(ui): alternate the unread eye with background work in ProcessIndicator`). Files: `motion.css`, `ProcessIndicator.vue`, `glow.test.js`.

### Task 5: Project index fills the figures; aggregate rendering

**Files:**
- Modify: `src/utils/projectProcessActivity.js`, `src/components/ui/ProcessActivityIndicator.vue`
- Test: `src/utils/projectProcessActivity.test.js`, `src/components/ui/ProcessActivityIndicator.tooltip.test.js`, `src/components/ui/AggregatedProcessIndicator.activity.test.js`

**Interfaces:**
- Consumes: `unreadBackgroundKind` (Task 1), `unreadBackgroundCount` / `unreadShellCount` and the mode (Task 2), `ProcessIndicator` `unread` (Task 4).
- Produces: `index.summary(projectIds, previous)` results carry the two figures (`EMPTY` gains them at `0`; `sameSummary` compares them). `ProcessActivityIndicator` renders mode `'unread_background'` through its existing `ProcessIndicator` branch: `state="user_turn"`, `unread` true, `background-shells = summary.unreadShellCount`, `has-active-crons = summary.unreadShellCount === 0`. It adds no `AppTooltip`.

- [ ] **Step 1: Write the failing tests.**
  - `projectProcessActivity.test.js` (extend its fixture style): an unread session with a shell in project `q` gives `summary(['q'])` `unreadBackgroundCount 1`, `unreadShellCount 1`; a cron-only unread session next to a read session with a shell gives `unreadShellCount 0` and `backgroundShellCount 1` (Review Focus 2); the figures sum across several project ids; the existing literal expectations gain the two keys: line ~57 gets `unreadBackgroundCount: 0, unreadShellCount: 0`; line ~63 (session `a` unread in `user_turn` with 2 shells) gets `unreadBackgroundCount: 1, unreadShellCount: 1`.
  - same file, startup guard: with `store.isStartupInProgress = true` the new figures are `0`.
  - same file, caching (Review Focus 5): when only a shell appears on an already-unread session, `summary` returns a new object (the figures changed); when an unrelated field changes and the figures do not, it returns the previous object (`assert.strictEqual`).
  - `ProcessActivityIndicator.tooltip.test.js`: the existing "exactly 3 AppTooltip, all guarded" assertions must still hold unchanged.
  - `AggregatedProcessIndicator.activity.test.js`: add a case using the existing store-fixture pattern: unread cron-only session in project `p`, read shell session in `p` → `p.value.unreadBackgroundCount === 1` and `unreadShellCount === 0`.
  - source check (new test in `ProcessActivityIndicator.tooltip.test.js`): the template passes `unread` and `unreadShellCount` to `ProcessIndicator`.
- [ ] **Step 2:** Run `node --test src/utils/projectProcessActivity.test.js src/components/ui/*.test.js`. Expected: FAIL.
- [ ] **Step 3:** In `projectProcessActivity.js` extend the existing `unreadCounts` computed so its single scan also produces, per project, the unread sessions with background work and those with a shell (one `isSessionUnread` call per session, skipped while `store.isStartupInProgress`; field-wise equality for `retainMap`). `index.unread()` and `index.totalUnread()` keep reading only the plain unread count. Add both keys to `EMPTY` and sum them in `summary`. Update `ProcessActivityIndicator.vue`: state mapping, props, header cascade comment. Update the header comment of `AggregatedProcessIndicator.vue`.
- [ ] **Step 4:** Run the same command, then `npm test`. Expected: PASS.
- [ ] **Step 5: Commit** (`feat(ui): alternate the unread eye with background work in aggregated indicators`). Files: the six above.

### Task 6: Session list rows

**Files:**
- Modify: `src/components/session/list/SessionListItem.vue`
- Test: `src/components/session/list/SessionListItem.rail.test.js` (or a new `SessionListItem.unread.test.js` in the same folder, same source-pattern style as `SessionListItem.tree.test.js`)

**Interfaces:**
- Consumes: `ProcessIndicator` `unread` (Task 4).
- Produces: in the process line and in compact mode, a session with a `processState` renders one `ProcessIndicator` with `:unread="hasUnread"`, instead of an eye or an indicator. The hand-drawn eye stays only where there is no `processState` (standalone eye on the project line, compact eye of a session without process), because such a session has no background work.

- [ ] **Step 1: Write the failing source tests:**
  - the process-line `ProcessIndicator` (id `rowId('process-indicator')`) has `:unread="hasUnread"` and is no longer behind a `v-else` of an eye.
  - the compact `ProcessIndicator` (id `rowId('compact-process-indicator')`) has `:unread="hasUnread"`.
  - the template no longer contains the ids `process-unread`; it still contains `standalone-unread` and `compact-unread`.
  - the pending-request hand conditions are unchanged (still `!hasUnread` in compact mode).
- [ ] **Step 2:** Run the test file. Expected: FAIL.
- [ ] **Step 3:** Edit the template.
  - Process line: remove the `process-unread` eye and its tooltip. The `ProcessIndicator` always renders and gets `:unread="hasUnread"`. Its tooltip text is `New content to read · {{ processTooltip }}` when `hasUnread`, else `processTooltip`.
  - Compact mode: the process `ProcessIndicator` condition drops `!hasUnread`. It keeps `!pendingRequest` unless `hasUnread` (pending hand shows only when not unread, as today). The compact process tooltip text is `New content to read · {{ processTooltip }}` when `hasUnread`, else `processTooltip`; its `v-if` keeps `!cumulativeMode` (`SessionListItem.tree.test.js` requires it on every `rowId('compact-*')` line). The `compact-unread` eye and tooltip render only when `compactView && !cumulativeMode && hasUnread && !processState`. In compact mode with unread, set the indicator font size to `var(--wa-font-size-xs)` with an inline `:style`, so the unread look keeps the compact eye size.
  - Keep `.unread-indicator` and `.compact-unread-indicator` CSS (still used).
  - Update the nearby comments.
- [ ] **Step 4:** Run the test file, then `node --test src/components/session/list/*.test.js src/styles/glow.test.js`. Expected: PASS.
- [ ] **Step 5: Commit** (`feat(sidebar): alternate the unread eye with background work in session rows`). Files: `SessionListItem.vue` and the test file.

### Task 7: Sidebar rail rows and preview children

**Files:**
- Modify: `src/components/sidebar/SidebarRail.vue`
- Test: `src/components/sidebar/SidebarRail.test.js`

**Interfaces:**
- Consumes: `ProcessIndicator` `unread` (Task 4). Rail rows already carry `hasUnread`, `userTurnBackgroundShells`, `hasActiveCrons`; preview children carry `hasUnread`, `processState`, `userTurnBackgroundShells`, `hasActiveCrons`.
- Produces: a rail row and a preview child with a `processState` render `ProcessIndicator` with `:unread`. The eye alone stays for a preview child without `processState`.

- [ ] **Step 1: Write the failing source tests** in `SidebarRail.test.js`:
  - the row template has no `wa-icon ... v-if="row.hasUnread"`; its `ProcessIndicator` has `:unread="row.hasUnread"`; `row.pendingRequest` hand branch still precedes it only when not unread.
  - the preview child `ProcessIndicator` has `:unread="child.hasUnread"`; a `wa-icon name="eye"` remains only for a child without `processState`.
  - rewrite the two existing assertions that match the old template: `SidebarRail.test.js:132` (`ProjectMark … v-if="row.hasUnread" … v-else-if="row.pendingRequest" … ProcessIndicator`) and `:155` (`<template v-else> … v-if="row.hasUnread" … row.processState.state`). They describe the new shape: `row.pendingRequest` hand guarded by `!row.hasUnread`, then `ProcessIndicator` with `:unread="row.hasUnread"`.
- [ ] **Step 2:** Run `node --test src/components/sidebar/SidebarRail.test.js`. Expected: FAIL.
- [ ] **Step 3:** Edit the template. The hand shows when `pendingRequest && !hasUnread`, as today (unread wins over pending on the rail). Keep the `.rail-unread` CSS for the remaining eye. The rule `.rail-session-button > wa-icon { font-size: .75rem }` matches only a direct `wa-icon`: give the row `ProcessIndicator` the same `.75rem` font size when unread (inline `:style`, as in Task 6) so the eye keeps its size. The preview child keeps the same rule: its `ProcessIndicator` gets the `.rail-unread` size (the child eye has no explicit size today, so it takes the `small` size of the indicator; accepted).
- [ ] **Step 4:** Run `node --test src/components/sidebar/*.test.js src/styles/glow.test.js`. Expected: PASS.
- [ ] **Step 5: Commit** (`feat(sidebar): alternate the unread eye with background work in the rail`). Files: `SidebarRail.vue`, `SidebarRail.test.js`.

### Task 8: Session switcher

**Files:**
- Modify: `src/components/app/SessionSwitcher.vue`
- Test: new `src/components/app/SessionSwitcher.unread.test.js` (source-pattern style, like `settingsNavigation.test.js`)

**Interfaces:**
- Consumes: `ProcessIndicator` `unread` (Task 4). `stateOf` already returns `{ kind: 'unread', processState }`.
- Produces: an `unread` row with a `processState` renders `ProcessIndicator` with `unread`; the eye colour stays `var(--wa-color-brand-60)` through `--process-unread-color`. An `unread` row without `processState` keeps the hand-drawn `.switcher-unread` eye.

- [ ] **Step 1: Write the failing source tests:** the template renders `ProcessIndicator` for `row.state.kind === 'unread'` when `row.state.processState` exists, with `unread`; the eye `wa-icon` remains only for `unread` without `processState`; the style sets `--process-unread-color: var(--wa-color-brand-60)` on the switcher indicator.
- [ ] **Step 2:** Run the new test. Expected: FAIL.
- [ ] **Step 3:** Edit the template and add the colour rule.
- [ ] **Step 4:** Run `node --test src/components/app/SessionSwitcher.unread.test.js src/styles/glow.test.js`. Expected: PASS.
- [ ] **Step 5: Commit** (`feat(switcher): alternate the unread eye with background work`). Files: `SessionSwitcher.vue` and the test.

### Task 9: Full verification

**Files:** none modified unless a check fails.

- [ ] **Step 1:** Run `npm test`. Expected: all pass.
- [ ] **Step 2:** Run `npm run build` (the indicator is not in a non-HMR bundle, but the build catches template errors). Expected: success.
- [ ] **Step 3:** Report to the user: the dev server restart and a manual browser check (unread session with a shell or cron; project badge with only such sessions) are theirs. List the surfaces to look at: session list (normal and compact), rail row and preview, session switcher, project and workspace badges, collapsed tree row.
