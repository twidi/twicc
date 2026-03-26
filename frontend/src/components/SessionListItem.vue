<script setup>
/**
 * SessionListItem - Renders a single session entry in the SessionList.
 *
 * Extracted from SessionList to isolate per-item store lookups into computed
 * properties. Previously, getProcessState() and getPendingRequest() were
 * called ~9 and ~4 times respectively in the template for the same session.
 * Now each is called once via a computed, and Vue caches the result.
 */
import { ref, computed, watch, inject } from 'vue'
import { useDataStore } from '../stores/data'
import { useSettingsStore } from '../stores/settings'
import { formatDate } from '../utils/date'
import { PROCESS_STATE, PROCESS_STATE_COLORS, PROCESS_STATE_NAMES, SESSION_TIME_FORMAT } from '../constants'
import { killProcess, markSessionReadState, cancelSessionViewedThrottle } from '../composables/useWebSocket'
import ProjectBadge from './ProjectBadge.vue'
import ProcessIndicator from './ProcessIndicator.vue'
import ProcessDuration from './ProcessDuration.vue'
import CostDisplay from './CostDisplay.vue'
import AppTooltip from './AppTooltip.vue'

const props = defineProps({
    session: {
        type: Object,
        required: true
    },
    active: {
        type: Boolean,
        default: false
    },
    highlighted: {
        type: Boolean,
        default: false
    },
    compactView: {
        type: Boolean,
        default: false
    },
    showProjectName: {
        type: Boolean,
        default: false
    }
})

const emit = defineEmits(['select'])

const store = useDataStore()
const settingsStore = useSettingsStore()

// ═══════════════════════════════════════════════════════════════════════════
// Cached store lookups (the whole point of this component)
// ═══════════════════════════════════════════════════════════════════════════

/** Process state for this session — single lookup, used everywhere in template. */
const processState = computed(() => store.getProcessState(props.session.id))

/** Pending request for this session — single lookup. */
const pendingRequest = computed(() => store.getPendingRequest(props.session.id))

/** Whether the process has active cron jobs. */
const hasActiveCrons = computed(() => processState.value?.active_crons?.length > 0)

/** Number of active cron jobs (for tooltip). */
const activeCronCount = computed(() => processState.value?.active_crons?.length || 0)

/** Project for this session — single lookup. */
const project = computed(() => store.getProject(props.session.project_id))

/**
 * Whether the session has unread content (new assistant messages since last view).
 * Only shown when:
 * - Not the currently viewed session (active prop = UI guard against race conditions)
 * - There IS new content (last_new_content_at is set)
 * - The user hasn't seen it (last_viewed_at is null or older than last_new_content_at)
 * - If a process is running: only in user_turn state (no point showing during assistant_turn)
 * - Not a draft session
 */
const hasUnread = computed(() => {
    if (props.active) return false
    const session = props.session
    if (session.draft || !session.last_new_content_at) return false
    if (session.last_viewed_at && session.last_new_content_at <= session.last_viewed_at) return false
    // If process is running, only show unread when in user_turn
    if (processState.value && processState.value.state !== PROCESS_STATE.USER_TURN) return false
    return true
})

/**
 * Whether the mark as read/unread menu items should be shown.
 * Hidden when: draft, or process running but not in user_turn.
 * Active session is allowed (mark-unread will deselect it).
 */
const canToggleReadState = computed(() => {
    if (props.session.draft) return false
    if (processState.value && processState.value.state !== PROCESS_STATE.USER_TURN) return false
    return true
})

// ═══════════════════════════════════════════════════════════════════════════
// Settings
// ═══════════════════════════════════════════════════════════════════════════

const showCosts = computed(() => settingsStore.areCostsShown)
const sessionTimeFormat = computed(() => settingsStore.getSessionTimeFormat)
const useRelativeTime = computed(() =>
    sessionTimeFormat.value === SESSION_TIME_FORMAT.RELATIVE_SHORT ||
    sessionTimeFormat.value === SESSION_TIME_FORMAT.RELATIVE_NARROW
)
const relativeTimeFormat = computed(() =>
    sessionTimeFormat.value === SESSION_TIME_FORMAT.RELATIVE_SHORT ? 'short' : 'narrow'
)

// ═══════════════════════════════════════════════════════════════════════════
// Display helpers
// ═══════════════════════════════════════════════════════════════════════════

