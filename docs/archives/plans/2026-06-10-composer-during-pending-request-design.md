# Composer access during a pending request — design

**Date:** 2026-06-10
**Status:** Design (awaiting approval)

## Problem

When an agent raises a pending request (tool approval, `ask_user_question`, Codex
approval), the `.session-footer` swaps the `MessageInput` composer out for the
`PendingRequestForm`. The two are mutually exclusive branches of a `v-if /
v-else-if` cascade in
`frontend/src/components/session/detail/SessionItemsList.vue`, so the composer is
**unmounted** for as long as the request is pending.

Consequences for the user:

- The editing surface vanishes mid-thought: they cannot keep preparing a message
  while the request is up.
- State that is not yet persisted is lost on the swap: attachments still being
  encoded, changed-but-not-applied agent settings, caret/focus. (The draft *text*
  itself survives — it is persisted to the store/IndexedDB on every keystroke and
  restored on remount — but the surface and the in-flight state are gone.)

## Goal

While a pending request is active on a **main** session, the composer stays
accessible and fully usable for **preparing** a message, but **sending is
blocked**. The pending request remains the primary, action-required element. The
two share the footer as two stacked collapsible panels with a single rule: **at
most one is expanded at a time**. Opening one **reduces** the other to its
single-line bar; **reducing** a panel leaves the other untouched (so both can be
reduced at once). They never both occupy the full footer.

When the request is resolved, the composer returns to normal with the prepared
message and attachments intact, ready to send.

## Non-goals

- Subagent sessions keep the standalone `PendingRequestForm` with no composer
  (subagents have no composer today — unchanged).
- Stale-session and provider-disabled banners are untouched.
- No change to how a response is submitted to the agent.

## Design

### 1. Keep `MessageInput` mounted on main sessions

Root cause of the "everything disappears" problem: `MessageInput` is unmounted
during a pending request. Fix: on a main session (`!parentSessionId`),
`MessageInput` is **always rendered**, whether or not a request is pending. It no
longer unmounts when `hasPendingRequest` flips in either direction, so all
in-flight, not-yet-persisted state survives both the appearance and the
resolution of the request.

The `.session-footer` cascade becomes:

```
v-if      isStale && !parentSessionId            → stale banner            (unchanged)
v-else-if !isProviderEnabled && !parentSessionId → provider-disabled banner (unchanged)
v-else (active footer):
    PendingRequestForm   v-if="hasPendingRequest"     (stacked when !parentSessionId)
    MessageInput         v-if="!parentSessionId"      (sendingLocked when hasPendingRequest)
```

Resulting matrix:

| Session   | Pending? | Footer contents                                            |
|-----------|----------|------------------------------------------------------------|
| main      | no       | `MessageInput` (normal — unchanged)                        |
| main      | yes      | `PendingRequestForm` + `MessageInput` (locked) — *stacked* |
| subagent  | no       | nothing (unchanged)                                        |
| subagent  | yes      | `PendingRequestForm` only (standalone — unchanged)        |

"Stacked mode" ≝ `hasPendingRequest && !parentSessionId`.

### 2. Two stacked panels — "at most one expanded"

Each panel owns its own reduced state; the parent coordinates only the
**open-reduces-the-other** direction.

- `PendingRequestForm` keeps its internal `viewState` (`normal` / `minimized` /
  `maximized`); `minimize()` reduces it, `restore()` expands it.
- `MessageInput` keeps its own internal `collapsed`; `collapse()` reduces it,
  `expand()` expands it.

Both reduced states render through one shared **`CollapsedBar`** component
(`frontend/src/components/message/CollapsedBar.vue`) so they look and hover
identically: a single clickable line (icon + label + chevron expand button).
Clicking anywhere on the line — or the chevron — expands that panel.

Coordination (in `SessionItemsList`, the common parent):

- when a panel is **opened** it emits `expand`; the parent reduces the other panel
  (`MessageInput.collapse()` / `PendingRequestForm.minimize()`). Reduce methods do
  not emit, so there is no feedback loop and no "reduce reopens the other".
- when a request **appears**, the composer collapses on the `sendingLocked`
  transition; when it **resolves**, the composer is restored. A *new* request
  replacing a previous one also reduces the composer (so the invariant holds when
  the composer was expanded while the prior request sat minimized).

`PendingRequestForm`'s **maximize** stays an internal overlay (`position:absolute;
inset:0`) and carries a `z-index` so it covers the always-mounted composer.

### 3. Locked composer (`sendingLocked`)

When `MessageInput` receives `sendingLocked = true`:

- The **Send / Apply-settings button is hidden** (one button carries both
  actions) and replaced by a compact, non-interactive indicator: a lock icon +
  `Sending paused`, with a tooltip `Answer the pending request to send`.
- Keyboard send (`⌘/Ctrl + ↵`) is **guarded off** (`handleSend` early-returns).
- The **Reset** button stays available (clearing the in-progress draft/settings
  is harmless).
- Everything else stays fully functional: typing, `@` file paths, `/` commands,
  `!` history, attachments, agent-settings dropdowns, snippets, draft autosave.
- A **hairline separator** (top border) sits above the locked composer so it reads
  as distinct from the request stacked above it (which itself sits under its own
  `wa-divider` below the conversation).
- The agent-settings popover's "click X to apply" hint switches to "your changes
  are saved and will apply once you answer the pending request".

When `hasPendingRequest` flips to false, the composer returns to a normal full
composer (prepared text + attachments + changed settings preserved, since it never
unmounted), Send reappears.

## Affected files

- `frontend/src/components/message/CollapsedBar.vue` — **new** shared single-line
  bar (icon + label + chevron, clickable) used by both reduced panels.
- `frontend/src/components/session/detail/SessionItemsList.vue` — restructure the
  `.session-footer` active branch to render both components on a main session (no
  coordination state).
- `frontend/src/components/message/MessageInput.vue` — add `sendingLocked` prop;
  use `CollapsedBar` for the collapsed bar; default-collapse on the lock
  transition; hide Send/Apply + locked indicator; guard `handleSend`; top-border
  separator; pass `sendingLocked` to the settings popover.
- `frontend/src/components/message/PendingRequestForm.vue` — render the minimized
  state as a `CollapsedBar` (body kept mounted, hidden by CSS); independent
  `minimize`/`restore`; `z-index` on the maximized overlay.
- `frontend/src/components/message/AgentSettingsPopover.vue` — `sendingLocked` prop
  to adapt the apply hint.
- `frontend/src/utils/focusChat.js` — don't auto-expand the composer when a
  (non-minimized) request is the primary target; treat a minimized request as a
  non-target so focus falls to the composer.
- `frontend/src/views/SessionView.vue` — command-palette collapse/expand act on the
  composer alone (no longer assume the composer is unmounted during a request).

## Edge cases

- **Maximize:** the maximized request overlays the whole session area (covering the
  composer, via `z-index`); restoring returns to its prior size.
- **Body state across minimize/restore:** the request's per-provider body stays
  mounted (hidden by CSS) while minimized, so an in-progress deny reason / question
  selection / edit mode survives.
- **Multiple parallel requests:** when one resolves and the next takes the slot,
  the request resets to normal size (`viewState` → `normal`).
- **Focus routing:** when a request is shown at normal size it is the primary focus
  target (Approve button, …) and the composer is left collapsed; when the request
  is minimized, focus falls through to the composer textarea.
