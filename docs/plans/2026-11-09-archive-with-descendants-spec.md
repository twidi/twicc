# Archive a session with its descendants

Status: spec, not implemented.

## Intent

Today, archiving a session archives only that session. Its spawned sessions stay in the sidebar. In the tree display, they can show a `↰ from <parent>` hint. After an orchestration ends, the user must archive every child by hand.

With this feature, archiving a session that has descendants opens one dialog. The dialog shows the descendant tree. The sessions that are safe to archive are already checked. The user confirms in one click, or changes the selection.

## Goals

- Archive a session and its finished descendants in one gesture.
- Never stop running work without an explicit choice of the user.
- Keep the plain archive with no extra step when no descendant is concerned.

## Non-goals

- **Unarchive** stays per session. It never cascades.
- **Later effects.** The archive happens once, at confirmation. Nothing happens afterwards to the sessions that were left alone, even when their work ends.
- **Date-based bulk archive** (`POST /api/sessions/bulk-archive/`) does not change.
- **Sidebar multi-select archive** does not change.
- **CLI and MCP archive commands** do not change. They keep archiving only the sessions they name.

## Definitions

- **Descendant:** a session reachable through `spawned_by` links, at any depth. Provider subagents are not descendants.
- **Target:** the session the user asks to archive.
- **Detached session:** a session with `tree_detached` set.
- **Excluded branch:** a detached descendant and its whole subtree. See "Which sessions are concerned".
- **Barrier:** a descendant that the dialog does not check automatically. A session is a barrier if at least one of these is true:
  - it is in `starting`, `assistant_turn` or `awaiting_user_input`;
  - it has a running background shell, subagent, Monitor, workflow, pending scheduled wake-up or goal, whatever its state;
  - it has an active cron, whatever its state. This includes a dead session whose crons are kept for an automatic restart (a TwiCC shutdown, an `apply-settings` or a crash keeps them; see `should_keep_dead_process_run` in `src/twicc/providers/claude_code/helpers.py`);
  - it is pinned.

## Which sessions are concerned

The walk starts at the target and goes down the descendant tree.

| Descendant | Listed in the dialog | Behaviour |
|---|---|---|
| Detached | No | The walk stops. The session and its whole subtree are ignored, hidden sessions included. |
| Already archived | No | The walk goes through it. Its descendants attach to the nearest listed ancestor. |
| Hidden | No | The walk goes through it. Its descendants attach to the nearest listed ancestor. See "Hidden sessions". |
| Any other | Yes | One row with a checkbox. |

The tree has no display dependency: the feature behaves the same with the "show sessions as tree" setting on or off.

## When the dialog opens

The dialog opens if the target has at least one listed descendant. This applies to every single-session archive gesture of the UI: the session list menu, the session header, the session view action and the session toast. All of them use `stopSessionProcess(id, { archive: true })` in `frontend/src/composables/useStopSessionProcess.js`.

If there is no listed descendant, the target's archive behaves exactly as today, including today's crons and background shells confirmation. Its hidden descendants follow "Hidden sessions". Cancelling today's confirmation archives nothing.

The dialog replaces today's crons and background shells confirmation (`StopProcessConfirmDialog.vue`) when it opens. The user never sees two dialogs for one archive.

## Dialog content

### Title

`Archive "<target title>"?`

### Warning

A warning callout appears when the current selection would lose something. It lists the totals for the target and all checked sessions:

- running processes that will be stopped: only sessions with work in progress, meaning the first two conditions of the barrier definition;
- background shells that will be killed;
- active crons that will be cancelled, with today's explanation that they are not restored.

The totals follow each checkbox change. The warning is absent when the totals are all zero. The sentences of `batchStopConfirmationMessage` in `frontend/src/utils/backgroundWork.js` are the reference wording.

### Tree

- The target is not a row: it is in the title.
- Direct children of the target are at the first level, with no guide line. Deeper levels use the same guide lines and elbows as the sidebar tree.
- Each row is one line: checkbox, pin icon if pinned, title, process indicator.
- The title never wraps. A title that does not fit scrolls like the session titles in the sidebar (`ScrollableText`).
- The process indicator is the one of the sidebar (`ProcessIndicator`). It stays live while the dialog is open. There is no text tag for the state.
- The list has a maximum height and scrolls. It shows the scroll-edge shadow (`v-scroll-shadow`).
- A **Select all** control checks every row, barriers included. When every row is checked, it unchecks every row.

### Checkbox rules