// Only assistant_turn should animate in session list
const animateStates = ['assistant_turn']

/**
 * Get display name for session (title if available, "New session" for drafts, otherwise ID).
 */
function getSessionDisplayName(session) {
    if (session.draft && !session.title) {
        return 'New session'
    }
    return session.title || session.id
}

/**
 * Get the color for a process state.
 * @param {string} state
 * @returns {string} CSS color variable
 */
function getProcessColor(state) {
    return PROCESS_STATE_COLORS[state] || PROCESS_STATE_COLORS[PROCESS_STATE.DEAD]
}

/**
 * Format memory in bytes to a human-readable string.
 * - KB: no decimal (e.g., "512 KB")
 * - MB < 10: 1 decimal (e.g., "4.6 MB")
 * - MB >= 10: no decimal (e.g., "420 MB")
 * - GB: 1 decimal (e.g., "1.5 GB")
 * @param {number|null} bytes
 * @returns {string}
 */
function formatMemory(bytes) {
    if (bytes == null) return ''

    const kb = bytes / 1024
    const mb = kb / 1024
    const gb = mb / 1024

    if (gb >= 1) {
        return `${gb.toFixed(1)} GB`
    }
    if (mb >= 10) {
        return `${Math.round(mb)} MB`
    }
    if (mb >= 1) {
        return `${mb.toFixed(1)} MB`
    }
    return `${Math.round(kb)} KB`
}

/**
 * Convert Unix timestamp (seconds) to Date object for wa-relative-time.
 * @param {number} timestamp - Unix timestamp in seconds
 * @returns {Date}
 */
function timestampToDate(timestamp) {
    if (!timestamp) return new Date()
    return new Date(timestamp * 1000)
}

/**
 * Check if a session's process can be stopped (any state except dead).
 * @returns {boolean}
 */
const canStop = computed(() => {
    const state = processState.value?.state
    return state && state !== PROCESS_STATE.DEAD
})

// Track when a stop request has been sent and we're waiting for the process to die
const stoppingProcess = ref(false)

// Reset stoppingProcess when the process actually dies (or becomes un-stoppable for any reason)
watch(canStop, (value) => {
    if (!value) {
        stoppingProcess.value = false
    }
})

// ═══════════════════════════════════════════════════════════════════════════
// Actions
// ═══════════════════════════════════════════════════════════════════════════

// Rename dialog (provided by ProjectView)
const openRenameDialog = inject('openRenameDialog')

/**
 * Handle dropdown menu selection for a session.
 * @param {CustomEvent} event - The wa-select event
 */
function handleMenuSelect(event) {
    const action = event.detail.item.value
    const session = props.session
    if (action === 'rename') {
        openRenameDialog(session)
    } else if (action === 'stop') {
        stoppingProcess.value = true
        killProcess(session.id)
    } else if (action === 'delete-draft') {
        store.deleteDraftSession(session.id)
    } else if (action === 'archive') {
        // Stop the process if running — archived and running are mutually exclusive
        if (canStop.value) {
            killProcess(session.id)
        }
        store.setSessionArchived(session.project_id, session.id, true)
    } else if (action === 'unarchive') {
        store.setSessionArchived(session.project_id, session.id, false)
    } else if (action === 'pin') {
        store.setSessionPinned(session.project_id, session.id, true)
    } else if (action === 'unpin') {
        store.setSessionPinned(session.project_id, session.id, false)
    } else if (action === 'mark-unread') {
        // Cancel any pending session_viewed trailing throttle to prevent it
        // from immediately resetting last_viewed_at after we mark unread
        cancelSessionViewedThrottle(session.id)
        markSessionReadState(session.id, true)
        // If viewing this session, deselect it (navigate to project home)
        // to prevent session_viewed from re-marking it as read
        if (props.active) {
            emit('select', session)
        }
    } else if (action === 'mark-read') {
        markSessionReadState(session.id, false)
    }
}
</script>

