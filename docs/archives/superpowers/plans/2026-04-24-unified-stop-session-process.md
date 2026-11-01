# Unified stop-session-process and triple-Escape shortcut — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Centralize the "stop a session's process" flow in a single composable consumed by the header stop-button, the sidebar dropdown actions, and a new triple-Escape global shortcut that stops the current chat session's process.

**Architecture:** A new composable `useStopSessionProcess` exposes a single `stopSessionProcess(sessionId, { archive })` function plus module-scoped reactive state (`pendingConfirmation`) that drives a **single** globally-mounted `StopProcessConfirmDialog` in `App.vue`. A `stopping` flag is lifted from each component's local state into `processStates[sessionId].stopping` in the Pinia store so the header and sidebar stay synchronized. The `StopProcessConfirmDialog` refactors from imperative (`ref.open()`) to props-driven (`open`, `mode`, `cronCount`). The triple-Escape shortcut is a new branch inside the existing `handleGlobalKeydown` in `App.vue`, scoped to chat routes (`session`, `projects-session`), with a 200 ms inter-key window and 1 s post-trigger cooldown.

**Tech Stack:** Vue 3 Composition API (`<script setup>`), Pinia, Web Awesome 3, Vite. No backend change. No new dependency.

**Reference spec:** `docs/superpowers/specs/2026-04-24-unified-stop-session-process-design.md`

**Quality note (per `CLAUDE.md`):** this project does not write unit tests or run linters. Each task ends with **manual verification in the browser**. The agent MUST start the dev servers via `devctl.py status` / `devctl.py logs` (not restart — restart is reserved to the user).

**Pre-flight check (agent):** before editing any file, confirm the dev servers are running (`uv run ./devctl.py status`). If they are not, **stop** and ask the user to start them. Never restart servers autonomously.

---

## File structure

| File | Change kind | Purpose |
|---|---|---|
| `frontend/src/stores/data.js` | Modify | Add `setSessionStopping` action and `isSessionStopping` getter. Reset `stopping` naturally when `processStates[sessionId]` entry is replaced/deleted. |
| `frontend/src/composables/useStopSessionProcess.js` | Create | Central composable exposing `stopSessionProcess`, `pendingConfirmation`, `confirmPendingStop`, `cancelPendingStop`. |
| `frontend/src/components/StopProcessConfirmDialog.vue` | Modify | Refactor from imperative `open()` API to props-driven (`open`, `mode`, `cronCount`); emit `cancel` in addition to `confirm`. |
| `frontend/src/App.vue` | Modify | Mount global `<StopProcessConfirmDialog>`; wire to composable's `pendingConfirmation`; add triple-Escape handler in `handleGlobalKeydown`. |
| `frontend/src/components/SessionHeader.vue` | Modify | Remove local `stoppingProcess`, its watcher, the local dialog instance, `handleStopConfirm`; `handleStopProcess` and `handleArchive` delegate to `stopSessionProcess`. |
| `frontend/src/components/SessionListItem.vue` | Modify | Same migration as `SessionHeader.vue` for the stop and archive-while-running paths. |

No changes to: `useWebSocket.js` (the `killProcess` export stays), backend, router, any other component.

---

## Task 1: Add `stopping` flag support in the store

**Files:**
- Modify: `frontend/src/stores/data.js` (getters block near line 294; actions block starting line 556)

### Rationale

The `stopping` flag lives inside each `processStates[sessionId]` entry. Because the backend replaces the entire entry when it pushes a new process state, the flag is naturally reset whenever the process advances or dies. We only need:
- an action to set it,
- a getter to read it reactively.

No watcher is required on the store side.

### Steps

- [ ] **Step 1.1 — Add the `isSessionStopping` getter.**

In `frontend/src/stores/data.js`, inside the `getters: {` block (right after `getProcessState` at line 294), add:

```js
/**
 * Whether a stop request has been sent for this session and we're
 * waiting for the backend to confirm the process has died.
 * Used by UI components to show a spinner / disabled state on stop buttons.
 */
isSessionStopping: (state) => (sessionId) =>
    state.processStates[sessionId]?.stopping === true,
```

- [ ] **Step 1.2 — Add the `setSessionStopping` action.**

In the same file, inside the `actions: {` block (starting line 556), add:

```js
/**
 * Mark a session as "stopping" so the UI can reflect it immediately.
 * The flag is automatically cleared when the backend replaces the
 * processState entry (on state transition, including DEAD).
 * No-op if the session has no active process state.
 * @param {string} sessionId
 */
setSessionStopping(sessionId) {
    const ps = this.processStates[sessionId]
    if (!ps) return
    this.processStates[sessionId] = { ...ps, stopping: true }
},
```

Use a new object reference (`{ ...ps, stopping: true }`) rather than mutating in place so consumers relying on reference identity (if any) still update.

- [ ] **Step 1.3 — Browser sanity check.**

Open the app with DevTools, pick a session with a running process, and from the Vue DevTools' Pinia panel (or the console via `useDataStore()`) call `setSessionStopping('<sessionId>')`. Verify `processStates[<id>].stopping === true` and that `isSessionStopping('<id>')` returns `true`.

No explicit revert is needed — the flag will be cleared automatically the next time the backend pushes a new process state for that session (which replaces the whole entry). A page reload also clears it.

- [ ] **Step 1.4 — Commit.**

```bash
git add frontend/src/stores/data.js
git commit -m "feat(front): add stopping flag on processStates + store helpers

Introduce setSessionStopping action and isSessionStopping getter so the
'waiting for the backend to confirm the kill' state can be shared across
all UI components that offer to stop a session's process."
```

---

## Task 2: Refactor `StopProcessConfirmDialog` to a props-driven API

**Files:**
- Modify: `frontend/src/components/StopProcessConfirmDialog.vue` (entire `<script setup>` + template)

### Rationale

Switching the dialog from `ref.open({mode, cronCount})` to `:open="..."`, `:mode="..."`, `:cron-count="..."` lets a single globally-mounted instance be driven by reactive state. We also add a `cancel` emit so the composable can clear its pending-state on dismissal.

### Steps

- [ ] **Step 2.1 — Replace `<script setup>` with props-driven version.**

Replace the full `<script setup>` block (lines 1–51) with:

```vue
<script setup>
/**
 * StopProcessConfirmDialog - Confirmation dialog shown when stopping a process
 * that has active crons.
 *
 * Warns the user that active cron jobs will no longer trigger, and asks for
 * explicit confirmation before proceeding. Supports two modes:
 * - 'stop': just stop the process
 * - 'archive': stop the process and archive the session
 *
 * Props-driven: the parent controls visibility via the `open` prop and
 * receives `confirm` / `cancel` events. There is typically a single instance
 * mounted globally in App.vue.
 */
import { ref, watch } from 'vue'

const props = defineProps({
    open: { type: Boolean, default: false },
    mode: { type: String, default: 'stop' },       // 'stop' | 'archive'
    cronCount: { type: Number, default: 0 },
})
const emit = defineEmits(['confirm', 'cancel'])

const dialogRef = ref(null)

// Sync the `open` prop to the underlying wa-dialog's `open` attribute.
watch(() => props.open, (isOpen) => {
    if (!dialogRef.value) return
    dialogRef.value.open = isOpen
}, { immediate: true })

// When the user dismisses the wa-dialog (Escape, backdrop, close button),
// bubble a `cancel` event so the parent can clear its pending state.
function onWaHide() {
    if (props.open) emit('cancel')
}

function handleConfirm() {
    emit('confirm', { mode: props.mode })
}

function handleCancel() {
    emit('cancel')
}
</script>
```

- [ ] **Step 2.2 — Update template to use props and the hide event.**

Replace the `<template>` block with:

```vue
<template>
    <wa-dialog
        ref="dialogRef"
        label="Active crons will be lost"
        class="stop-confirm-dialog"
        @wa-hide="onWaHide"
    >
        <div class="dialog-content">
            <wa-callout variant="warning" size="small" open>
                <p>
                    This session has
                    <strong>{{ cronCount }} active cron{{ cronCount > 1 ? 's' : '' }}</strong>.
                </p>
                <p>
                    Stopping the process manually will cancel all active crons. They will not be
                    restored automatically when the process restarts.
                </p>
                <p>
                    You can always ask the agent to set them up again later.
                </p>
            </wa-callout>
        </div>

        <div slot="footer" class="dialog-footer">
            <wa-button variant="neutral" appearance="outlined" @click="handleCancel">
                Cancel, keep the process
            </wa-button>
            <wa-button variant="danger" appearance="filled" @click="handleConfirm">
                <wa-icon slot="start" name="ban"></wa-icon>
                {{ mode === 'archive' ? 'Stop and archive' : 'Stop the process' }}
            </wa-button>
        </div>
    </wa-dialog>
</template>
```

