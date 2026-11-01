# Session List Multi-Select — Design

**Date:** 2026-06-10
**Status:** Approved by user (design phase)
**Branch / worktree:** `multi-select-sessions`

## Goal

Let users select several sessions in the sidebar session list using widely
recognized mouse conventions (Ctrl/Cmd+click to toggle, Shift+click for
ranges) and apply batch actions (pin, mark read/unread, archive, stop,
delete drafts) through a floating action bar. Users must never be surprised:
outside the explicit multi-select mode, nothing changes.

## Non-goals

- Keyboard-driven selection (arrows + Shift, Ctrl+A). Mouse only for v1.
- Backend bulk endpoints. Batch actions loop over existing per-session
  store actions; no API change.
- Persisting the mode or the selection (ephemeral UI state only).
- Rename in batch (inherently single-session).

## UX

### Explicit multi-select mode

- **Entry:** a new `Select sessions` checkbox item (icon `square-check`) in
  the *Session list options* dropdown at the top of the sidebar
  (`ProjectView.vue`). Checked while the mode is active; selecting it again
  exits the mode.
- **Exit:** the ✕ button in the floating bar, the `Escape` key,
  automatically after a batch action completes successfully, and when the
  project/view changes.
- The mode is not persisted (not part of synced settings).

### Mouse interactions (only while the mode is active)

| Gesture | Behavior |
|---|---|
| Click | Opens the session (unchanged). Does **not** clear the selection. |
| Ctrl/Cmd+click | Toggles the session in the selection; sets the anchor. |
| Shift+click | Selects the range anchor→target, **replacing** the selection (standard Windows/macOS semantics). Sets no new anchor. Without an anchor: selects the clicked item alone and sets it as anchor. |
| Ctrl/Cmd+Shift+click | Adds the range anchor→target to the selection without replacing it. |
| Middle click | Opens in a new tab (unchanged). |

- Ranges follow the **visual order** of the filtered flat list and cross
  block dividers (extra / cross-filter pinned / cross-filter active /
  natural).
- While the mode is active, Ctrl/Cmd+click no longer opens in a new tab
  (middle click still does). Outside the mode, all current behaviors are
  untouched — zero regression.
- Sessions that leave the visible list (filter change, archived away, …)
  are pruned from the selection.

### Rendering

No checkboxes. Selected items get a `session-item-wrapper--selected`
modifier (accent background/border), visually distinct from the active
session (`--active`) and keyboard highlight (`--highlighted`). The
per-item ⋯ menu stays functional during the mode.

### Floating action bar

New `SessionSelectionBar.vue`, floating at the bottom of the session list
container, visible **as soon as the mode is active** (even with an empty
selection, so the exit affordance is always there):

- `N selected` counter.
- `Actions` button (disabled when N = 0) opening a `wa-dropdown`
  (placement top) with the batch actions below.
- `✕` button to exit the mode.

### Batch actions and enablement rules

Disabled actions stay visible but greyed out. The general rule is strict
("enabled only if applicable to every selected session"), with a Gmail-style
exception for read/unread.

| Action | Enabled when… | Execution |
|---|---|---|
| Pin: none / project / workspace / all (checkboxes) | no draft selected; a mode shows checked when **all** selected share it | loop `setSessionPinMode`; clicking a mode applies it to every selected session ("none" = unpin all). No toggle-off on re-click, unlike the per-item menu — with a mixed selection, re-applying the checked-nowhere mode must still converge everyone to it. |
| Mark as read | ≥1 selected session is unread (Gmail style); applied only to the unread ones | loop `markSessionReadState(id, false)` |
| Mark as unread | ≥1 selected session is read; applied only to the read ones | loop `markSessionReadState(id, true)` (+ existing navigate-away behavior if the active session becomes unread) |
| Archive | all selected are non-archived, non-draft | if some have a running process or active crons → **one** aggregated confirmation dialog, then loop stop+archive. Note: `useStopSessionProcess`'s confirmation state is single-session (`pendingConfirmation` is module-scoped); the batch path needs either a confirmation-bypass entry point on the composable or a dedicated batch dialog calling the unconfirmed execution path — never a per-session dialog loop. |
| Unarchive | all selected are archived | loop `setSessionArchived(…, false)` |
| Stop the process | all selected have a stoppable process | loop `stopSessionProcess` |
| Delete drafts | all selected are drafts | one confirmation with count, then loop `deleteDraftSession` |

After a successful batch action: clear the selection and exit the mode.

## Architecture

### New Pinia store — `frontend/src/stores/sessionSelection.js`

```js
state: {
  active: false,        // multi-select mode on/off
  selectedIds: Set,     // session ids
  anchorId: null,       // anchor for Shift+click ranges
}
actions: enter(), exit(), toggle(id), selectRange(ids, { additive }),
         clear(), prune(visibleIds)
getters: count, isSelected(id)
```

A dedicated store avoids prop-drilling through the virtualized scroller
(`ProjectView` → `SessionList` → `VirtualScroller` slot →
`SessionListItem`) and matches existing store patterns. Selection is kept
by id, so virtualization (mount/unmount of rows) has no effect on it.

### Component changes

- **`SessionListItem.vue`** — in `handleClick`, when the mode is active:
  Ctrl/Cmd and/or Shift clicks call `preventDefault()` and dispatch to the
  selection store (emitting an event with the modifiers so `SessionList`
  computes ranges); plain click keeps its current behavior. Applies the
  `--selected` class from the store.
- **`SessionList.vue`** — owns range computation (it knows the visual
  order of the filtered flat array); prunes the selection when the visible
  list changes (watcher); `Escape` exits the mode (takes priority over the
  existing highlight clear).
- **`ProjectView.vue`** — adds the `Select sessions` menu item; mounts
  `SessionSelectionBar`; exits the mode on project change.
- **`SessionSelectionBar.vue`** (new) — counter, Actions dropdown, ✕.
  Computes enablement from the selected session objects (data store) and
  runs the batch loops, reusing existing actions/composables
  (`setSessionPinMode`, `setSessionArchived`, `markSessionReadState`,
  `stopSessionProcess`, `deleteDraftSession`).

### Error handling

Batch loops run per-session through the existing actions, which already
handle optimistic updates and errors individually. A failure on one
session does not abort the rest of the loop. The mode exits after the
batch completes regardless of individual failures (WS broadcasts will
reconcile state).

### Edge cases

- **Search filter change:** mode stays active; selection pruned to the
  sessions still visible.
- **Project/view change:** mode exits, selection cleared.
- **Session disappears live** (archived elsewhere, hidden): pruned by the
  visibility watcher.
- **Escape priority:** Escape is also consumed elsewhere in the sidebar
  (clearing the keyboard highlight in the list, clearing the search field
  in `ProjectView`). Full order: **exit multi-select mode > clear
  highlight > clear search**. One Escape press performs exactly one of
  these.
- **Drafts** can be selected (needed for *Delete drafts*); their presence
  disables pin and archive actions per the rules above.

## Testing

Per project policy, no automated tests. Manual checks: modifier clicks in
and out of the mode, range across block dividers, enablement matrix per
action, pruning on filter change, aggregated archive confirmation, exit
paths (✕, Escape, post-action, project change).
