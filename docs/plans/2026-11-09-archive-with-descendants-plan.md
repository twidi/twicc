# Archive With Descendants Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Archiving a session that has descendants opens one dialog with a checkbox tree, and archives the selection.

**Architecture:** The backend adds a cron count to the topology payload and one endpoint that archives eligible hidden descendants. The frontend reads the topology, computes rows and checkbox rules in a pure module, shows one global dialog, and runs the flow from `useStopSessionProcess.js`, the single entry point of every single-session archive gesture.

**Tech Stack:** Django 6 ASGI, Vue 3 `<script setup>`, Pinia, Web Awesome 3, vue-i18n, pytest, `node:test`.

**Spec:** `docs/plans/2026-11-09-archive-with-descendants-spec.md`. Read it first. It is the authority on behaviour.

## Global Constraints

- Work on the current branch. The checkout is shared and holds other people's uncommitted changes. Never run a working-tree-wide git command. Commit with `git add <file>` file by file, and only the files of your task. Some files of your task can already hold changes that are not yours: then use `git diff <file>` first, and if the file holds foreign hunks, stop and report it.
- Commit message: Conventional Commit with a lowercase summary and a body. Trailer: `Co-Authored-By: Claude MODEL <noreply@anthropic.com>`, with the exact name of the model you run as.
- All text displayed in the interface goes through i18n, in all five catalogs of `frontend/src/i18n/locales/` (`en`, `fr`, `de`, `es`, `pt-BR`). Follow `docs/internationalization.md`. Backend-owned UI errors use `ui_error(message, key)` from `src/twicc/core/ui_messages.py`.
- Python: `orjson`, never stdlib `json`. `NamedTuple` for immutable data.
- Do not run `migrate`, a dev server restart or a package installation. No migration is needed.
- Python tests: `uv run pytest <path>`. Frontend tests: `cd frontend && node --test <path>` for one file, `npm test` for all.
- Barrier definition (spec): state in `starting`, `assistant_turn`, `awaiting_user_input`; or a non-null `background_work_in_progress`; or an active cron count above 0; or a truthy `pinned`.
- Work in progress (spec, warning): the first two conditions of the barrier definition.
- UI message keys, under `session.archiveDescendants.`, with their English values: `title` `Archive "{title}"?`; `intro` `Choose the descendant sessions to archive with it:`; `loading` `Loading descendant sessions…`; `loadError` `The descendant sessions could not be loaded.`; `selectAll` `Select all`; `deselectAll` `Deselect all`; `cancel` `Cancel`; `archiveOnly` `Archive this session only`; `stopArchiveOnly` `Stop and archive this session only`; `archiveWith` `Archive with {count} descendant | Archive with {count} descendants`; `stopArchiveWith` `Stop and archive with {count} descendant | Stop and archive with {count} descendants`; `failed` `{count} session could not be archived. | {count} sessions could not be archived.`. The existing key `session.stopConfirmation.cancel` (`Cancel, keep the process`) is reused.

## Review Focus

- A spawned leaf session (has `spawn_root`, no descendant): archive goes straight through, with no dialog flash.
- The topology read fails: the dialog offers the error, `Cancel` and the target-only action, and today's target warning still shows.
- A listed session that is not loaded in the store is really archived, and unpinned if `autoUnpinOnArchive` is on.
- A check descent reaches a row below a hidden barrier: that row and everything below it stay as they were.
- The hidden-descendant endpoint runs when listed rows are already archived in the database: eligibility follows the sent ids, not the database flag.

---

### Task 1: Expose the active cron count in the topology

**Files:**
- Create: `src/twicc/core/services/session_crons.py`
- Modify: `src/twicc/cli/topology.py`
- Modify: `src/twicc/agent/plugin/twicc/skills/twicc-topology/SKILL.md`, `src/twicc/agent/plugin/twicc/.claude-plugin/plugin.json`, `SKILLS-AND-CLI.md`
- Test: `tests/test_cli_topology.py`

**Interfaces:**
- Produces: `nodes[].active_cron_count: int` on every topology node, with or without `--no-processes`. `active_cron_counts(session_ids) -> dict[str, int]` in `session_crons.py`: non-expired crons per session id, same expiry rule as `SessionCron.active_for_session`, one query, all providers.

Keep this helper in the new service file: do not modify `src/twicc/core/models.py`.

