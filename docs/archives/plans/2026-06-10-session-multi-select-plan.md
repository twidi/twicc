# Session List Multi-Select Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an explicit multi-select mode to the sidebar session list (Ctrl/Cmd+click toggle, Shift+click range) with a floating batch-actions bar (pin, read/unread, archive, stop, delete drafts).

**Architecture:** A new Pinia store (`sessionSelection`) holds the ephemeral mode + selection state so it doesn't have to be prop-drilled through the virtualized scroller. `SessionListItem` intercepts modifier clicks while the mode is active; `SessionList` computes Shift ranges (it knows the visual order) and prunes the selection; a new `SessionSelectionBar` floats at the bottom of the sidebar and loops batch actions over existing per-session store actions/composables. No backend change.

**Tech Stack:** Vue 3 `<script setup>`, Pinia (options-style stores), Web Awesome 3 (`wa-dropdown`, `wa-dialog`, `wa-button`).

**Spec:** `docs/plans/2026-06-10-session-multi-select-design.md` — read it first; it is the source of truth for behavior.

**Project policies that override generic practice:**
- **No tests, no linting** (project-wide policy). Tasks have no test steps; verification is manual.
- All code/comments/UI strings in **English**.
- **Never** run `devctl.py restart`/`npm install` — the user runs the dev servers.
- This is a git worktree: prefix every Bash command with `cd /home/twidi/dev/twicc-poc/.worktrees/multi-select-sessions && `.

---

### Task 1: Selection store

**Files:**
- Create: `frontend/src/stores/sessionSelection.js`

- [ ] **Step 1.1: Create the store**

```js
// frontend/src/stores/sessionSelection.js
// Pinia store for the session list multi-select mode.
//
// Ephemeral UI state (never persisted): whether the explicit multi-select
// mode is active, which session ids are selected, and the anchor used for
// Shift+click range selection. Range *computation* lives in SessionList.vue,
// which knows the visual order of the filtered list — this store only holds
// the resulting ids.

import { defineStore, acceptHMRUpdate } from 'pinia'

export const useSessionSelectionStore = defineStore('sessionSelection', {
    state: () => ({
        /** Whether the multi-select mode is active. */
        active: false,
        /** Selected session ids. The Set is replaced (never mutated in place) on every change. */
        selectedIds: new Set(),
        /** Anchor session id for Shift+click ranges (last Ctrl/Cmd-toggled item). */
        anchorId: null,
    }),

    getters: {
        count: (state) => state.selectedIds.size,
        isSelected: (state) => (id) => state.selectedIds.has(id),
    },

    actions: {
        enter() {
            this.active = true
        },

        exit() {
            this.active = false
            this.selectedIds = new Set()
            this.anchorId = null
        },

        /** Ctrl/Cmd+click: toggle one session and make it the new anchor. */
        toggle(id) {
            const next = new Set(this.selectedIds)
            if (next.has(id)) next.delete(id)
            else next.add(id)
            this.selectedIds = next
            this.anchorId = id
        },

        /**
         * Shift+click: replace the selection with a range. The anchor is
         * preserved unless explicitly provided (first Shift+click without
         * an anchor selects the clicked item alone and anchors it).
         */
        setSelection(ids, { anchor } = {}) {
            this.selectedIds = new Set(ids)
            if (anchor !== undefined) this.anchorId = anchor
        },

        /** Ctrl/Cmd+Shift+click: add a range to the selection. */
        addSelection(ids) {
            const next = new Set(this.selectedIds)
            for (const id of ids) next.add(id)
            this.selectedIds = next
        },

        /** Drop selected ids that are no longer present in the visible list. */
        prune(visibleIds) {
            if (this.anchorId && !visibleIds.has(this.anchorId)) this.anchorId = null
            if (this.selectedIds.size === 0) return
            const next = new Set([...this.selectedIds].filter(id => visibleIds.has(id)))
            if (next.size !== this.selectedIds.size) this.selectedIds = next
        },
    },
})

if (import.meta.hot) {
    import.meta.hot.accept(acceptHMRUpdate(useSessionSelectionStore, import.meta.hot))
}
```

- [ ] **Step 1.2: Commit**

