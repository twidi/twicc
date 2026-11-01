# Unified stop-session-process and triple-Escape shortcut — design

**Date:** 2026-04-24
**Status:** Proposed

## Problem

The frontend stops a session's Claude process from two places today:

- `SessionHeader.vue` — the **Stop** button in the session header.
- `SessionListItem.vue` — two dropdown items in the sidebar session menu: **Stop the Claude Code process** and **Archive…** (which, when the process is running, also stops it).

Each call site independently implements the same logic:

- a local `stoppingProcess = ref(false)` flag, shown as a spinner / disabled state,
- a `watch(canStop, …)` to reset the flag when the process actually dies,
- a local `<StopProcessConfirmDialog>` instance (and associated `ref` + `handleStopConfirm`) for the "active crons" confirmation path,
- an inlined `if (hasActiveCrons) open dialog else killProcess(…)` branch.

This duplication has concrete consequences:

1. **No synchronization between the two UIs.** Clicking Stop in the sidebar does not reflect a "stopping" state in the header's button (and vice versa), because each component owns its own flag.
2. **Two `<StopProcessConfirmDialog>` components** are mounted in the DOM for any session that is visible both in the sidebar and in the header — wasteful and confusing.
3. **Any change to the stop flow** (new confirmation condition, extra cleanup, analytics) must be done in multiple places, with the risk of divergence.

Separately, the user has asked for a **triple-Escape global shortcut** to quickly stop the process of the session currently being viewed, as an "emergency" gesture when an agent goes off the rails. Adding this as a third independent call site is unacceptable.

## Goals

1. Centralize the "stop a session's process" flow in a single composable so the three call sites (header, sidebar, triple-Escape) share identical behavior.
2. Lift the `stopping` flag to the store so the header and sidebar stay synchronized.
3. Mount the `StopProcessConfirmDialog` once, at the app level, driven by the centralized composable.
4. Add a triple-Escape global shortcut that only fires on the chat view of a session and delegates to the centralized composable.

## Non-goals

- **Agent stopping** (`stoppingAgent` in `SessionHeader.vue` and `ToolUseContent.vue`) is out of scope. Agents are scoped to a session and their stop flow is unrelated. It stays as is.
- Backend changes. The `kill_process` WebSocket message, the `active_crons` payload, and the `setSessionArchived` behavior are untouched.
- Any change to `StopProcessConfirmDialog`'s UI or its emitted payload (`{ mode: 'stop' | 'archive' }`).

## Design

### 1. `stopping` flag in the store

Add a per-session boolean flag inside the existing `processStates` map in `stores/data.js`.

**State shape change:** each entry in `processStates[sessionId]` gains an optional `stopping` field. No new top-level key in the store.

**Two new store actions:**

- `setSessionStopping(sessionId)` — sets `stopping = true` on `processStates[sessionId]`. No-op if no entry exists (defensive).
- An internal reset path: whenever `processStates[sessionId]` is removed, replaced, or transitions to `DEAD`, the `stopping` flag naturally disappears with the entry (since the entry is recreated or deleted). The component-level `watch(canStop, …)` pattern that currently resets the flag becomes unnecessary because `canStop` being false means the underlying state has already changed.

**Getter:** `isSessionStopping(sessionId)` — returns `state.processStates[sessionId]?.stopping === true`.

**Why in `processStates` and not a separate map:** the flag's lifetime is strictly tied to a live process's lifetime. Co-locating it avoids a parallel map that must be kept in sync with process births/deaths.

### 2. `useStopSessionProcess` composable

New file: `frontend/src/composables/useStopSessionProcess.js`.

Module-scoped (singleton) reactive state:

```js
// null when no confirmation is pending; otherwise the pending request
const pendingConfirmation = ref(null)
// Shape: { sessionId, mode: 'stop' | 'archive', cronCount: number }
```

Exported API:

