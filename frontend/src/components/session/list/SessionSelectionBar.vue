<script setup>
/**
 * SessionSelectionBar - Action bar for the session list multi-select mode.
 *
 * Shown above the sidebar session list while the mode is active, making the
 * mode immediately visible. Computes which batch actions apply to the current
 * selection and runs them by looping over the existing per-session store
 * actions / composables. The mode is only exited by an explicit user action
 * (the ✕ button here, the menu entry, or Escape) — batch actions leave the
 * mode and the selection in place (list pruning drops sessions that
 * disappear, e.g. after an archive).
 *
 * Enablement rule (uniform Gmail semantics): every action is enabled as soon
 * as it would affect at least one selected session, and only applies to those.
 * The right-aligned count on each menu entry shows exactly how many.
 */
import { ref, computed } from 'vue'
import { useDataStore } from '../../../stores/data'
import { useSessionSelectionStore } from '../../../stores/sessionSelection'
import { markSessionReadState, cancelSessionViewedThrottle } from '../../../composables/useWebSocket'
import { stopSessionProcessUnconfirmed, isStoppable } from '../../../composables/useStopSessionProcess'
import { canToggleSessionReadState, isSessionUnread } from '../../../utils/sessions'
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
// Action targets — for each action, the selected sessions it would actually
// change. An action is enabled iff it has at least one target, applies only
// to its targets, and the target count is shown right-aligned in the menu
// (so a greyed entry reads as "0 affected").
// ═══════════════════════════════════════════════════════════════════════════

/** Drafts can't be pinned. */
const pinnableSessions = computed(() => selectedSessions.value.filter(s => !s.draft && !s.ephemeral))

/** Sessions whose pin mode differs from the target mode. */
function pinTargets(mode) {
    return pinnableSessions.value.filter(s => (s.pinned || null) !== mode)
}

/**
 * Whether a pin-mode checkbox shows as checked: every pinnable selected
 * session already has that mode. Clicking a mode applies it to the sessions
 * whose mode differs (no toggle-off on re-click, unlike the per-item menu).
 */
function isPinModeChecked(mode) {
    return pinnableSessions.value.length > 0
        && pinnableSessions.value.every(s => (s.pinned || null) === mode)
}

const markReadCandidates = computed(() =>
    selectedSessions.value.filter(s => isSessionUnread(s, store.getProcessState(s.id)))
)
const markUnreadCandidates = computed(() =>
    selectedSessions.value.filter(s => {
        const ps = store.getProcessState(s.id)
        return canToggleSessionReadState(s, ps) && !isSessionUnread(s, ps)
    })
)

const archiveTargets = computed(() =>
    selectedSessions.value.filter(s => !s.archived && !s.draft && !s.ephemeral)
)
const unarchiveTargets = computed(() =>
    selectedSessions.value.filter(s => s.archived)
)
const stopTargets = computed(() =>
    selectedSessions.value.filter(s => isStoppable(store.getProcessState(s.id)))
)
const draftTargets = computed(() =>
    selectedSessions.value.filter(s => s.draft || s.ephemeral)
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
    return `Discard ${n} local session${n > 1 ? 's' : ''}?`
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
    // The dialog stays clickable during its hide animation; a double-click
    // (or Cancel-then-confirm) must not re-run on a nulled state.
    if (!confirmState.value) return
    const { mode, sessionIds } = confirmState.value
    confirmState.value = null
    if (mode === 'archive') {
        for (const id of sessionIds) stopSessionProcessUnconfirmed(id, { archive: true })
    } else if (mode === 'stop') {
        for (const id of sessionIds) {
            if (store.getSession(id)?.ephemeral) store.stopEphemeralSession(id)
            else stopSessionProcessUnconfirmed(id)
        }
    } else if (mode === 'delete-drafts') {
        if (sessionIds.includes(props.activeSessionId)) {
            const active = store.getSession(props.activeSessionId)
            if (active) emit('deselect-session', active)
        }
        for (const id of sessionIds) {
            if (store.getSession(id)?.ephemeral && !store.getSession(id)?.draft) store.discardEphemeralSession(id)
            else store.deleteDraftSession(id)
        }
    }
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
    const targets = archiveTargets.value
    const { processCount, cronCount } = batchProcessStats(targets)
    if (processCount === 0) {
        for (const s of targets) {
            store.setSessionArchived(s.project_id, s.id, true)
        }
        return
    }
    confirmState.value = { mode: 'archive', sessionIds: targets.map(s => s.id), processCount, cronCount }
}

function requestStop() {
    const targets = stopTargets.value
    const { processCount, cronCount } = batchProcessStats(targets)
    if (cronCount === 0) {
        // Mirrors the single-session flow: stopping only confirms when active
        // crons would be lost.
        for (const s of targets) stopSessionProcessUnconfirmed(s.id)
        return
    }
    confirmState.value = { mode: 'stop', sessionIds: targets.map(s => s.id), processCount, cronCount }
}