```bash
git add frontend/src/stores/sessionSelection.js
git commit -m "feat(multi-select): add session selection Pinia store"
```

---

### Task 2: Confirmation-bypass entry point in useStopSessionProcess

The batch archive/stop path needs to run the kill+archive logic without the
single-session confirmation dialog (whose `pendingConfirmation` module state
is single-session by design — see spec). Extract the unconfirmed execution
path and export it, plus `isStoppable` for enablement checks.

**Files:**
- Modify: `frontend/src/composables/useStopSessionProcess.js`

- [ ] **Step 2.1: Export `isStoppable`**

Change the existing private function declaration (line ~31) to an export:

```js
export function isStoppable(processState) {
```

(Body unchanged.)

- [ ] **Step 2.2: Add `stopSessionProcessUnconfirmed` and refactor `stopSessionProcess`**

Replace the current `stopSessionProcess` (lines ~63-90) with:

```js
/**
 * Stop the process of a session WITHOUT the active-crons confirmation.
 *
 * Used by batch flows (session list multi-select) that show ONE aggregated
 * confirmation dialog for the whole batch and must not trigger the
 * single-session `pendingConfirmation` dialog per session. Handles the
 * archive variant and the "no process running" archive-only path.
 *
 * @param {string} sessionId
 * @param {Object} [options]
 * @param {boolean} [options.archive=false] - Also archive the session after stop.
 *   If `archive` is true and no process is running, archives the session outright.
 */
export function stopSessionProcessUnconfirmed(sessionId, { archive = false } = {}) {
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

    doKill(store, sessionId, { archive, projectId })
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

    // Same idiom as SessionHeader / SessionListItem so migrations are behavior-preserving.
    const cronCount = isStoppable(processState) ? (processState.active_crons?.length || 0) : 0
    if (cronCount > 0) {
        const session = store.getSession(sessionId)
        pendingConfirmation.value = {
            sessionId,
            projectId: session?.project_id ?? null,
            mode: archive ? 'archive' : 'stop',
            cronCount,
        }
        return
    }

    stopSessionProcessUnconfirmed(sessionId, { archive })
}
```

Behavior check (must hold): for a single session, the observable behavior is
identical to before — confirmation iff stoppable process with active crons;
archive-only when no process; kill(+archive) otherwise.

- [ ] **Step 2.3: Add the new exports to the composable wrapper**

In `useStopSessionProcess()` at the bottom of the file, add the two new
symbols to the returned object:

```js
export function useStopSessionProcess() {
    return {
        stopSessionProcess,
        stopSessionProcessUnconfirmed,
        isStoppable,
        confirmPendingStop,
        cancelPendingStop,
        pendingConfirmation,
    }
}
```

- [ ] **Step 2.4: Commit**

```bash
git add frontend/src/composables/useStopSessionProcess.js
git commit -m "refactor(stop-process): expose unconfirmed stop path for batch flows"
```

---

### Task 3: Modifier-click interception + selected styling in SessionListItem

**Files:**
- Modify: `frontend/src/components/session/list/SessionListItem.vue`

- [ ] **Step 3.1: Import the selection store and compute the selected flag**

In `<script setup>`, add the import next to the other store imports:

```js
import { useSessionSelectionStore } from '../../../stores/sessionSelection'
```

Instantiate it next to the other stores (after `codeCommentsStore`):

```js
const selectionStore = useSessionSelectionStore()

/** Whether this item is selected in the multi-select mode. */
const selected = computed(() =>
    selectionStore.active && selectionStore.selectedIds.has(props.session.id)
)
```

- [ ] **Step 3.2: Add the `selection-click` emit and rewrite the click handlers**

Change the emits declaration:

```js
const emit = defineEmits(['select', 'drop-data', 'selection-click'])
```

Replace `handleClick` (lines ~308-313) with:

