# Multi-Terminal Support

**Date:** 2026-04-03
**Status:** DRAFT

## Overview

Allow users to open multiple independent terminals within a single session. Each terminal runs its own PTY (and its own tmux session when tmux is enabled). The user navigates between terminals via tabs integrated into the terminal toolbar. Only one terminal is visible at a time.

## Concepts

### Terminal Index

Each terminal within a session is identified by a numeric **index**. The index is assigned sequentially at creation time and never reused or renumbered within a page session.

- Index `0` is the **main terminal**. It is always present — its tab cannot be removed.
- Indices `1`, `2`, `3`, … are **secondary terminals** created by the user via a `[+]` button.

The label displayed on each tab is derived from the index: the main terminal shows "Main", and secondary terminals show "Term N" (e.g., "Term 2", "Term 3"). No custom naming for now (see "Deferred" section).

### One Visible, One Active

Only one terminal is visible at a time (the selected tab). That terminal is the **active terminal**: it receives all input from the ExtraKeysBar.

Each terminal has its own toolbar displaying state specific to that terminal (scroll position, selection, connection status). When the user switches tabs, the entire view (toolbar + xterm.js area) swaps to the newly selected terminal.

### Independence

Each terminal is fully independent:

- Its own xterm.js instance
- Its own WebSocket connection to the backend
- Its own PTY process (or its own tmux session)
- Its own connection state (connected/disconnected)
- Its own scroll position, selection, alternate-screen state

There is no shared state between terminals except the ExtraKeysBar (which routes input to whichever terminal is currently active) and the terminal config (combos/snippets, which is already global).

## UI

### Tab Bar

Terminal tabs and action buttons share a **single toolbar bar**. The left side uses a `wa-tab-group` (for its built-in scrollable overflow behavior), with the body/panels hidden — only the nav is rendered. Terminal panels are managed separately with `v-show`.

The tab bar portion contains:

```
[Main] [Term 2] [Term 3] [+]       ↑↓  Copy  Paste  ✕
└──── scrollable tab nav ────┘       └── action buttons ──┘
```

- **`[Main]`** — Always present, always first. Cannot be removed.
- **`[Term N]`** — One tab per secondary terminal, in creation order.
- **`[+]`** — A button at the end of the tab row to create a new terminal. Always visible.

The `[+]` button is available regardless of tmux mode. Even without tmux, having multiple terminals within a single work session is useful — they just won't persist across page refreshes.

### Toolbar (Shared, Single Bar)

The tab bar and action buttons are merged into a **single toolbar bar**:

- **Left side**: terminal tabs (using `wa-tab-group` for its scrollable nav) + `[+]` button
- **Right side**: action buttons — scroll to top/bottom, touch mode toggle, copy, paste, disconnect/kill