The `<style>` block stays untouched.

- [ ] **Step 2.3 — Browser sanity check.**

At this point the dialog is props-driven but the existing call sites still use `ref.open(...)`. The imperative API has been removed, so **`SessionHeader.vue` and `SessionListItem.vue` are now broken.** This is expected — they are fixed in tasks 4 and 5.

Verify the app compiles (`uv run ./devctl.py logs front --lines=50` should not show Vite errors). If the build fails at this stage, re-read the edits — do not proceed to Task 3 until Vite is clean.

- [ ] **Step 2.4 — Commit.**

```bash
git add frontend/src/components/StopProcessConfirmDialog.vue
git commit -m "refactor(front): make StopProcessConfirmDialog props-driven

Switch from imperative open()/close() API to open/mode/cron-count props
plus confirm/cancel events. This enables a single global instance driven
by shared state (coming next)."
```

Note: the working tree is temporarily broken between this commit and Task 5. That is acceptable for bisectability — each commit still describes a single coherent change.

---

## Task 3: Create `useStopSessionProcess` composable and wire the global dialog

**Files:**
- Create: `frontend/src/composables/useStopSessionProcess.js`
- Modify: `frontend/src/App.vue` (imports, template, mount the dialog)

### Rationale

One composable, module-scoped state, consumed by the global dialog mount and by every call site. This is where the "branching logic" (crons → confirmation, no crons → direct kill) lives — *once*.

### Steps

- [ ] **Step 3.1 — Create the composable file.**

New file `frontend/src/composables/useStopSessionProcess.js`:

```js
/**
 * useStopSessionProcess - Centralized "stop a session's process" flow.
 *
 * Single entry point for every UI that can stop a session's Claude Code
 * process (header stop button, sidebar dropdown, triple-Escape shortcut).
 *
 * Exposes module-scoped reactive state (`pendingConfirmation`) consumed by a
 * single <StopProcessConfirmDialog> mounted globally in App.vue. Components
 * only call `stopSessionProcess(sessionId, { archive? })` and forget about
 * the crons / confirmation / flag mechanics.
 */
import { ref } from 'vue'
import { useDataStore } from '../stores/data'
import { killProcess } from './useWebSocket'
import { PROCESS_STATE } from '../constants'

// Module-scoped: shared across every consumer of the composable.
// null when no confirmation dialog is requested.
// Shape: { sessionId, projectId, mode: 'stop' | 'archive', cronCount }
const pendingConfirmation = ref(null)

/**
 * Whether a session's process is stoppable right now.
 * Same rule as the inline canStop/canStopProcess computed in the legacy
 * call sites, kept here so every caller uses the exact same criterion.
 */
function isStoppable(processState) {
    return Boolean(
        processState
        && !processState.synthetic
        && processState.state
        && processState.state !== PROCESS_STATE.DEAD
    )
}

/**
 * Execute the actual kill + optional archive for a session.
 * Also sets the per-session "stopping" flag in the store for UI feedback.
 */
function doKill(store, sessionId, { archive = false, projectId = null } = {}) {
    if (store.isSessionStopping(sessionId)) return  // debounce re-entrance
    store.setSessionStopping(sessionId)
    killProcess(sessionId)
    if (archive) {
        const pid = projectId ?? store.getSession(sessionId)?.project_id
        if (pid) store.setSessionArchived(pid, sessionId, true)
    }
}

/**
 * Stop the process of a session. Handles the active-crons confirmation,
 * the archive variant, and the "no process running" no-op.
 *
 * @param {string} sessionId
 * @param {Object} [options]
 * @param {boolean} [options.archive=false] - Also archive the session after stop.
 *   If `archive` is true and no process is running, archives the session outright.
 */
export function stopSessionProcess(sessionId, { archive = false } = {}) {
    const store = useDataStore()
    const processState = store.getProcessState(sessionId)
    const session = store.getSession(sessionId)
    const projectId = session?.project_id ?? null

    if (!isStoppable(processState)) {
        // Archive-only path: no running process, just archive if requested.
        if (archive && projectId && session && !session.archived) {
            store.setSessionArchived(projectId, sessionId, true)
        }
        return
    }

    const activeCrons = processState.active_crons || []
    if (activeCrons.length > 0) {
        pendingConfirmation.value = {
            sessionId,
            projectId,
            mode: archive ? 'archive' : 'stop',
            cronCount: activeCrons.length,
        }
        return
    }

    doKill(store, sessionId, { archive, projectId })
}

/**
 * Called by the global StopProcessConfirmDialog when the user confirms.
 * The `mode` carried by the dialog payload overrides the pending mode
 * (they are always the same today, but honoring the payload is defensive).
 */
export function confirmPendingStop({ mode } = {}) {
    const pending = pendingConfirmation.value
    if (!pending) return
    pendingConfirmation.value = null
    const store = useDataStore()
    doKill(store, pending.sessionId, {
        archive: (mode ?? pending.mode) === 'archive',
        projectId: pending.projectId,
    })
}

/**
 * Called by the global StopProcessConfirmDialog when the user dismisses.
 */
export function cancelPendingStop() {
    pendingConfirmation.value = null
}

/**
 * Composable wrapper — returns the module-scoped refs and functions so
 * callers can use the standard `const { stopSessionProcess } = useStopSessionProcess()`
 * pattern if they prefer. (Direct named imports also work.)
 */
export function useStopSessionProcess() {
    return {
        stopSessionProcess,
        confirmPendingStop,
        cancelPendingStop,
        pendingConfirmation,
    }
}
```