```js
function handleClick(event) {
    if (event.button !== 0) return // middle/right click: let the browser handle it
    const modifier = event.ctrlKey || event.metaKey || event.shiftKey
    if (selectionStore.active && modifier) {
        // Multi-select mode: modifier clicks drive the selection instead of
        // the browser's native open-in-new-tab/window behaviors.
        event.preventDefault()
        emit('selection-click', {
            session: props.session,
            shift: event.shiftKey,
            ctrl: event.ctrlKey || event.metaKey,
        })
        return
    }
    // Let browser handle modifier-key clicks natively (open in new tab, etc.)
    if (modifier) return
    event.preventDefault()
    emit('select', props.session)
}

function handleMousedown(event) {
    // Shift+mousedown extends the browser text selection before our click
    // handler runs; suppress it while the multi-select mode is active.
    if (selectionStore.active && event.shiftKey) event.preventDefault()
}
```

- [ ] **Step 3.3: Wire the template**

On the root wrapper `<div class="session-item-wrapper">`, add the modifier
class to the existing `:class` object:

```js
'session-item-wrapper--selected': selected,
```

On the `<wa-button class="session-item">`, add alongside `@click="handleClick"`:

```html
@mousedown="handleMousedown"
```

- [ ] **Step 3.4: Add the selected style**

In `<style scoped>`, after the `.session-item--highlighted::part(base)` rule:

```css
/* Multi-select mode: selected item */
.session-item-wrapper--selected .session-item::part(base) {
    background-color: var(--wa-color-brand-fill-quiet);
    box-shadow: inset 0 0 0 1px var(--wa-color-brand-border-quiet);
}
```

(Token check: `--wa-color-brand-fill-quiet` / `--wa-color-brand-border-quiet`
are standard Web Awesome 3 semantic tokens; if they render empty, check
`frontend/node_modules/@awesome.me/webawesome/dist/llms.txt` for the current
quiet fill/border token names and substitute.)

- [ ] **Step 3.5: Commit**

```bash
git add frontend/src/components/session/list/SessionListItem.vue
git commit -m "feat(multi-select): intercept modifier clicks and style selected items"
```

---

### Task 4: Range computation, pruning, and Escape priority in SessionList

**Files:**
- Modify: `frontend/src/components/session/list/SessionList.vue`

- [ ] **Step 4.1: Import and instantiate the selection store**

```js
import { useSessionSelectionStore } from '../../../stores/sessionSelection'
```

After `const workspacesStore = ...`:

```js
const selectionStore = useSessionSelectionStore()
```

- [ ] **Step 4.2: Add the selection click handler**

After `handleDropData` (line ~334):

```js
/**
 * Handle a modifier click forwarded by SessionListItem while the
 * multi-select mode is active. Ranges follow the visual order of the
 * filtered flat list (`sessions`), crossing block dividers.
 *
 * Semantics (standard Windows/macOS list selection):
 * - Ctrl/Cmd+click            → toggle the item, it becomes the anchor
 * - Shift+click               → select anchor→target range, REPLACING the selection
 * - Ctrl/Cmd+Shift+click      → ADD the anchor→target range to the selection
 * - Shift+click with no anchor → select the item alone and anchor it
 */
function handleSelectionClick({ session, shift, ctrl }) {
    if (!shift) {
        selectionStore.toggle(session.id)
        return
    }
    const ids = sessions.value.map(s => s.id)
    const targetIndex = ids.indexOf(session.id)
    if (targetIndex === -1) return
    const anchorIndex = selectionStore.anchorId ? ids.indexOf(selectionStore.anchorId) : -1
    if (anchorIndex === -1) {
        selectionStore.setSelection([session.id], { anchor: session.id })
        return
    }
    const [from, to] = anchorIndex <= targetIndex
        ? [anchorIndex, targetIndex]
        : [targetIndex, anchorIndex]
    const range = ids.slice(from, to + 1)
    if (ctrl) selectionStore.addSelection(range)
    else selectionStore.setSelection(range)
}
```

- [ ] **Step 4.3: Prune the selection when the visible list changes**

After the existing `watch(() => props.sessionId, ...)` watchers:

```js
// Sessions that leave the visible list (filter change, archived away, …)
// are dropped from the multi-select selection.
watch(sessions, (list) => {
    if (!selectionStore.active) return
    selectionStore.prune(new Set(list.map(s => s.id)))
})
```

- [ ] **Step 4.4: Escape priority**

The multi-select exit must be checked BEFORE the `if (count === 0) return false`
early return at the top of `handleKeyNavigation` — otherwise Escape could not
exit the mode while the filtered list is empty (mode active + filter matching
nothing). Add at the very top of `handleKeyNavigation`:

```js
function handleKeyNavigation(event, { fromSearch = false } = {}) {
    // Escape priority: exit multi-select mode > clear highlight > (parent:
    // clear search). Checked before the empty-list early return so the mode
    // can be exited even when the filter matches nothing.
    if (event.key === 'Escape' && selectionStore.active) {
        selectionStore.exit()
        return true
    }
    const count = sessions.value.length
    if (count === 0) return false
    // ... rest unchanged
```

The existing `case 'Escape':` block (clear highlight, else return false) stays
as is — it is only reached when the mode is inactive.

(No change needed in `ProjectView.handleSearchKeydown`: it already clears the
search only when `handleKeyNavigation` returns false, so the priority chain
falls out naturally.)

- [ ] **Step 4.5: Wire the event in the template**

On `<SessionListItem ...>` add:

```html
@selection-click="handleSelectionClick"
```

- [ ] **Step 4.6: Commit**

```bash
git add frontend/src/components/session/list/SessionList.vue
git commit -m "feat(multi-select): range selection, pruning and Escape exit in SessionList"
```

---

### Task 5: SessionSelectionBar component

**Files:**
- Create: `frontend/src/components/session/list/SessionSelectionBar.vue`

- [ ] **Step 5.1: Create the component**

```vue
<script setup>
/**
 * SessionSelectionBar - Floating action bar for the session list multi-select mode.
 *
 * Shown at the bottom of the sidebar while the mode is active (replacing the
 * floating "New session" button). Computes which batch actions apply to the
 * current selection, runs them by looping over the existing per-session store
 * actions / composables, and exits the mode once an action completes.
 *
 * Enablement rules (see design doc): strict "applicable to EVERY selected
 * session" — except Mark as read/unread which use Gmail semantics (enabled
 * when at least one session is concerned, applied only to those).
 */
import { ref, computed } from 'vue'
import { useDataStore } from '../../../stores/data'
import { useSessionSelectionStore } from '../../../stores/sessionSelection'
import { markSessionReadState, cancelSessionViewedThrottle } from '../../../composables/useWebSocket'
import { stopSessionProcessUnconfirmed, isStoppable } from '../../../composables/useStopSessionProcess'
import { isSessionUnread } from '../../../utils/sessions'
import { PROCESS_STATE } from '../../../constants'
import AppTooltip from '../../ui/AppTooltip.vue'

const props = defineProps({
    /** Id of the currently open session, if any (route param). */
    activeSessionId: {
        type: String,
        default: null
    }
})

// Emitted with the active session object when a batch action needs to
// navigate away from it (mark-unread / delete-drafts hitting the currently
// open session). The parent handles it with the same select-toggle used by
// normal session clicks (selecting the active session deselects it).
const emit = defineEmits(['deselect-session'])

const store = useDataStore()
const selectionStore = useSessionSelectionStore()

const selectedSessions = computed(() =>
    [...selectionStore.selectedIds]
        .map(id => store.getSession(id))
        .filter(Boolean)
)

const count = computed(() => selectedSessions.value.length)

// ═══════════════════════════════════════════════════════════════════════════
// Enablement rules
// ═══════════════════════════════════════════════════════════════════════════

const hasDraft = computed(() => selectedSessions.value.some(s => s.draft))

const canPin = computed(() => count.value > 0 && !hasDraft.value)

/**
 * Whether a pin-mode checkbox shows as checked: only when EVERY selected
 * session already has that mode. Clicking a mode always applies it to every
 * selected session (no toggle-off on re-click, unlike the per-item menu:
 * with a mixed selection, re-applying a mode must converge everyone to it).
 */
function isPinModeChecked(mode) {
    return count.value > 0 && selectedSessions.value.every(s => (s.pinned || null) === mode)
}

/** Mirrors SessionListItem's canToggleReadState. */
function canToggleRead(session) {
    if (session.draft) return false
    const ps = store.getProcessState(session.id)
    if (ps && ps.state !== PROCESS_STATE.USER_TURN) return false
    return true
}

const markReadCandidates = computed(() =>
    selectedSessions.value.filter(s => isSessionUnread(s, store.getProcessState(s.id)))
)
const markUnreadCandidates = computed(() =>
    selectedSessions.value.filter(s => canToggleRead(s) && !isSessionUnread(s, store.getProcessState(s.id)))
)

const canArchive = computed(() =>
    count.value > 0 && selectedSessions.value.every(s => !s.archived && !s.draft)
)
const canUnarchive = computed(() =>
    count.value > 0 && selectedSessions.value.every(s => s.archived)
)
const canStopAll = computed(() =>
    count.value > 0 && selectedSessions.value.every(s => isStoppable(store.getProcessState(s.id)))
)
const canDeleteDrafts = computed(() =>
    count.value > 0 && selectedSessions.value.every(s => s.draft)
)

// ═══════════════════════════════════════════════════════════════════════════
// Aggregated confirmation dialog (one for the whole batch, never per session)
// ═══════════════════════════════════════════════════════════════════════════

// null when closed. Shape: { mode: 'archive' | 'stop' | 'delete-drafts',
//                            sessionIds, processCount, cronCount }
const confirmState = ref(null)

const confirmLabel = computed(() => {
    if (!confirmState.value) return ''
    const { mode, sessionIds } = confirmState.value
    const n = sessionIds.length
    if (mode === 'archive') return `Archive ${n} session${n > 1 ? 's' : ''}?`
    if (mode === 'stop') return `Stop ${n} process${n > 1 ? 'es' : ''}?`
    return `Delete ${n} draft${n > 1 ? 's' : ''}?`
})

const confirmMessage = computed(() => {
    if (!confirmState.value) return ''
    const { mode, processCount, cronCount } = confirmState.value
    const parts = []
    if (mode === 'archive' && processCount > 0) {
        parts.push(`${processCount} running process${processCount > 1 ? 'es' : ''} will be stopped.`)
    }
    if ((mode === 'archive' || mode === 'stop') && cronCount > 0) {
        parts.push(`${cronCount} active cron job${cronCount > 1 ? 's' : ''} will be cancelled.`)
    }
    if (mode === 'delete-drafts') {
        parts.push('This cannot be undone.')
    }
    return parts.join(' ')
})

const confirmButtonLabel = computed(() => {
    if (!confirmState.value) return ''
    const { mode } = confirmState.value
    if (mode === 'archive') return 'Stop and archive'
    if (mode === 'stop') return 'Stop'
    return 'Delete'
})

function cancelConfirm() {
    confirmState.value = null
}

function handleDialogHide(event) {
    // Guard against wa-hide bubbling from nested WA components (same idiom
    // as BulkArchiveConfirmDialog).
    if (event.target !== event.currentTarget) return
    cancelConfirm()
}

function runConfirmed() {
    const { mode, sessionIds } = confirmState.value
    confirmState.value = null
    if (mode === 'archive') {
        for (const id of sessionIds) stopSessionProcessUnconfirmed(id, { archive: true })
    } else if (mode === 'stop') {
        for (const id of sessionIds) stopSessionProcessUnconfirmed(id)
    } else if (mode === 'delete-drafts') {
        if (sessionIds.includes(props.activeSessionId)) {
            const active = store.getSession(props.activeSessionId)
            if (active) emit('deselect-session', active)
        }
        for (const id of sessionIds) store.deleteDraftSession(id)
    }
    selectionStore.exit()
}

// ═══════════════════════════════════════════════════════════════════════════
// Batch actions
// ═══════════════════════════════════════════════════════════════════════════

/** Count stoppable processes and their active crons among sessions. */
function batchProcessStats(sessionsList) {
    let processCount = 0
    let cronCount = 0
    for (const s of sessionsList) {
        const ps = store.getProcessState(s.id)
        if (isStoppable(ps)) {
            processCount++
            cronCount += ps.active_crons?.length || 0
        }
    }
    return { processCount, cronCount }
}

function requestArchive() {
    const sessionIds = selectedSessions.value.map(s => s.id)
    const { processCount, cronCount } = batchProcessStats(selectedSessions.value)
    if (processCount === 0) {
        for (const s of selectedSessions.value) {
            store.setSessionArchived(s.project_id, s.id, true)
        }
        selectionStore.exit()
        return
    }
    confirmState.value = { mode: 'archive', sessionIds, processCount, cronCount }
}

function requestStop() {
    const sessionIds = selectedSessions.value.map(s => s.id)
    const { processCount, cronCount } = batchProcessStats(selectedSessions.value)
    if (cronCount === 0) {
        // Mirrors the single-session flow: stopping only confirms when active
        // crons would be lost.
        for (const id of sessionIds) stopSessionProcessUnconfirmed(id)
        selectionStore.exit()
        return
    }
    confirmState.value = { mode: 'stop', sessionIds, processCount, cronCount }
}

function handleActionSelect(event) {
    const action = event.detail.item.value
    if (action === 'pin-none' || action === 'pin-project' || action === 'pin-workspace' || action === 'pin-all') {
        const mode = action === 'pin-none' ? null : action.slice(4)
        for (const s of selectedSessions.value) {
            store.setSessionPinMode(s.project_id, s.id, mode)
        }
        selectionStore.exit()
    } else if (action === 'mark-read') {
        for (const s of markReadCandidates.value) {
            markSessionReadState(s.id, false)
        }
        selectionStore.exit()
    } else if (action === 'mark-unread') {
        const candidates = markUnreadCandidates.value
        for (const s of candidates) {
            // Cancel any pending session_viewed trailing throttle to prevent it
            // from immediately resetting last_viewed_at after we mark unread.
            cancelSessionViewedThrottle(s.id)
            markSessionReadState(s.id, true)
        }
        const active = candidates.find(s => s.id === props.activeSessionId)
        if (active) emit('deselect-session', active)
        selectionStore.exit()
    } else if (action === 'archive') {
        requestArchive()
    } else if (action === 'unarchive') {
        for (const s of selectedSessions.value) {
            store.setSessionArchived(s.project_id, s.id, false)
        }
        selectionStore.exit()
    } else if (action === 'stop') {
        requestStop()
    } else if (action === 'delete-drafts') {
        confirmState.value = {
            mode: 'delete-drafts',
            sessionIds: selectedSessions.value.map(s => s.id),
            processCount: 0,
            cronCount: 0,
        }
    }
}
</script>

<template>
    <div class="session-selection-bar">
        <span class="selection-count">{{ count }} selected</span>

        <wa-dropdown placement="top-end" @wa-select="handleActionSelect">
            <wa-button
                slot="trigger"
                variant="brand"
                appearance="accent"
                size="small"
                with-caret
                :disabled="count === 0"
            >
                Actions
            </wa-button>

            <wa-dropdown-item type="checkbox" :checked="isPinModeChecked(null)" :disabled="!canPin" value="pin-none">
                Not pinned
            </wa-dropdown-item>
            <wa-dropdown-item type="checkbox" :checked="isPinModeChecked('project')" :disabled="!canPin" value="pin-project">
                Pin in project
            </wa-dropdown-item>
            <wa-dropdown-item type="checkbox" :checked="isPinModeChecked('workspace')" :disabled="!canPin" value="pin-workspace">
                Pin in workspace
            </wa-dropdown-item>
            <wa-dropdown-item type="checkbox" :checked="isPinModeChecked('all')" :disabled="!canPin" value="pin-all">
                Pin everywhere
            </wa-dropdown-item>
            <wa-divider></wa-divider>
            <wa-dropdown-item :disabled="markReadCandidates.length === 0" value="mark-read">
                <wa-icon slot="icon" name="eye-slash"></wa-icon>
                Mark as read
            </wa-dropdown-item>
            <wa-dropdown-item :disabled="markUnreadCandidates.length === 0" value="mark-unread">
                <wa-icon slot="icon" name="eye"></wa-icon>
                Mark as unread
            </wa-dropdown-item>
            <wa-dropdown-item :disabled="!canArchive" value="archive">
                <wa-icon slot="icon" name="box-archive"></wa-icon>
                Archive
            </wa-dropdown-item>
            <wa-dropdown-item :disabled="!canUnarchive" value="unarchive">
                <wa-icon slot="icon" name="box-open"></wa-icon>
                Unarchive
            </wa-dropdown-item>
            <wa-divider></wa-divider>
            <wa-dropdown-item :disabled="!canStopAll" value="stop">
                <wa-icon slot="icon" name="ban"></wa-icon>
                Stop the processes
            </wa-dropdown-item>
            <wa-dropdown-item :disabled="!canDeleteDrafts" value="delete-drafts" variant="danger">
                <wa-icon slot="icon" name="trash"></wa-icon>
                Delete drafts
            </wa-dropdown-item>
        </wa-dropdown>

        <wa-button
            id="selection-bar-exit"
            variant="neutral"
            appearance="plain"
            size="small"
            @click="selectionStore.exit()"
        >
            <wa-icon name="xmark" label="Exit selection mode"></wa-icon>
        </wa-button>
        <AppTooltip for="selection-bar-exit">Exit selection mode</AppTooltip>

        <!-- Aggregated batch confirmation -->
        <wa-dialog :open="confirmState !== null" :label="confirmLabel" @wa-hide="handleDialogHide">
            <p class="confirm-message">{{ confirmMessage }}</p>
            <wa-button slot="footer" variant="neutral" appearance="plain" @click="cancelConfirm">
                Cancel
            </wa-button>
            <wa-button slot="footer" variant="danger" @click="runConfirmed">
                {{ confirmButtonLabel }}
            </wa-button>
        </wa-dialog>
    </div>
</template>

<style scoped>
.session-selection-bar {
    position: absolute;
    bottom: var(--wa-space-s);
    left: var(--wa-space-s);
    right: var(--wa-space-s);
    z-index: 5;
    display: flex;
    align-items: center;
    gap: var(--wa-space-xs);
    padding: var(--wa-space-xs) var(--wa-space-s);
    background-color: var(--wa-color-surface-raised);
    border: 1px solid var(--wa-color-surface-border);
    border-radius: var(--wa-border-radius-m);
    box-shadow: var(--wa-shadow-m);
}

.selection-count {
    flex: 1;
    min-width: 0;
    font-size: var(--wa-font-size-s);
    color: var(--wa-color-text-quiet);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
}

.confirm-message {
    margin: 0;
}
</style>
```