```js
export function useStopSessionProcess() {
    return {
        stopSessionProcess,  // (sessionId, { archive = false } = {}) => void
        pendingConfirmation, // Ref, consumed only by the global dialog wrapper
        confirmPendingStop,  // ({ mode }) => void — called by the global dialog
        cancelPendingStop,   // () => void — called by the global dialog on dismiss
    }
}
```

**`stopSessionProcess(sessionId, { archive = false } = {})` semantics:**

1. Read `processState = store.getProcessState(sessionId)`.
2. Compute `canStop = processState && !processState.synthetic && processState.state && processState.state !== PROCESS_STATE.DEAD`.
3. Branch on `archive`:
   - If `archive === true` **and** `canStop === false`: there is no running process, just archive. Call `store.setSessionArchived(projectId, sessionId, true)`. Return.
   - If `archive === true` **and** `canStop === true` **and** `hasActiveCrons`: open confirmation with `mode: 'archive'`.
   - If `archive === true` **and** `canStop === true` **and** no crons: set stopping flag, `killProcess(sessionId)`, archive. Return.
   - If `archive === false` **and** `canStop === false`: return (nothing to do).
   - If `archive === false` **and** `canStop === true` **and** `hasActiveCrons`: open confirmation with `mode: 'stop'`.
   - If `archive === false` **and** `canStop === true` **and** no crons: set stopping flag, `killProcess(sessionId)`. Return.
4. When the stopping flag needs to be set, check first whether it's already set — if yes, the call is a no-op (debounces re-entrant calls).

**"Open confirmation"** = set `pendingConfirmation.value = { sessionId, mode, cronCount }`. A single `<StopProcessConfirmDialog>` mounted at the app level watches this ref and opens itself accordingly.

**`confirmPendingStop({ mode })`:**

1. Read current `pendingConfirmation.value` to get `sessionId`.
2. Clear `pendingConfirmation.value = null`.
3. Apply the actual stop: set stopping flag, `killProcess(sessionId)`, and if `mode === 'archive'`, archive.

**`cancelPendingStop()`:** just clears `pendingConfirmation.value = null`.

**Why a composable (vs. a utility function):** because we need a module-scoped reactive ref (`pendingConfirmation`) that is shared across all callers and consumed by the global dialog wrapper. A plain utility function cannot expose reactive state idiomatically.

### 3. Global `<StopProcessConfirmDialog>` mount

Move the component instantiation from `SessionHeader.vue` and `SessionListItem.vue` into `App.vue`.

In `App.vue`:

```vue
<StopProcessConfirmDialog
    :open="pendingConfirmation !== null"
    :mode="pendingConfirmation?.mode"
    :cron-count="pendingConfirmation?.cronCount"
    @confirm="confirmPendingStop"
    @cancel="cancelPendingStop"
/>
```

The existing `StopProcessConfirmDialog` API is `ref.open({ mode, cronCount })` + emits `confirm`. It needs a small refactor to be driven by props instead of an imperative `open()` call:

- Accept `open: boolean`, `mode: 'stop' | 'archive'`, `cronCount: number` as props.
- Expose `@cancel` in addition to `@confirm` so the composable can clear its pending state if the user dismisses.
- Internally the dialog still controls its own `wa-dialog` open state, but mirrors the `open` prop via a watcher.

This is the smallest invasive change. The alternative (keep the imperative API and call `open()` from a watcher in `App.vue`) works too; props-driven is cleaner and Vue-idiomatic.

### 4. Migrate call sites

**`SessionHeader.vue`:**
- Remove `stoppingProcess = ref(false)` and its `watch`.
- Remove the `<StopProcessConfirmDialog>` instance and its `ref`, and the `handleStopConfirm` function.
- Replace `handleStopProcess()` body with a call to `stopSessionProcess(props.sessionId)`.
- Bind the button's `:loading` / `:disabled` to `store.isSessionStopping(sessionId)` (via a computed).