<template>
    <div
        class="session-item-wrapper"
        :class="{
            'session-item-wrapper--active': active,
            'session-item-wrapper--highlighted': highlighted,
            'session-item-wrapper--compact': compactView
        }"
    >
        <wa-button
            :id="`session-button-${session.id}`"
            :appearance="active ? 'outlined' : 'plain'"
            :variant="active ? 'brand' : 'neutral'"
            class="session-item"
            :class="{
                'session-item--active': active,
                'session-item--highlighted': highlighted
            }"
            @click="emit('select', session)"
        >
            <div class="session-name-row">
                <!-- Compact mode: inline project color dot (instead of full ProjectBadge line) -->
                <span
                    v-if="compactView && showProjectName"
                    :id="`compact-project-dot-${session.id}`"
                    class="compact-project-dot"
                    :style="project?.color ? { '--dot-color': project.color } : null"
                ></span>
                <AppTooltip v-if="compactView && showProjectName" :for="`compact-project-dot-${session.id}`">{{ store.getProjectDisplayName(session.project_id) }}</AppTooltip>
                <wa-icon v-if="session.pinned" name="thumbtack" class="pinned-icon"></wa-icon>
                <wa-tag v-if="session.archived" size="small" variant="neutral" class="archived-tag">Arch.</wa-tag>
                <wa-tag v-else-if="session.draft && !processState" size="small" variant="warning" class="draft-tag">Draft</wa-tag>
                <span class="session-name">{{ getSessionDisplayName(session) }}</span>
                <!-- Compact mode: unread indicator (highest priority) -->
                <wa-icon
                    v-if="compactView && hasUnread"
                    :id="`compact-unread-${session.id}`"
                    name="eye"
                    class="compact-unread-indicator"
                ></wa-icon>
                <AppTooltip v-if="compactView && hasUnread" :for="`compact-unread-${session.id}`">New content to read<template v-if="processState"> · Claude Code state: {{ PROCESS_STATE_NAMES[processState.state] }}<template v-if="activeCronCount"> ({{ activeCronCount }} active cron{{ activeCronCount > 1 ? 's' : '' }})</template></template></AppTooltip>
                <!-- Compact mode: pending request indicator (takes priority over process indicator) -->
                <wa-icon
                    v-if="compactView && !hasUnread && pendingRequest"
                    :id="`compact-pending-request-${session.id}`"
                    name="hand"
                    class="compact-pending-request-indicator"
                ></wa-icon>
                <AppTooltip v-if="compactView && !hasUnread && pendingRequest" :for="`compact-pending-request-${session.id}`">Waiting for your response</AppTooltip>
                <!-- Compact mode: process indicator (hidden when unread or pending request is shown) -->
                <ProcessIndicator
                    v-if="compactView && processState && !hasUnread && !pendingRequest"
                    :id="`compact-process-indicator-${session.id}`"
                    :state="processState.state"
                    :has-active-crons="hasActiveCrons"
                    size="small"
                    :animate-states="animateStates"
                    class="compact-process-indicator"
                />
                <AppTooltip v-if="compactView && processState && !hasUnread && !pendingRequest" :for="`compact-process-indicator-${session.id}`">Claude Code state: {{ PROCESS_STATE_NAMES[processState.state] }}<template v-if="activeCronCount"> ({{ activeCronCount }} active cron{{ activeCronCount > 1 ? 's' : '' }})</template></AppTooltip>
            </div>
            <!-- Project badge line (hidden in compact mode, dot is shown inline instead) -->
            <!-- When unread + no process: show unread indicator on the project line (right-aligned) -->
            <div v-if="!compactView && (showProjectName || (hasUnread && !processState))" class="session-project-row">
                <ProjectBadge v-if="showProjectName" :project-id="session.project_id" class="session-project" />
                <wa-icon
                    v-if="hasUnread && !processState"
                    :id="`standalone-unread-${session.id}`"
                    name="eye"
                    class="unread-indicator standalone-unread-indicator"
                ></wa-icon>
                <AppTooltip v-if="hasUnread && !processState" :for="`standalone-unread-${session.id}`">New content to read</AppTooltip>
            </div>
            <!-- Process info row (only shown when process is active, hidden in compact mode) -->
            <div
                v-if="!compactView && processState"
                class="process-info"
                :style="{ color: getProcessColor(processState.state) }"
            >
                <span :id="`process-memory-${session.id}`" class="process-memory">
                    <template v-if="processState.memory">
                        {{ formatMemory(processState.memory) }}
                    </template>
                </span>
                <AppTooltip :for="`process-memory-${session.id}`">Claude Code memory usage</AppTooltip>

                <span :id="`process-duration-${session.id}`" class="process-duration">
                    <ProcessDuration
                        v-if="processState.state === PROCESS_STATE.ASSISTANT_TURN && processState.state_changed_at"
                        :state-changed-at="processState.state_changed_at"
                    />
                </span>
                <AppTooltip :for="`process-duration-${session.id}`">Assistant turn duration</AppTooltip>

                <span class="process-indicator-cell">
                    <wa-icon
                        v-if="pendingRequest"
                        :id="`pending-request-${session.id}`"
                        name="hand"
                        class="pending-request-indicator"
                    ></wa-icon>
                    <AppTooltip v-if="pendingRequest" :for="`pending-request-${session.id}`">Waiting for your response</AppTooltip>
                    <!-- Unread indicator replaces process indicator when unread -->
                    <wa-icon
                        v-if="hasUnread"
                        :id="`process-unread-${session.id}`"
                        name="eye"
                        class="unread-indicator"
                    ></wa-icon>
                    <AppTooltip v-if="hasUnread" :for="`process-unread-${session.id}`">New content to read · Claude Code state: {{ PROCESS_STATE_NAMES[processState.state] }}<template v-if="activeCronCount"> ({{ activeCronCount }} active cron{{ activeCronCount > 1 ? 's' : '' }})</template></AppTooltip>
                    <ProcessIndicator
                        v-else
                        :id="`process-indicator-${session.id}`"
                        :state="processState.state"
                        :has-active-crons="hasActiveCrons"
                        size="small"
                        :animate-states="animateStates"
                    />
                    <AppTooltip v-if="!hasUnread" :for="`process-indicator-${session.id}`">Claude Code state: {{ PROCESS_STATE_NAMES[processState.state] }}<template v-if="activeCronCount"> ({{ activeCronCount }} active cron{{ activeCronCount > 1 ? 's' : '' }})</template></AppTooltip>
                </span>
            </div>
            <!-- Meta row (not shown for draft sessions, hidden in compact mode) -->
            <div v-if="!compactView && !session.draft" class="session-meta" :class="{ 'session-meta--no-cost': !showCosts }">
                <span :id="`session-messages-${session.id}`" class="session-messages"><wa-icon auto-width name="comment" variant="regular"></wa-icon>{{ session.user_message_count ?? '??' }}</span>
                <AppTooltip :for="`session-messages-${session.id}`">Number of message turns</AppTooltip>

                <template v-if="showCosts">
                    <CostDisplay :id="`session-cost-${session.id}`" :cost="session.total_cost" class="session-cost" />
                    <AppTooltip :for="`session-cost-${session.id}`">Total session cost</AppTooltip>
                </template>

                <span :id="`session-mtime-${session.id}`" class="session-mtime">
                    <wa-icon auto-width name="clock" variant="regular"></wa-icon>
                    <wa-relative-time v-if="useRelativeTime" :date.prop="timestampToDate(session.mtime)" :format="relativeTimeFormat" numeric="always" sync></wa-relative-time>
                    <template v-else>{{ formatDate(session.mtime, { smart: true }) }}</template>
                </span>
                <AppTooltip :for="`session-mtime-${session.id}`">{{ useRelativeTime ? `Last activity: ${formatDate(session.mtime, { smart: true })}` : 'Last activity' }}</AppTooltip>
            </div>
        </wa-button>
        <AppTooltip :for="`session-button-${session.id}`" placement="right">{{ session.title || session.id }}</AppTooltip>
        <!-- Session dropdown menu (outside button to avoid nesting issues) -->
        <wa-dropdown
            class="session-menu"
            placement="bottom-end"
            @wa-select="handleMenuSelect"
        >
            <wa-button
                :id="`session-menu-trigger-${session.id}`"
                slot="trigger"
                variant="neutral"
                appearance="plain"
                size="small"
                class="session-menu-trigger"
            >
                <wa-icon name="ellipsis" label="Session menu"></wa-icon>
            </wa-button>
            <!-- Standard actions -->
            <wa-dropdown-item value="rename">
                <wa-icon slot="icon" name="pencil"></wa-icon>
                Rename
            </wa-dropdown-item>
            <wa-dropdown-item v-if="!session.draft && !session.pinned" value="pin">
                <wa-icon slot="icon" name="thumbtack"></wa-icon>
                Pin
            </wa-dropdown-item>
            <wa-dropdown-item v-if="session.pinned" value="unpin">
                <wa-icon slot="icon" name="thumbtack" class="unpinned-menu-icon"></wa-icon>
                Unpin
            </wa-dropdown-item>
            <wa-dropdown-item v-if="canToggleReadState && hasUnread" value="mark-read">
                <wa-icon slot="icon" name="eye-slash"></wa-icon>
                Mark as read
            </wa-dropdown-item>
            <wa-dropdown-item v-if="canToggleReadState && !hasUnread" value="mark-unread">
                <wa-icon slot="icon" name="eye"></wa-icon>
                Mark as unread
            </wa-dropdown-item>
            <wa-dropdown-item v-if="!session.draft && !session.archived" value="archive">
                <wa-icon slot="icon" name="box-archive"></wa-icon>
                {{ canStop ? 'Archive (it will stop the Claude Code process)' : 'Archive' }}
            </wa-dropdown-item>
            <wa-dropdown-item v-if="session.archived" value="unarchive">
                <wa-icon slot="icon" name="box-open"></wa-icon>
                Unarchive
            </wa-dropdown-item>
            <!-- Danger actions -->
            <template v-if="canStop || session.draft">
                <wa-divider></wa-divider>
                <wa-dropdown-item v-if="canStop" value="stop" :disabled="stoppingProcess">
                    <wa-icon slot="icon" name="ban"></wa-icon>
                    {{ stoppingProcess ? 'Stopping…' : 'Stop the Claude Code process' }}
                </wa-dropdown-item>
                <wa-dropdown-item v-if="session.draft" value="delete-draft" variant="danger">
                    <wa-icon slot="icon" name="trash"></wa-icon>
                    Delete draft
                </wa-dropdown-item>
            </template>
        </wa-dropdown>
        <AppTooltip :for="`session-menu-trigger-${session.id}`">Session actions</AppTooltip>
    </div>