Implementation notes:
- **Intentional divergence from the spec's action table:** the table says Stop
  = "loop `stopSessionProcess`", but that would fire the single-session
  `pendingConfirmation` dialog once per session with active crons. Consistent
  with the spec's own "never a per-session dialog loop" rule, the batch stop
  aggregates ONE confirmation (only when crons would be lost, mirroring the
  single-session trigger condition) and then loops the unconfirmed path.
- `wa-dialog`, `wa-dropdown`, `wa-dropdown-item`, `wa-divider`, `wa-button`,
  `wa-icon` are already imported in `frontend/src/main.js` (used elsewhere) —
  verify with `rg "dialog/dialog.js" frontend/src/main.js`, and add any
  missing import there.
- Surface tokens (`--wa-color-surface-raised`, `--wa-color-surface-border`,
  `--wa-shadow-m`): if any renders empty, check the Web Awesome docs at
  `frontend/node_modules/@awesome.me/webawesome/dist/llms.txt` and substitute
  the current token names.
- `runConfirmed` reads `confirmState.sessionIds` (captured at request time),
  NOT `selectedSessions`, so the action survives any selection change while
  the dialog is open.

- [ ] **Step 5.2: Commit**

```bash
git add frontend/src/components/session/list/SessionSelectionBar.vue
git commit -m "feat(multi-select): floating batch-actions bar"
```