**`SessionListItem.vue`:**
- Remove `stoppingProcess`, its `watch`, the `<StopProcessConfirmDialog>` instance, and `handleStopConfirm`.
- Replace the `stop` branch with `stopSessionProcess(session.id)`.
- Replace the `archive` branch (when process is running or not) with `stopSessionProcess(session.id, { archive: true })`.
- Bind the dropdown item's `:disabled` and label to `store.isSessionStopping(sessionId)`.

After migration, **no component** except `App.vue` and the composable should mention `killProcess` for session stopping. (Agent-stopping paths still use `killProcess` — that's fine, they're different flows.)

### 5. Triple-Escape shortcut

Add handling inside the existing `handleGlobalKeydown` in `App.vue` (consistent with the current pattern for global shortcuts). If the logic grows, it can be extracted to a small composable, but one `if` block is sufficient.

**Constants** (top of `App.vue` script section):

```js
const TRIPLE_ESCAPE_WINDOW_MS = 200  // max gap between consecutive Escape presses
const TRIPLE_ESCAPE_COOLDOWN_MS = 1000  // after a trigger, ignore Escape for this long
```

Reuse the existing `SESSION_CHAT_ROUTES` constant already defined in `App.vue` (line 74) to restrict the shortcut to the chat view. Do **not** introduce a second near-duplicate name.

**State** (module-scoped in `App.vue`):

```js
let escapeTimestamps = []  // rolling array of recent Escape timestamps
let lastTriggerAt = 0
```

**Handler logic** (added to `handleGlobalKeydown`):

```js
if (event.key === 'Escape' && !event.repeat) {
    // Never stop propagation — let overlays close naturally.
    const now = performance.now()

    // Cooldown check
    if (now - lastTriggerAt < TRIPLE_ESCAPE_COOLDOWN_MS) return

    // Route check: only on the chat view of a session
    if (!SESSION_CHAT_ROUTES.has(route.name)) {
        escapeTimestamps = []
        return
    }
    const sessionId = route.params.sessionId
    if (!sessionId) return

    // Process-running check
    const ps = store.getProcessState(sessionId)
    const canStop = ps && !ps.synthetic && ps.state && ps.state !== PROCESS_STATE.DEAD
    if (!canStop) {
        escapeTimestamps = []
        return
    }

    // Track timestamps, pruning entries older than the window
    escapeTimestamps = escapeTimestamps.filter(t => now - t < TRIPLE_ESCAPE_WINDOW_MS)
    escapeTimestamps.push(now)

    if (escapeTimestamps.length >= 3) {
        lastTriggerAt = now
        escapeTimestamps = []
        stopSessionProcess(sessionId)
    }
}
```

**Key properties:**