- [ ] **Step 1: Write failing tests** in `tests/test_cli_topology.py`: `test_topology_node_reports_active_cron_count` (one session with one recurring non-expired cron → `1`, one with none → `0`), and `test_topology_cron_count_ignores_expired` (a recurring cron older than `CLAUDE_RECURRING_MAX_AGE` → `0`).
- [ ] **Step 2: Run them.** `uv run pytest tests/test_cli_topology.py -k cron -v`. Expected: FAIL.
- [ ] **Step 3: Implement** `active_cron_counts`, and add the count in `build_topology` / `_serialize_topology_node`.
- [ ] **Step 4: Run** `uv run pytest tests/test_cli_topology.py -v`. Expected: PASS. Update any existing exact-shape assertion that the new key breaks.
- [ ] **Step 5: Document** `nodes[].active_cron_count` in the skill's output section and in `SKILLS-AND-CLI.md`. Bump `plugin.json` by one patch.
- [ ] **Step 6: Commit** as `feat(topology): expose the active cron count of each node`.

### Task 2: Backend path that archives eligible hidden descendants

**Files:**
- Create: `src/twicc/core/services/archive_descendants.py`
- Modify: `src/twicc/views.py`, `src/twicc/urls.py`, the five `frontend/src/i18n/locales/*.json` (one `backend.sessions.*` key)
- Test: `tests/test_archive_hidden_descendants.py`

**Interfaces:**
- Consumes: `active_cron_counts(session_ids) -> dict[str, int]` from `twicc.core.services.session_crons`; `load_process_rows` and `project_virtual_state` from `twicc.cli._process_state`; `apply_session_archived_change` from `twicc.core.services.session_update`.
- Produces:
  - `select_hidden_descendants(target_id: str, archived_ids: set[str]) -> list[str]` (sync, reads the database).
  - `async archive_hidden_descendants(target_id: str, archived_ids: set[str]) -> tuple[list[str], list[str]]`: `(archived, failed)`.
  - `POST /api/projects/<project_id>/sessions/<session_id>/archive-hidden-descendants/`, body `{"archived_ids": [str]}`, response `{"archived": [str], "failed": [str]}`. Target resolved with `_resolve_session_or_404`. A body that is not a list of strings → 400, key `backend.sessions.archived_ids_invalid`.

The walk over `spawned_by` children, from the target, with `eligible = True` at the target. For each child, the first matching case applies:

| Child | Effect | `eligible` for its children |
|---|---|---|
| `tree_detached` | Skip the subtree. | none |
| id in `archived_ids` | Nothing. | `True` |
| already archived | Nothing. | unchanged |
| hidden and barrier | Nothing. | `False` |
| hidden, not barrier | Select it if `eligible`. | unchanged |
| any other (listed, not sent) | Nothing. | `False` |

Barrier state is read at call time. Use `load_process_rows(ids, os.getpid())`. Do not broadcast: hidden sessions never surface. Call `apply_session_archived_change(session, True)` once per selected session. Each failure is caught, logged and added to `failed`.