---

### Task 6: ProjectView integration

**Files:**
- Modify: `frontend/src/views/ProjectView.vue`

- [ ] **Step 6.1: Imports and store**

Add next to the other component imports:

```js
import SessionSelectionBar from '../components/session/list/SessionSelectionBar.vue'
import { useSessionSelectionStore } from '../stores/sessionSelection'
```

Instantiate near the other stores:

```js
const selectionStore = useSessionSelectionStore()
```

- [ ] **Step 6.2: Menu entry in the Session list options dropdown**

In the `session-options-dropdown` (template, line ~1693), add as the FIRST
item, before the `show-archived` checkbox:

```html
<wa-dropdown-item
    type="checkbox"
    value="multi-select"
    :checked="selectionStore.active"
>
    Select sessions
</wa-dropdown-item>
<wa-divider></wa-divider>
```

(Deliberate deviation from the spec's `square-check` icon: checkbox-type
`wa-dropdown-item`s render their own check indicator in the icon slot area,
and the other checkbox items in this menu have no icons either.)

- [ ] **Step 6.3: Handle the menu entry**

In `handleSessionOptionsSelect` (line ~733), add a branch:

```js
} else if (value === 'multi-select') {
    if (item.checked) selectionStore.enter()
    else selectionStore.exit()
}
```