</template>

<style scoped>
.session-item-wrapper {
    position: relative;
    width: 100%;
}

.session-item {
    width: 100%;
}

.session-item::part(base) {
    padding: var(--wa-space-xs);
    height: auto;
    margin-bottom: var(--wa-shadow-offset-y-s);  /* default if border, enforce for non active items to avoid movement */
}

/* Keyboard navigation highlight */
.session-item--highlighted::part(base) {
    outline: var(--wa-focus-ring);
    outline-offset: var(--wa-focus-ring-offset);
}

.session-item::part(label) {
    width: 100%;
    text-align: left;
}

.session-name-row {
    display: flex;
    align-items: center;
    gap: var(--wa-space-xs);
    min-width: 0;
}

.pinned-icon {
    flex-shrink: 0;
    font-size: var(--wa-font-size-2xs);
    color: var(--wa-color-yellow-80) !important;
    transform: rotate(30deg);
}

.unpinned-menu-icon {
    opacity: 0.5;
}

.draft-tag,
.archived-tag {
    flex-shrink: 0;
    line-height: unset;
    height: unset;
    align-self: stretch;
}

.session-name {
    font-size: var(--wa-font-size-s);
    font-weight: 700;
    /* Truncate with ellipsis */
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    /* Visual space for dropdown button */
    margin-right: 1.5rem;
}