The action buttons on the right reflect the state of the **active terminal**. When switching tabs, the buttons update automatically (driven by the active terminal's reactive state via `provide`/`inject`). There is no per-terminal toolbar duplication — one shared toolbar, one set of buttons.

### ExtraKeysBar (Shared)

The ExtraKeysBar already exists in the current single-terminal implementation. It remains a single shared instance, displayed below all terminal content. It does not change when switching terminals. It emits events (key input, modifier toggles, paste, combos, snippets) which are routed by the parent to whichever terminal is currently active.

The rationale: the ExtraKeysBar has no terminal-dependent state. It only emits actions. The modifiers (Ctrl, Alt, Shift) are one-shot or locked states that apply conceptually to the next keypress, regardless of which terminal receives it.

### Creating a Terminal

The user clicks the `[+]` button. A new tab appears with the next sequential label ("Term 2", "Term 3", …). The new tab is automatically selected (becomes active). The terminal connects immediately.

The index counter increments monotonically and is never decremented, even when terminals are closed. If the user creates Term 2, Term 3, closes Term 2, then creates another, the new one is Term 4 (not a new Term 2).

### Closing a Terminal

**Main terminal (index 0):**
- The Disconnect button disconnects the WebSocket and shows the reconnect overlay, but the tab remains. The user can reconnect.
- The tab cannot be closed or removed. It is permanent.

**Secondary terminals (index ≥ 1):**
- The Disconnect button kills the terminal: closes the WebSocket, removes the tab, and destroys the component.
- If the closed terminal was the active tab, the previous tab becomes active (or the main terminal if there's no previous tab).
- If the terminal was running in tmux mode, the underlying tmux session is killed on disconnect (the user explicitly chose to close this terminal, so we clean up).

No renumbering occurs when a terminal is closed. If "Term 2" is closed, "Term 3" keeps its label.

## tmux Strategy

### One tmux Session Per Terminal

Each terminal gets its own independent tmux session on the backend. The naming convention:

| Terminal | tmux session name |
|----------|------------------|
| Main (index 0) | `twicc-<session_id>` (unchanged from current behavior) |
| Index N (N ≥ 1) | `twicc-<session_id>__<N>` |

The double underscore `__` separator is chosen because single underscores already appear in session names (from dot/colon replacement in `tmux_session_name()`).

All tmux sessions use the existing dedicated socket (`-L twicc`) for isolation from the user's own tmux environment.

### tmux Mode Rules

Each terminal (main or secondary) follows the same rules as the current single-terminal implementation:

| Condition | tmux? |
|-----------|-------|
| Draft session | No |
| Archived session (no existing tmux session) | No |
| Non-draft, non-archived, tmux setting enabled | Yes |
| Non-draft, non-archived, tmux setting disabled | No |

There is no per-terminal tmux override. All terminals in a session share the same tmux/non-tmux mode.

## Discovery: tmux as Source of Truth

### Problem

When the user refreshes the page, switches devices, or reopens a session, the frontend needs to know which terminals exist. Without tmux, terminals are ephemeral (they die with the WebSocket), so only the main terminal needs to be shown. But with tmux, terminals persist on the server — we need to rediscover them.

### Solution

The backend queries tmux to discover all existing sessions for a given Claude session:

```
tmux -L twicc list-sessions -F "#{session_name}"
```

This returns all active tmux session names. The backend filters by prefix (`twicc-<normalized_session_id>`) and parses the terminal indices:

- `twicc-<id>` → index 0
- `twicc-<id>__N` → index N

The frontend receives the list of existing terminal indices and creates a tab for each one.

### No Database Storage

Terminal existence is not stored in the database. tmux is the source of truth. This means:

- Zero schema changes
- Zero sync complexity
- Cross-device: any browser connecting to the same backend sees the same terminals (tmux sessions are server-side)
- If a tmux session is killed externally (e.g., `tmux kill-session`), it simply disappears from the list on next query

### Cross-Device Synchronization

When a terminal is created or killed, other connected clients (other browser tabs, other devices) must be notified so their tab bar stays in sync. The backend broadcasts WebSocket messages through the existing main app WebSocket (not the terminal WebSocket) to all connected clients:

- **Terminal created**: When a new terminal WebSocket connection is established and the PTY is spawned, the backend broadcasts a message indicating a new terminal exists for that session (with the session ID and terminal index).
- **Terminal killed**: When a secondary terminal is explicitly disconnected (killed) by the user, the backend broadcasts a message indicating that terminal no longer exists.

On receiving these messages, other clients add or remove the corresponding tab. This keeps all clients in sync without polling.

Note: this mechanism is only useful when tmux is enabled (persistent terminals). Without tmux, terminals are local to a single browser session and there is nothing to sync. However, the broadcast is sent regardless of tmux mode for simplicity — non-tmux clients can simply ignore terminal notifications for sessions they don't have open.

### Non-tmux Fallback

When tmux is disabled (draft sessions, or setting off), discovery returns no results. The frontend shows only the main terminal tab. The `[+]` button still works — it creates ephemeral terminals that won't survive a page refresh.

## Archive Cleanup

When a session is archived, all tmux sessions belonging to it must be killed. The current code kills a single tmux session (`twicc-<session_id>`). The new behavior:

1. List all tmux sessions on the `twicc` socket
2. Filter by prefix `twicc-<normalized_session_id>` (exact match or `__` suffix)
3. Kill each matching session

This is more robust than the current approach: instead of guessing which sessions might exist, we discover and clean up everything. No terminal can be orphaned.

## WebSocket URL

The WebSocket URL gains an additional path segment for the terminal index:

```
/ws/terminal/<project_id>/<session_id>/<terminal_index>/
```

Examples:
- `/ws/terminal/proj-abc/sess-123/0/` — main terminal
- `/ws/terminal/proj-abc/sess-123/3/` — terminal 3

The backend uses `terminal_index` to derive the tmux session name (for tmux mode) or to spawn an independent PTY (for non-tmux mode).

## Deferred (Out of Scope)

The following features are explicitly deferred for later:

- **Tab renaming**: Letting the user rename "Term 3" to "Build server". Would require storage (likely a JSON field on the Session model or a separate config).
- **Tab reordering**: Drag-and-drop to rearrange tabs. Same storage requirement.
- **Maximum terminal count**: No limit is enforced for now. Could add one later if needed.
- **Per-terminal tmux toggle**: All terminals follow the same tmux mode. A per-terminal override is not planned.