- [ ] **Step 6.4: Mount the bar and hide the New session controls while the mode is active**

In the `.sidebar-sessions` div (template, line ~1759):

1. After the `<SessionList ... />` element, add:

```html
<!-- Multi-select mode: floating batch-actions bar (replaces the New session button) -->
<SessionSelectionBar
    v-if="selectionStore.active"
    :active-session-id="sessionId"
    @deselect-session="handleSessionSelect"
/>
```

2. On the single-project floating button group (`<wa-button-group v-if="!isAllProjectsMode" class="new-session-split-button">`), change the condition to:

```html
v-if="!isAllProjectsMode && !selectionStore.active"
```

3. On the all-projects floating dropdown (`<wa-dropdown v-if="isAllProjectsMode" id="new-session-dropdown">`, line ~1931), change the condition to:

```html
v-if="isAllProjectsMode && !selectionStore.active"
```

4. The two `AppTooltip`s targeting the single-project buttons (lines ~1924-1927,
`v-if="!isAllProjectsMode"`) would keep rendering while their targets are
hidden — harmless but untidy. Extend their condition the same way:

```html
v-if="!isAllProjectsMode && !selectionStore.active"
```

- [ ] **Step 6.5: Exit the mode on project/view change**

Extend the existing `watch(effectiveProjectId, ...)` that clears the search
(line ~622):