/* Compact mode: inline project color dot */
.compact-project-dot {
    width: var(--wa-space-s);
    height: var(--wa-space-s);
    border-radius: 50%;
    flex-shrink: 0;
    border: 1px solid;
    box-sizing: border-box;
    background-color: var(--dot-color, transparent);
    border-color: var(--dot-color, var(--wa-color-border-quiet));
}

/* Compact mode: inline pending request indicator */
.compact-pending-request-indicator {
    margin-left: auto;
    flex-shrink: 0;
    color: var(--wa-color-warning-60);
    font-size: var(--wa-font-size-xs);
    animation: pending-pulse 1.5s ease-in-out infinite;
    position: relative;
    left: -1.5rem;
}

/* Compact mode: inline unread indicator */
.compact-unread-indicator {
    margin-left: auto;
    flex-shrink: 0;
    color: var(--wa-color-warning-60);
    font-size: var(--wa-font-size-xs);
    position: relative;
    left: -1.5rem;
}

/* Compact mode: inline process indicator pushed to the right */
.compact-process-indicator {
    margin-left: auto;
    flex-shrink: 0;
    position: relative;
    left: -1.5rem;
}

/* Session dropdown menu */
.session-menu {
    display: block;
    position: absolute;
    top: var(--wa-space-xs);
    right: 0;
    z-index: 1;
}