function handleActionSelect(event) {
    const action = event.detail.item.value
    if (action === 'pin-none' || action === 'pin-project' || action === 'pin-workspace' || action === 'pin-all') {
        const mode = action === 'pin-none' ? null : action.slice(4)
        for (const s of pinTargets(mode)) {
            store.setSessionPinMode(s.project_id, s.id, mode)
        }
    } else if (action === 'mark-read') {
        for (const s of markReadCandidates.value) {
            markSessionReadState(s.id, false)
        }
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
    } else if (action === 'archive') {
        requestArchive()
    } else if (action === 'unarchive') {
        for (const s of unarchiveTargets.value) {
            store.setSessionArchived(s.project_id, s.id, false)
        }
    } else if (action === 'stop') {
        requestStop()
    } else if (action === 'delete-drafts') {
        confirmState.value = {
            mode: 'delete-drafts',
            sessionIds: draftTargets.value.map(s => s.id),
            processCount: 0,
            cronCount: 0,
        }
    }
}
</script>

<template>
    <div class="session-selection-bar">
        <span class="selection-count">{{ count }} selected</span>

        <wa-dropdown placement="bottom-end" @wa-select="handleActionSelect">
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

            <wa-dropdown-item type="checkbox" :checked="isPinModeChecked(null)" :disabled="pinTargets(null).length === 0" value="pin-none">
                Not pinned
                <span slot="details" class="affected-count">{{ pinTargets(null).length }}</span>
            </wa-dropdown-item>
            <wa-dropdown-item type="checkbox" :checked="isPinModeChecked('project')" :disabled="pinTargets('project').length === 0" value="pin-project">
                Pin in project
                <span slot="details" class="affected-count">{{ pinTargets('project').length }}</span>
            </wa-dropdown-item>
            <wa-dropdown-item type="checkbox" :checked="isPinModeChecked('workspace')" :disabled="pinTargets('workspace').length === 0" value="pin-workspace">
                Pin in workspace
                <span slot="details" class="affected-count">{{ pinTargets('workspace').length }}</span>
            </wa-dropdown-item>
            <wa-dropdown-item type="checkbox" :checked="isPinModeChecked('all')" :disabled="pinTargets('all').length === 0" value="pin-all">
                Pin everywhere
                <span slot="details" class="affected-count">{{ pinTargets('all').length }}</span>
            </wa-dropdown-item>
            <wa-divider></wa-divider>
            <wa-dropdown-item :disabled="markReadCandidates.length === 0" value="mark-read">
                <wa-icon slot="icon" name="eye-slash"></wa-icon>
                Mark as read
                <span slot="details" class="affected-count">{{ markReadCandidates.length }}</span>
            </wa-dropdown-item>
            <wa-dropdown-item :disabled="markUnreadCandidates.length === 0" value="mark-unread">
                <wa-icon slot="icon" name="eye"></wa-icon>
                Mark as unread
                <span slot="details" class="affected-count">{{ markUnreadCandidates.length }}</span>
            </wa-dropdown-item>
            <wa-dropdown-item :disabled="archiveTargets.length === 0" value="archive">
                <wa-icon slot="icon" name="box-archive"></wa-icon>
                Archive
                <span slot="details" class="affected-count">{{ archiveTargets.length }}</span>
            </wa-dropdown-item>
            <wa-dropdown-item :disabled="unarchiveTargets.length === 0" value="unarchive">
                <wa-icon slot="icon" name="box-open"></wa-icon>
                Unarchive
                <span slot="details" class="affected-count">{{ unarchiveTargets.length }}</span>
            </wa-dropdown-item>
            <wa-divider></wa-divider>
            <wa-dropdown-item :disabled="stopTargets.length === 0" value="stop">
                <wa-icon slot="icon" name="ban"></wa-icon>
                Stop the process{{ stopTargets.length > 1 ? 'es' : '' }}
                <span slot="details" class="affected-count">{{ stopTargets.length }}</span>
            </wa-dropdown-item>
            <wa-dropdown-item :disabled="draftTargets.length === 0" value="delete-drafts" variant="danger">
                <wa-icon slot="icon" name="trash"></wa-icon>
                Discard
                <span slot="details" class="affected-count">{{ draftTargets.length }}</span>
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
/* In-flow banner above the session list (the parent .sidebar-sessions is a
   flex column): takes its own height so it never covers list items, and its
   presence is the visual cue that the multi-select mode is on. */
.session-selection-bar {
    flex-shrink: 0;
    margin: var(--wa-space-2xs) var(--wa-space-2xs) 0;
    display: flex;
    align-items: center;
    gap: var(--wa-space-xs);
    padding: var(--wa-space-xs) var(--wa-space-s);
    background-color: var(--wa-color-surface-raised);
    border: 1px solid var(--wa-color-surface-border);
    border-radius: var(--wa-border-radius-m);
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

/* Right-aligned "how many sessions this action would change" hint. */
.affected-count {
    font-size: var(--wa-font-size-xs);
    color: var(--wa-color-text-quiet);
    font-variant-numeric: tabular-nums;
}
</style>