```js
// Clear search and exit the multi-select mode when project changes
watch(effectiveProjectId, () => {
    searchQuery.value = ''
    selectionStore.exit()
})
```

- [ ] **Step 6.6: Commit**

```bash
git add frontend/src/views/ProjectView.vue
git commit -m "feat(multi-select): mode entry, floating bar and exits in ProjectView"
```

---

### Task 7: Verification

- [ ] **Step 7.1: Syntax/build check**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/multi-select-sessions/frontend && npx vite build 2>&1 | tail -5
```

Expected: build completes without errors. (Do NOT start/restart dev servers —
user-reserved operation. If the user's dev server is already running, Vite HMR
picks the changes up automatically.)

**If `frontend/node_modules` is missing** (fresh worktree where `devctl.py
start` has never run): do NOT run `npm install`/`npm ci` yourself — ask the
user to either run `uv run ./devctl.py start` once or explicitly approve a
standalone `npm ci` (only while no devctl operation is running in parallel).
Do not silently skip this verification.

- [ ] **Step 7.2: Manual test checklist (user or explicitly-requested browser pass)**

- Outside the mode: plain/Ctrl/Shift/middle clicks behave exactly as before.
- Enter the mode via *Session list options → Select sessions* (checkbox reflects state).
- Ctrl+click toggles; Shift+click ranges (replace); Ctrl+Shift+click adds range; range across block dividers.
- Plain click opens the session and keeps the selection; middle click still opens a new tab.
- Bar appears at mode entry (0 selected, Actions disabled), New session button hidden.
- Enablement matrix: pin disabled with a draft selected; archive disabled if any archived/draft; unarchive only when all archived; stop only when all running; delete drafts only when all drafts; read/unread per Gmail rules.
- Pin checkbox checked only when all selected share the mode; clicking applies to all.
- Batch archive with running processes → single aggregated dialog; cancel keeps mode and selection.
- Mark unread including the open session → navigates away from it.
- Pruning: filter the list with a selection → hidden sessions are dropped from it.
- Exits: ✕, Escape (mode > highlight > search priority), after each action, on project change.

- [ ] **Step 7.3: Final commit (leftovers, if any)**

```bash
git status --short
# commit any remaining changes with an appropriate message
```