| Moment | Effect |
|---|---|
| Dialog opens | Every listed descendant is checked, except barriers and everything below a barrier. |
| User checks a row | The row is checked. Its descendants are checked, and the descent stops at each barrier. A barrier met during this descent stays as it was, and so do the rows below it. |
| User unchecks a row | The row and its whole subtree are unchecked. |
| Any time | Rows are independent. A child can be checked when its parent is not. |

The user can check a barrier. The barrier is then archived. If it has work in progress or crons, they are stopped, and the warning shows it. A barrier that is only pinned has nothing to stop.

### Buttons

The footer has three buttons. When the row does not fit, the buttons wrap, and the primary button stays right-aligned.

| Button | Label | Action |
|---|---|---|
| Cancel | `Cancel` | Nothing is archived. |
| Secondary | `Archive this session only` | Archives the target only: it ignores the checkboxes, and no hidden session is archived. Not shown with zero checked rows. |
| Primary | `Archive with N descendants` | Archives the target, the checked rows and the eligible hidden sessions. With zero checked rows: `Archive this session only`, with the same effect as the secondary button. |

When the warning is visible, the primary label starts with `Stop and archive` instead of `Archive`, and the primary button uses the danger variant. The secondary label starts with `Stop and archive`, and `Cancel` becomes `Cancel, keep the process`, only if the warning concerns the target.

`N` counts checked rows only. It does not count hidden sessions.

## Hidden sessions

Hidden sessions never appear in the dialog, in the warning or in `N`.

- A hidden session is archived if it is not a barrier, there is no hidden barrier between it and its nearest listed ancestor, and that ancestor is archived by the confirmation. When the nearest listed ancestor is the target, this requires the `with N descendants` action, or an archive with no listed descendant, with or without today's crons and background shells confirmation. `Archive this session only` archives no hidden session.
- A hidden barrier is never archived by this feature. It also behaves as a barrier for the walk: the rows below it open unchecked.
- A session with an active cron is a barrier even when hidden: archiving it would cancel a cron that the user cannot see.

## Archive behaviour

The target and each listed session that is archived go through the existing single-session archive path: stop its process if any, then the `archived` update. Hidden sessions need a backend path (see "Data needs").

Two requirements are new compared with today:

- `autoUnpinOnArchive` applies to a checked pinned session, also when the session is not loaded in the frontend store.
- Each session succeeds or fails alone. A failure is reported to the user and does not undo the others.

A session that the frontend has not loaded must still be archived. The dialog must not depend on the store knowing the descendants: the list of sessions is paginated, and hidden sessions are never in the store.

## When the state is read

| Element | Moment |
|---|---|
| Checkboxes at opening | Dialog opening. They do not change on their own afterwards. |
| Barrier status for the checkboxes (opening and check descent) | The opening read. |
| Process indicators and warning totals | Live for the target and the listed sessions, as far as the frontend knows them. Crons of a session with no live process come from the opening read. |
| Hidden sessions: archive decision | At the confirmation, or at the archive when this dialog does not open. On the backend: the frontend has no live state for them. |

The confirmation applies the checkboxes as they are.

## Data needs

The existing endpoint `GET /api/projects/<id>/sessions/<session_id>/topology/` (`session_topology` in `src/twicc/views.py`) returns the whole spawn tree, with hidden, archived and detached sessions, and with each node's state and background work. The dialog uses it.

**Backend additions:**

1. The topology node does not expose crons, and the frontend store does not have them for hidden or dead sessions. Each node gets its active cron count. The `twicc topology` CLI shares this engine: its documentation follows the change.
2. The session `PATCH` route refuses hidden sessions (`_resolve_session_or_404`). The archive needs a backend path that archives the eligible hidden sessions of a target, at the confirmation, or at the archive when this dialog does not open. The backend applies the "Hidden sessions" rules itself.

## Loading and errors

- Archiving a target with possible descendants first reads the tree. The UI shows that the action is in progress.
- A target known to have no spawned session does not trigger this read.
- If the read fails, the dialog offers an error message, `Cancel` and `Archive this session only`. That action archives no hidden session. The warning and label rules still apply to the target, from its live state.

## Internationalization

All new text goes through i18n. See `docs/internationalization.md`.

## Decisions to confirm

These were proposed during the discussion. The user did not state them explicitly:

- The feature does not depend on the "show sessions as tree" setting.
- Sidebar multi-select archive does not cascade.
- CLI and MCP archive commands do not cascade.
- Select all also checks barriers, and it toggles to uncheck.
- Unchecking a row unchecks its subtree.
- `Archive this session only` archives no hidden session.