.session-menu-trigger {
    opacity: 0.4;
    transition: opacity 0.15s;
    font-size: var(--wa-font-size-2xs);
}

/* Show menu trigger on hover or when dropdown is open */
.session-item-wrapper:hover .session-menu-trigger,
.session-item-wrapper--active .session-menu-trigger,
.session-menu[open] .session-menu-trigger {
    opacity: 0.6;
}

.session-menu-trigger:hover {
    opacity: 1 !important;
}

/* Unread indicator — orange eye icon (same color as warning/pending) */
.unread-indicator {
    color: var(--wa-color-warning-60);
    font-size: var(--wa-font-size-s);
    flex-shrink: 0;
}

/* Project + unread row wrapper (non-compact mode) */
.session-project-row {
    display: flex;
    align-items: center;
    gap: var(--wa-space-xs);
    margin-top: var(--wa-space-3xs);
}

.session-project-row .session-project {
    margin-top: 0;
}

.standalone-unread-indicator {
    margin-left: auto;
}

.session-project {
    font-size: var(--wa-font-size-xs);
    color: var(--wa-color-text-quiet);
    font-weight: var(--wa-font-weight-body);;
    margin-top: var(--wa-space-3xs);
    max-width: 100%;
}

/* Process info row - shows memory, duration, and indicator */
.process-info {
    display: grid;
    grid-template-columns: 1fr auto 0.6fr;
    align-items: center;
    justify-items: end;
    gap: var(--wa-space-xs);
    font-size: var(--wa-font-size-xs);
    font-weight: var(--wa-font-weight-body);
    margin-top: var(--wa-space-3xs);
    overflow: hidden;
}

.process-memory {
    justify-self: start;
}

.process-duration {
    justify-self: center;
}

.process-indicator-cell {
    display: flex;
    align-items: center;
    justify-content: flex-end;
    gap: var(--wa-space-2xs);
}

.pending-request-indicator {
    color: var(--wa-color-warning-60);
    font-size: var(--wa-font-size-xs);
    animation: pending-pulse 1.5s ease-in-out infinite;
}

@keyframes pending-pulse {
    0%, 100% { opacity: 1; }
    50% { opacity: 0.3; }
}

.session-meta {
    display: grid;
    grid-template-columns: 1fr 1fr 1fr;
    align-items: center;
    justify-items: start;
    gap: var(--wa-space-xs);
    font-size: var(--wa-font-size-xs);
    color: var(--wa-color-text-quiet);
    font-weight: var(--wa-font-weight-body);;
    margin-top: var(--wa-space-2xs);
    overflow: hidden;
}

.session-meta > span {
    display: flex;
    align-items: center;
    gap: var(--wa-space-xs);
}

.session-mtime {
    justify-self: end;
}
.session-cost {
    justify-self: center;
}

.session-meta--no-cost {
    grid-template-columns: 1fr 1fr;
}

/* Compact mode: tighter padding */
.session-item-wrapper--compact .session-item::part(base) {
    padding-block: var(--wa-space-2xs);
}

/* Responsive: container queries reference the 'session-list' container
   defined in the parent SessionList component. CSS container queries
   work across component boundaries (DOM hierarchy, not component tree). */

@container session-list (width <= 12rem) {
    /* Hide memory when container is too narrow */
    .process-info {
        grid-template-columns: auto 0.6fr;
    }
    .process-duration {
        justify-self: start;
    }
    .process-memory {
        display: none !important;
    }
    /* Hide cost when container is too narrow */
    .session-meta {
        grid-template-columns: 1fr 1fr;
    }
    .session-cost {
        display: none !important;
    }
}

@container session-list (width <= 9rem) {
    /* Hide duration when container is very narrow (keep only indicator) */
    .process-info {
        grid-template-columns: 1fr;
    }
    .process-duration {
        display: none !important;
    }
    /* Hide messages when container is very narrow (keep only mtime) */
    .session-meta {
        grid-template-columns: 1fr;
    }
    .session-messages {
        display: none !important;
    }
}
</style>