- [ ] **Step 1: Write failing tests**, one per table line, plus: `test_listed_child_already_archived_in_db_and_sent_stays_eligible`, `test_hidden_cron_is_barrier`, `test_hidden_below_hidden_barrier_is_not_selected`, `test_endpoint_rejects_non_list_body`, `test_endpoint_returns_404_for_unknown_target`.
- [ ] **Step 2: Run** `uv run pytest tests/test_archive_hidden_descendants.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement** the service, the view and the route.
- [ ] **Step 4: Run** the same command. Expected: PASS.
- [ ] **Step 5: Commit** as `feat(archive): archive the eligible hidden descendants of a session`.

### Task 3: Pure archive tree rules

**Files:**
- Create: `frontend/src/utils/archiveTree.js`
- Test: `frontend/src/utils/archiveTree.test.js`

**Interfaces:**
- Consumes: the topology payload (`tree: {id, children}`, `nodes: [{id, session, process, active_cron_count}]`).
- Produces (all pure, no store, no i18n):
  - `isWorkInProgress(work: {state, backgroundWork}) -> boolean`.
  - `isArchiveBarrier(node) -> boolean`.
  - `buildArchiveTree(topology, targetId) -> Row[]`. `Row = {id, session, depth, isLast, through, barrier, stopsDescent, openChecked, childIds}`. Rows are flat, in tree order. `depth` is 0 for the first listed level. `isLast` and `through` have the shape `treeGuides` of `sessionTree.js` expects (see `flattenTree`). `childIds` are listed rows only.
  - `initialChecked(rows) -> Set<string>`.
  - `checkRow(rows, checked, id) -> Set<string>`, `uncheckRow(rows, checked, id) -> Set<string>`, `selectAll(rows) -> Set<string>`, `allChecked(rows, checked) -> boolean`.
  - `warningTotals(entries: [{working: boolean, shells: number, crons: number}]) -> {processCount, shellCount, cronCount}`.
  - `archiveDialogButtons({checkedCount, warningVisible, targetConcerned}) -> {primary: {key, count?, danger}, secondary: null | {key}, cancel: {key}}`, with full keys from the Global Constraints, each with its `session.archiveDescendants.` prefix: `primary.key` is one of `archiveOnly`, `stopArchiveOnly`, `archiveWith`, `stopArchiveWith`; `secondary.key` is `archiveOnly` or `stopArchiveOnly`; `cancel.key` is `session.archiveDescendants.cancel` or `session.stopConfirmation.cancel`. Mapping: spec section "Buttons".

Walk rules: spec table "Which sessions are concerned". A detached node ends the walk. Archived and hidden nodes are not rows, and their descendants attach to the nearest listed ancestor. An already archived node is never a crossed barrier, even when hidden. `stopsDescent` is true for a barrier and for a row whose path from its nearest listed ancestor crosses a hidden barrier. `openChecked` is true only when no ancestor row and no crossed hidden barrier prevents it, and the row itself does not stop the descent. `checkRow` checks the row and descends through children, leaving as they are every child with `stopsDescent` and everything below it. `uncheckRow` removes the row and its whole subtree.

- [ ] **Step 1: Write failing tests**: detached subtree absent; archived and hidden nodes traversed with reattachment; guide shape on a three-level tree; barrier and below barrier unchecked at opening; check descent stops at a barrier; checking a barrier checks it; check descent stops below a hidden barrier; uncheck removes the subtree; child checked with parent unchecked is allowed; `selectAll` includes barriers; button labels for zero checked, warning on target, warning on a checked row only, no warning.
- [ ] **Step 2: Run** `cd frontend && node --test src/utils/archiveTree.test.js`. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** the same command. Expected: PASS.
- [ ] **Step 5: Commit** as `feat(archive): add the descendant tree and checkbox rules`.

### Task 4: Archive dialog component

**Files:**
- Create: `frontend/src/components/app/ArchiveDescendantsDialog.vue`
- Modify: the five `frontend/src/i18n/locales/*.json`

**Interfaces:**
- Consumes: from `utils/archiveTree.js`: `Row = {id, session, depth, isLast, through, barrier, stopsDescent, openChecked, childIds}`; `initialChecked(rows) -> Set<string>`; `checkRow(rows, checked, id) -> Set<string>`; `uncheckRow(rows, checked, id) -> Set<string>`; `selectAll(rows) -> Set<string>`; `allChecked(rows, checked) -> boolean`; `isWorkInProgress({state, backgroundWork}) -> boolean`; `warningTotals([{working, shells, crons}]) -> {processCount, shellCount, cronCount}`; `archiveDialogButtons({checkedCount, warningVisible, targetConcerned}) -> {primary: {key, count?, danger}, secondary: null | {key}, cancel: {key}}`. `treeGuides` from `utils/sessionTree.js`. `batchStopConfirmationMessage`, `backgroundShellCount`, `userTurnBackgroundShellCount`, `activeCronCount` from `utils/backgroundWork.js`. Store `getProcessState`.
- Produces: props `open: boolean`, `status: 'loading' | 'ready' | 'error'`, `targetSession: Object | null`, `rows: Row[]`, `cronCounts: Record<string, number>` (session id → cron count from the opening read; `{}` after a read failure). Events `confirm` with `{ checkedIds: string[], targetOnly: boolean }`, and `cancel`.

Add the keys listed in the Global Constraints.

Requirements, all from the spec section "Dialog content":
- Copy the open/close wiring of `StopProcessConfirmDialog.vue`, including the frozen values during the hide animation. Guard `wa-hide` with `event.target !== event.currentTarget`.
- Rows are rendered from `rows`, with guides built like `.tree-guide` / `.tree-elbow` in `SessionListItem.vue`. No guide at depth 0.
- Row order: `wa-checkbox`, thumbtack icon if pinned, `ScrollableText` title (`components/ui/ScrollableText.vue`, `getSessionDisplayName` from `utils/sessionTitle.js`), `ProcessIndicator` (`components/ui/ProcessIndicator.vue`, only with a live process state; props `state`, `has-active-crons`, `background-shells`, `size="small"`). One line. Use `AppTooltip` (`components/ui/AppTooltip.vue`) for the full title, as the sidebar does.
- `v-scroll-shadow` on the scrolling list, with a maximum height.
- Warning callout: `batchStopConfirmationMessage({mode: 'archive', ...warningTotals(...)})`, plus `session.stopConfirmation.cronWarning` and `restoreHint` when `cronCount > 0`. Totals use the target and the checked rows, with live state from `store.getProcessState`, and `cronCounts[id]` for a session with no live process. `targetConcerned` is true when the target's own entry is working or has shells or crons.
- Footer: `flex-wrap`, primary button with `margin-left: auto`.
- `status === 'error'`: `wa-callout variant="danger"`, target warning, no list, no Select all; buttons Cancel and target-only.
- `status === 'loading'`: `wa-spinner` and `loading`; only Cancel.

- [ ] **Step 1: Implement** the component and the catalog keys.
- [ ] **Step 2: Run** `cd frontend && npm run lint:i18n`. Expected: no new error for the new keys.
- [ ] **Step 3: Run** `cd frontend && npx vite build --mode development --outDir /tmp/archive-dialog-build`. Expected: build succeeds. Do not write in `frontend/dist` or the static directory.
- [ ] **Step 4: Commit** as `feat(archive): add the archive with descendants dialog`.

### Task 5: Wire the flow

**Files:**
- Modify: `frontend/src/composables/useStopSessionProcess.js`, `frontend/src/App.vue`, `frontend/src/stores/data.js` (`setSessionArchived`)
- Create: `frontend/src/utils/archiveFlow.js`
- Test: `frontend/src/utils/archiveFlow.test.js`

**Interfaces:**
- Consumes: `GET /api/projects/<project_id>/sessions/<session_id>/topology/`, with `tree` and `nodes[{id, session, process, active_cron_count}]`. `POST /api/projects/<project_id>/sessions/<session_id>/archive-hidden-descendants/`, body `{"archived_ids": [str]}`, response `{"archived": [str], "failed": [str]}`. `buildArchiveTree(topology, targetId) -> Row[]` from `utils/archiveTree.js`, with `Row = {id, session, ...}`, where `session.project_id` and `session.pinned` come from the topology node. Dialog `ArchiveDescendantsDialog.vue`, props `open`, `status`, `targetSession`, `rows`, `cronCounts`, events `confirm({checkedIds, targetOnly})` and `cancel`.
- Produces: `createArchiveFlow(deps) -> {request(sessionId), confirm({checkedIds, targetOnly}), cancel()}` in `archiveFlow.js`, pure and free of store imports. `deps = {getSession(id), readTopology(projectId, id), archiveHidden(projectId, id, archivedIds), archiveOne({id, projectId, pinned}), runTodayPath(id, {onConfirmed}), setDialog(stateOrNull), reportFailures(count), loadingDelayMs}`. `useStopSessionProcess.js` exports the ref `archiveDialogState` (`null` or `{sessionId, projectId, status, targetSession, rows, cronCounts}`), `confirmArchiveDialog(payload)`, `cancelArchiveDialog()`. `store.setSessionArchived(projectId, sessionId, archived, { knownPinned } = {})`: `knownPinned` replaces `session?.pinned` for the unpin decision when the session is not in the store.

Flow of `stopSessionProcess(id, { archive: true })`:
1. `session.spawn_root` is falsy: today's path, unchanged, with no network call.
2. Otherwise read the topology. Show the dialog in `loading` only if the read lasts more than 250 ms (`ARCHIVE_TREE_LOADING_DELAY_MS`). Ignore a result if the user cancelled, and ignore a second archive request for the same session while one runs.
3. Read failed: dialog in `error`, with `cronCounts` empty.
4. Rows empty: today's path through `runTodayPath`. `onConfirmed` runs when the archive proceeds: at once, or when the user confirms today's crons and shells confirmation. It calls `archiveHidden` with `archived_ids: [target]`.
5. Rows present: dialog in `ready`. `cronCounts` holds `active_cron_count` of the target and of each row.

Confirmation:
- `targetOnly`, or no row checked: archive the target only, with no hidden call.
- Otherwise call `archiveHidden` first, with the target and the checked ids, then archive the target and each checked row.
- The target and each checked row: stop the process if stoppable, as `doKill` does, then `setSessionArchived`. A checked row uses the `project_id` and `pinned` of its topology node, and never depends on `store.getSession`. The target uses the `projectId` of the dialog state and its store record.
- Failures are collected and reported by one `useToast` (`composables/useToast.js`) error with `session.archiveDescendants.failed`, on every `confirm` path and on every `archiveHidden` call. On `runTodayPath`, the target's own archive keeps today's handling. `failed` entries of the response count one each. A failed `archiveHidden` request counts as one failure and does not block the listed archives. Other sessions are not undone.

- [ ] **Step 1: Write failing tests** for: no `spawn_root` makes no read; empty rows go to today's path; the hidden call waits for today's confirmation; `targetOnly` makes no hidden call; the endpoint receives target and checked ids; a failed read gives `error`; a failed `archiveHidden` still archives the listed sessions and reports one failure in total; a failed target-only archive reports one failure.
- [ ] **Step 2: Run** `cd frontend && node --test src/utils/archiveFlow.test.js`. Expected: FAIL.
- [ ] **Step 3: Implement** the flow and mount `ArchiveDescendantsDialog` in `App.vue`, next to `StopProcessConfirmDialog`.
- [ ] **Step 4: Run** the tests, then `cd frontend && npm test`. Expected: PASS. Report any failure in a file you did not touch without fixing it.
- [ ] **Step 5: Commit** as `feat(archive): archive a session with its descendants from the dialog`.