**Note on design:** both named imports (`import { stopSessionProcess } from …`) and the composable function work. Use whichever is more idiomatic at each call site. The reactive `pendingConfirmation` ref is only consumed by `App.vue`.

- [ ] **Step 3.2 — Mount the global dialog in `App.vue`.**

Edit `frontend/src/App.vue`:

1. Add the import at the top of `<script setup>` (near the other component imports, around line 14):

```js
import StopProcessConfirmDialog from './components/StopProcessConfirmDialog.vue'
import {
    pendingConfirmation,
    confirmPendingStop,
    cancelPendingStop,
} from './composables/useStopSessionProcess'
```

2. Add the dialog to the template — inline it next to the `<CommandPalette>` / `<SearchOverlay>` siblings around line 200–201:

```vue
<StopProcessConfirmDialog
    :open="pendingConfirmation !== null"
    :mode="pendingConfirmation?.mode ?? 'stop'"
    :cron-count="pendingConfirmation?.cronCount ?? 0"
    @confirm="confirmPendingStop"
    @cancel="cancelPendingStop"
/>
```

Place it right after `<SearchOverlay ref="searchOverlayRef" />` and before the `<div class="app-container">`.

- [ ] **Step 3.3 — Browser sanity check.**

The dialog is now mounted globally. The call sites still reference a local `stopConfirmDialogRef` that points at nothing (since Task 2 removed `defineExpose`'s `open`). **Clicking Stop while crons are active will fail silently or throw a null-deref** — this is expected until Tasks 4 and 5.

Verify:
- The app compiles (check `uv run ./devctl.py logs front --lines=50`).
- Navigating through the app does not crash (the dialog is hidden until `pendingConfirmation` becomes non-null).

- [ ] **Step 3.4 — Commit.**

```bash
git add frontend/src/composables/useStopSessionProcess.js frontend/src/App.vue
git commit -m "feat(front): centralize session-process stop flow in composable

Introduce useStopSessionProcess with module-scoped pendingConfirmation ref
consumed by a single globally-mounted StopProcessConfirmDialog in App.vue.
Call sites are migrated in the next commits."
```

---

## Task 4: Migrate `SessionHeader.vue` to the composable

**Files:**
- Modify: `frontend/src/components/SessionHeader.vue`

### Steps

- [ ] **Step 4.1 — Replace the import line.**

Locate the existing import (somewhere near the top of `<script setup>`):

```js
import { killProcess, stopAgent } from '../composables/useWebSocket'
```

Change to:

```js
import { stopAgent } from '../composables/useWebSocket'
import { stopSessionProcess } from '../composables/useStopSessionProcess'
```

`killProcess` is no longer used here directly — only via the composable.

- [ ] **Step 4.2 — Remove the local `StopProcessConfirmDialog` import.**

Find:

```js
import StopProcessConfirmDialog from './StopProcessConfirmDialog.vue'
```

Delete it (the dialog is now mounted globally).

- [ ] **Step 4.3 — Replace local `stoppingProcess` state with a store-backed computed.**

At line 203–215, the current code is:

```js
// Track when a stop request has been sent and we're waiting for the process to die
const stoppingProcess = ref(false)
const stoppingAgent = ref(false)

// Confirmation dialog for stopping a process with active crons
const stopConfirmDialogRef = ref(null)

// Reset stoppingProcess when the process actually dies (or becomes un-stoppable for any reason)
watch(canStopProcess, (canStop) => {
    if (!canStop) {
        stoppingProcess.value = false
    }
})
```

Replace with:

```js
// Track when a stop request has been sent and we're waiting for the process to die.
// Sourced from the store so it stays in sync across all UIs (sidebar, header, shortcut).
const stoppingProcess = computed(() => store.isSessionStopping(props.sessionId))
const stoppingAgent = ref(false)
```

Keep the `stoppingAgent` block and its `watch` (agent stopping is out of scope).

Delete the `stopConfirmDialogRef` line entirely.

Delete the `watch(canStopProcess, ...)` block entirely (the store-backed computed is automatically reset when the backend replaces the processState entry).

- [ ] **Step 4.4 — Simplify `handleStopProcess`.**

At lines 224–237, replace with:

```js
/**
 * Stop the current process. Delegates to the centralized composable, which
 * handles the active-crons confirmation and the stopping flag.
 */
function handleStopProcess() {
    if (stoppingProcess.value) return
    stopSessionProcess(props.sessionId)
}
```

- [ ] **Step 4.5 — Simplify `handleArchive`.**

At lines 290–306, replace with:

```js
/**
 * Archive the current session.
 * Also stops the process if running — archived and running are mutually exclusive.
 * If the process has active crons, the composable shows the confirmation dialog.
 */
function handleArchive() {
    if (!session.value || session.value.archived || session.value.draft) return
    stopSessionProcess(props.sessionId, { archive: true })
}
```

- [ ] **Step 4.6 — Delete `handleStopConfirm`.**

At lines 308–320, delete the entire `handleStopConfirm` function. It is no longer referenced.

- [ ] **Step 4.7 — Remove the `<StopProcessConfirmDialog>` template instance.**

At line 658–659 (inside the `<header>` element), delete:

```vue
<!-- Confirmation dialog for stopping a process with active crons -->
<StopProcessConfirmDialog ref="stopConfirmDialogRef" @confirm="handleStopConfirm" />
```

- [ ] **Step 4.8 — Browser verification.**

Load a session where Claude Code is running **without** active crons:
1. Click the header Stop button. Verify the button shows its spinner (`:loading="stoppingProcess"`), and the process dies on the backend. Verify the spinner disappears once the process transitions to DEAD.

Load a session where the process has **active crons**:
1. Click the header Stop button. The global dialog opens (it now lives in `App.vue`'s DOM — use DevTools to verify there is exactly one `<wa-dialog class="stop-confirm-dialog">` in the document).
2. Click "Cancel, keep the process". The dialog closes; no kill sent; `stoppingProcess` stays false.
3. Click Stop again. Click "Stop the process". The dialog closes; the kill is sent; the spinner appears.

Click the Archive button on a running session with crons:
1. Dialog opens with label "Stop and archive".
2. Confirm. The session is killed and archived.

- [ ] **Step 4.9 — Commit.**

```bash
git add frontend/src/components/SessionHeader.vue
git commit -m "refactor(front): SessionHeader delegates stop to shared composable

Replace local stoppingProcess ref, watcher, dialog instance and
handleStopConfirm with a single stopSessionProcess() call. The stopping
spinner is now driven by the store so it reflects stops triggered from
any UI (sidebar, future triple-Escape shortcut)."
```

---

## Task 5: Migrate `SessionListItem.vue` to the composable

**Files:**
- Modify: `frontend/src/components/SessionListItem.vue`

### Steps

- [ ] **Step 5.1 — Update imports.**

Find:

```js
import { killProcess, markSessionReadState, cancelSessionViewedThrottle } from '../composables/useWebSocket'
```

Change to:

```js
import { markSessionReadState, cancelSessionViewedThrottle } from '../composables/useWebSocket'
import { stopSessionProcess } from '../composables/useStopSessionProcess'
```

And delete:

```js
import StopProcessConfirmDialog from './StopProcessConfirmDialog.vue'
```

- [ ] **Step 5.2 — Replace local `stoppingProcess` state.**

At lines 249–260, replace:

```js
// Track when a stop request has been sent and we're waiting for the process to die
const stoppingProcess = ref(false)

// Confirmation dialog for stopping a process with active crons
const stopConfirmDialogRef = ref(null)

// Reset stoppingProcess when the process actually dies (or becomes un-stoppable for any reason)
watch(canStop, (value) => {
    if (!value) {
        stoppingProcess.value = false
    }
})
```

With:

```js
// Stopping state, shared across all UIs via the store.
const stoppingProcess = computed(() => store.isSessionStopping(props.session.id))
```

- [ ] **Step 5.3 — Simplify the `stop` and `archive` branches in `handleMenuSelect`.**

At lines 300–306 (`else if (action === 'stop')`), replace with:

```js
} else if (action === 'stop') {
    if (!stoppingProcess.value) stopSessionProcess(session.id)
} 
```

At lines 313–322 (`else if (action === 'archive')`), replace with:

```js
} else if (action === 'archive') {
    // Delegates to the composable: it handles the active-crons confirmation
    // and the combined kill+archive in one place.
    stopSessionProcess(session.id, { archive: true })
} 
```

- [ ] **Step 5.4 — Delete `handleStopConfirm`.**

At lines 343–356, delete the entire `handleStopConfirm` function.

- [ ] **Step 5.5 — Remove the `<StopProcessConfirmDialog>` template instance.**

At line 585–586 (inside the session-item template), delete:

```vue
<!-- Confirmation dialog for stopping a process with active crons -->
<StopProcessConfirmDialog ref="stopConfirmDialogRef" @confirm="handleStopConfirm" />
```

- [ ] **Step 5.6 — Browser verification — "Stop" dropdown item.**

Load the sidebar. Find a session with Claude Code running, **no active crons**:
1. Open its dropdown menu. Click "Stop the Claude Code process".
2. Verify the item shows "Stopping…" and is disabled (driven by the new `stoppingProcess` computed).
3. Verify the process dies and the item disappears once DEAD.

Find a session with **active crons**:
1. Open the dropdown. Click "Stop the Claude Code process".
2. The global dialog opens.
3. Cancel. No kill sent.
4. Re-open the dropdown and try again; confirm this time. The kill is sent.

- [ ] **Step 5.7 — Browser verification — "Archive" path.**

Find a running session with crons:
1. Click Archive in the dropdown.
2. Dialog opens with "Stop and archive".
3. Confirm. Session is killed and archived.

Find a running session **without** crons:
1. Click Archive. Session should kill immediately and archive — no dialog.

Find a session that is not running:
1. Click Archive. Session archives immediately (no kill, no dialog).

- [ ] **Step 5.8 — Browser verification — synchronization (new bonus).**

Open a session in the main view so its `SessionHeader` is visible. In the sidebar, find the **same session** (it should appear in the session list too).

1. From the sidebar, click "Stop the Claude Code process".
2. Verify the **header button** immediately shows its spinner (since both read the same store flag now). This was a latent bug before Task 1.

- [ ] **Step 5.9 — Commit.**

```bash
git add frontend/src/components/SessionListItem.vue
git commit -m "refactor(front): SessionListItem delegates stop to shared composable

Remove local stoppingProcess ref, watcher, dialog instance and
handleStopConfirm. Both 'Stop' and 'Archive' dropdown actions now go
through stopSessionProcess(), synchronized with the header spinner."
```

---

## Task 6: Add the triple-Escape global shortcut

**Files:**
- Modify: `frontend/src/App.vue`

### Rationale

Emergency gesture: three `Escape` key presses within a 200 ms rolling window, on a session chat route, with a running process, triggers `stopSessionProcess(currentSessionId)`. The handler lives inside the existing `handleGlobalKeydown` capture-phase listener, does **not** preventDefault or stopPropagation so overlays close naturally, and includes a 1 s cooldown.

### Steps

- [ ] **Step 6.1 — Add constants and state at module scope of `App.vue`'s `<script setup>`.**

Near the existing `SESSION_CHAT_ROUTES` constant (line 74), add:

```js
// Triple-Escape shortcut (emergency stop of the current session's process)
const TRIPLE_ESCAPE_WINDOW_MS = 200  // max gap between two consecutive Escape presses
const TRIPLE_ESCAPE_COOLDOWN_MS = 1000  // after a trigger, ignore Escape for this long
let escapeTimestamps = []  // rolling window of recent Escape presses
let lastTripleEscapeAt = 0
```

- [ ] **Step 6.2 — Extend the imports already added in Task 3.2.**

In `App.vue`, update the two import lines touched by Task 3.2 to also bring in what the shortcut needs — in a single pass so you don't edit the same lines twice:

- Change the `useStopSessionProcess` import to include `stopSessionProcess`:

```js
import {
    pendingConfirmation,
    confirmPendingStop,
    cancelPendingStop,
    stopSessionProcess,
} from './composables/useStopSessionProcess'
```

- Change the `./constants` import (currently `import { COLOR_SCHEME } from './constants'` at line 9) to also import `PROCESS_STATE`:

```js
import { COLOR_SCHEME, PROCESS_STATE } from './constants'
```

- [ ] **Step 6.3 — Add the Escape handler inside `handleGlobalKeydown`.**

Inside the existing `handleGlobalKeydown(e)` function (starts line 94), add the following **at the end of the function body**, just before the closing brace at line 161:

```js
// Triple-Escape: emergency stop of the current chat session's process.
// Only active on chat routes (session, projects-session), only when a
// stoppable process exists. Does NOT preventDefault/stopPropagation —
// we let the Escape propagate so overlays close naturally.
if (e.key === 'Escape' && !e.repeat) {
    const now = performance.now()

    if (now - lastTripleEscapeAt < TRIPLE_ESCAPE_COOLDOWN_MS) {
        // Swallow counting during cooldown (but still let the event bubble)
        return
    }

    if (!SESSION_CHAT_ROUTES.has(route.name)) {
        escapeTimestamps = []
        return
    }
    const sessionId = route.params.sessionId
    if (!sessionId) return

    const ps = dataStore.getProcessState(sessionId)
    const canStop = ps && !ps.synthetic && ps.state && ps.state !== PROCESS_STATE.DEAD
    if (!canStop) {
        escapeTimestamps = []
        return
    }

    // Keep only recent timestamps
    escapeTimestamps = escapeTimestamps.filter(t => now - t < TRIPLE_ESCAPE_WINDOW_MS)
    escapeTimestamps.push(now)

    if (escapeTimestamps.length >= 3) {
        lastTripleEscapeAt = now
        escapeTimestamps = []
        stopSessionProcess(sessionId)
    }
}
```

Note: `dataStore` is already in scope at line 34 (`const dataStore = useDataStore()`). If for any reason it is not, use `useDataStore().getProcessState(sessionId)`.

- [ ] **Step 6.4 — Browser verification — positive case.**

Load a session on its chat route with Claude Code running, no active crons:
1. Press Escape three times rapidly (think of one fluid motion: tap-tap-tap). The process should be killed and the stop spinner should appear.
2. Keep pressing Escape repeatedly for ~1 s. Verify **nothing else happens** (cooldown). No extra kills, no dialog spam.

On the same session, once the process is DEAD:
1. Triple-press Escape again. Nothing happens (`canStop` is false).

- [ ] **Step 6.5 — Browser verification — crons path.**

Load a running session **with active crons** on its chat route:
1. Triple-press Escape. The global confirmation dialog opens (same as clicking the button).
2. Confirm. The kill proceeds.
3. After confirming, the 1 s cooldown is armed — do not re-test triple-Escape for at least a second.

- [ ] **Step 6.6 — Browser verification — scope guards.**

Navigate to:
- Files tab (`session-files`)
- Git tab (`session-git`)
- Terminal tab (`session-terminal`)
- A subagent view (`session-subagent`)
- The sidebar / home page

On each of these, triple-press Escape. Verify **nothing happens** (the `SESSION_CHAT_ROUTES` check excludes them). In particular, in the Terminal tab, the Escape should continue to reach xterm normally — open vim (`vim /tmp/foo`), press Escape to leave insert mode, press Escape again; the terminal should behave normally, not be killed.

- [ ] **Step 6.7 — Browser verification — overlay interaction.**

On a running session with no crons:
1. Open the command palette (Cmd+K / Ctrl+K).
2. Press Escape three times quickly.

Expected behavior: the first Escape closes the command palette (its own handler). The second and third Escapes happen after the palette is closed. Because we filter by `route.name` which stays the chat session through the palette's close, all three Escapes count. → **The process is killed.** This is acceptable per the spec ("emergency gesture — overlays closing on the way is fine").

Also verify: pressing Escape once to close a popover or dropdown does not "poison" the next genuine triple-Escape attempt. After closing something with Escape, wait for `escapeTimestamps` to drain (~250 ms) and re-test.

- [ ] **Step 6.8 — Browser verification — held-key autorepeat.**

Hold the Escape key for ~1 s. Verify the process is **not** killed. The `!e.repeat` filter should prevent browser autorepeat from ever counting beyond the first press.

- [ ] **Step 6.9 — Commit.**

```bash
git add frontend/src/App.vue
git commit -m "feat(front): triple-Escape shortcut to stop the current session's process

Emergency gesture: 3 Escapes within 200 ms rolling window on a chat
session route triggers stopSessionProcess(currentSessionId). Respects the
active-crons confirmation dialog. 1 s cooldown prevents spam. Capture
phase; never stops propagation so overlays still close naturally."
```

---

## Task 7: Wrap up

- [ ] **Step 7.1 — Grep sanity check.**

Run:

```bash
rg "stopConfirmDialogRef|handleStopConfirm" frontend/src/
```

Expected: **no matches** (all call sites migrated).

```bash
rg "killProcess" frontend/src/ --type=js --type=vue
```

Expected matches: only `useWebSocket.js` (definition) and `useStopSessionProcess.js` (the one shared caller). No component should import `killProcess` directly anymore, **except** `ToolUseContent.vue` and `SessionHeader.vue` for the `stopAgent` (not `killProcess`) flow — double-check that only the agent-stopping flow remains as direct WebSocket caller in those files.

If any other file imports `killProcess` for session stopping, migration is incomplete.

- [ ] **Step 7.2 — Browser end-to-end sweep.**

Run the full matrix one more time:
- Header Stop button — no crons: ✓
- Header Stop button — with crons: ✓
- Header Archive — running with crons: ✓
- Sidebar dropdown Stop — no crons: ✓
- Sidebar dropdown Stop — with crons: ✓
- Sidebar dropdown Archive — running: ✓
- Sidebar dropdown Archive — not running: ✓
- Triple-Escape on chat route — no crons: ✓
- Triple-Escape on chat route — with crons: ✓
- Triple-Escape on non-chat route: no-op ✓
- Triple-Escape cooldown: second trigger within 1 s is ignored ✓
- Sidebar + header spinner synchronization: ✓
- Held Escape: no trigger ✓

- [ ] **Step 7.3 — Final housekeeping commit (if needed).**

If any leftover comment, dead import or minor drift is discovered during Step 7.1, commit a small cleanup:

```bash
git add <files>
git commit -m "chore(front): remove leftovers from stop-process centralization"
```

Otherwise skip this step.

---

## Post-implementation reminders for the user

- **No Django migration needed** (frontend-only change).
- **No `npm install`** (no new dependency).
- **Dev server restart not needed** — Vite HMR handles every file in this plan.
- Suggest a follow-up task (not in this plan): the same duplication pattern exists for **agent stopping** between `SessionHeader.vue` and `ToolUseContent.vue` (`stoppingAgent` ref). Per the spec it is **out of scope** for this plan, but it's a natural next candidate for a similar centralization if the user wants.