- **Capture phase, no `preventDefault`, no `stopPropagation`.** Existing Escape handlers on overlays (dialogs, popovers, pickers) continue to work; if the first or second Escape closes a dialog, that's fine — an emergency triple-Escape closing a dialog on the way is acceptable.
- **`event.repeat` is ignored** to prevent held-key auto-repeat from triggering.
- **Scope restricted to the chat view** of a session (route names `session` and `projects-session` only). Sub-routes `-files`, `-git`, `-terminal`, `-subagent` are excluded, which also neutralizes the terminal-view concern (xterm's Escape usage is not affected because we never activate there).
- **Rolling window:** any three consecutive Escape presses with less than 200 ms between each consecutive pair triggers. A user who mashes Escape four or five times still triggers exactly once (thanks to the cooldown).
- **1 s cooldown** after a trigger prevents a spam of Escape from firing multiple stops or multiple confirmation dialogs.
- **Crons path respected:** `stopSessionProcess` opens the confirmation dialog if crons are active. The shortcut does not bypass it. This is consistent with the button behavior; the user explicitly accepted this trade-off for urgency vs. safety.

## Data flow summary

```
User action (button / dropdown / triple Escape)
    │
    ▼
stopSessionProcess(sessionId, options)
    │
    ├─ no process running ──► return (or archive-only)
    │
    ├─ crons active ──► pendingConfirmation.value = { … }
    │                       │
    │                       ▼
    │                   Dialog opens (App.vue)
    │                       │
    │                       ├─ confirm ──► confirmPendingStop({ mode })
    │                       │                   │
    │                       │                   ▼
    │                       │               setSessionStopping + killProcess [+ archive]
    │                       │
    │                       └─ cancel ───► cancelPendingStop()
    │
    └─ no crons ──► setSessionStopping + killProcess [+ archive]
```

All three UI entry points (header, sidebar, shortcut) feed the top of this flow with the same function call.

## File-level impact

| File | Change |
|---|---|
| `frontend/src/stores/data.js` | Add `setSessionStopping` action, `isSessionStopping` getter. |
| `frontend/src/composables/useStopSessionProcess.js` | **New.** Centralized composable. |
| `frontend/src/components/StopProcessConfirmDialog.vue` | Refactor to props-driven API (`open`, `mode`, `cronCount`) + emit `cancel`. |
| `frontend/src/App.vue` | Mount global `<StopProcessConfirmDialog>`; wire `pendingConfirmation`; add triple-Escape handler in `handleGlobalKeydown`. |
| `frontend/src/components/SessionHeader.vue` | Remove local stop machinery; call composable. |
| `frontend/src/components/SessionListItem.vue` | Remove local stop machinery; call composable. |

No backend change. No migration. No new dependency.

## Error handling & edge cases

- **Re-entrant calls:** `stopSessionProcess` checks `isSessionStopping` at the top of the "execute stop" branch and no-ops if already stopping. Covers rapid double-trigger (e.g., user clicks button and presses triple-Escape nearly simultaneously).
- **Session changes mid-confirmation:** if the user navigates away while the confirmation dialog is open, the dialog can stay open — `confirmPendingStop` uses the `sessionId` captured in `pendingConfirmation`, not the current route. Alternatively the dialog can be auto-cancelled on route change; we leave it open since dismissing destructive confirmations implicitly is surprising.
- **Process dies between shortcut trigger and dialog confirm:** `confirmPendingStop` blindly calls `killProcess` — the backend handles the case (no-op if the process is already dead). No extra guard needed.
- **Shortcut during page transition:** if `route.name` briefly doesn't match `CHAT_SESSION_ROUTES`, the Escape is simply ignored and the timestamps reset — acceptable.

## Rollout plan

Four commits, in order, each independently verifiable:

1. **Store flag.** Add `setSessionStopping` / `isSessionStopping` in `data.js`. No callers yet.
2. **Composable + dialog refactor + App.vue mount.** Introduce `useStopSessionProcess`, refactor `StopProcessConfirmDialog` to props-driven, mount it globally in `App.vue`. No call sites migrated yet, so the two existing dialog instances in `SessionHeader` and `SessionListItem` still work as before.
3. **Migrate call sites.** Replace local machinery in `SessionHeader.vue` and `SessionListItem.vue` with composable calls. Remove their local `<StopProcessConfirmDialog>` instances. After this commit, visual behavior must be identical for header and sidebar users; synchronization between the two is now a bonus.
4. **Triple-Escape shortcut.** Add the handler in `App.vue`'s `handleGlobalKeydown`. Only new user-facing feature.

## Open questions

None — all parameters settled during brainstorming:

- Geste: triple Escape, fenêtre 200 ms, cooldown 1 s.
- Scope: chat view uniquement (`session` et `projects-session`).
- Overlays: pas d'interférence, on laisse les Escape se propager.
- Crons: confirmation dialog respectée.
- Agents: hors scope.
- Composable name: `useStopSessionProcess`.
- Dialog piloté par état réactif (option A).
- Flag `stopping` dans `processStates` du store `data.js`.
