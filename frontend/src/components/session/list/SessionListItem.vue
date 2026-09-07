<script setup>
/**
 * SessionListItem - Renders a single session entry in the SessionList.
 *
 * Extracted from SessionList to isolate per-item store lookups into computed
 * properties. Previously, getProcessState() and getPendingRequests() were
 * called ~9 and ~4 times respectively in the template for the same session.
 * Now each is called once via a computed, and Vue caches the result.
 */
import { computed, watch, inject } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useCodeCommentsStore } from '../../../stores/codeComments'
import { useDataStore } from '../../../stores/data'
import { useSessionSelectionStore } from '../../../stores/sessionSelection'
import { useSettingsStore } from '../../../stores/settings'
import { formatDate } from '../../../utils/date'
import { sessionRouteLocation } from '../../../utils/sessionRoute'
import { projectPathTitle } from '../../../utils/projectName'
import { isSessionUnread } from '../../../utils/sessions'
import { PROCESS_STATE, PROCESS_STATE_COLORS, PROCESS_STATE_NAMES, SESSION_TIME_FORMAT } from '../../../constants'
import { markSessionReadState, cancelSessionViewedThrottle } from '../../../composables/useWebSocket'
import { stopSessionProcess } from '../../../composables/useStopSessionProcess'
import { useDragHover } from '../../../composables/useDragHover'
import ProjectBadge from '../../project/ProjectBadge.vue'
import WorktreeBadge from '../../project/WorktreeBadge.vue'
import ProjectMark from '../../project/ProjectMark.vue'
import ProcessIndicator from '../../ui/ProcessIndicator.vue'
import ProcessDuration from '../../ui/ProcessDuration.vue'
import CostDisplay from '../../ui/CostDisplay.vue'
import AppTooltip from '../../ui/AppTooltip.vue'
import { getProviderLabel, getProviderIcon } from '../../../providers'
import ProviderIcon from '../../ui/ProviderIcon.vue'

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
    },
    showTitleTooltip: {
        type: Boolean,
        default: true
    },
    // Natural scope project IDs for the current sidebar filter (single-project,
    // workspace, or all-projects). When non-null and the session's project is
    // not in this set, the session is "cross-filter" (e.g. deep-linked extra
    // session or a pinned session whose mode brings it in from another project)
    // and the project badge is shown even when showProjectName is false.
    // null = no scope filter (all-projects mode): rely on showProjectName alone.
    scopeProjectIds: {
        type: Array,
        default: null
    },
    // The single real project the sidebar is filtering on, or null in workspace /
    // all-projects mode. Used to hide the worktree marker when we're filtering
    // directly on this session's worktree (every session there is already from
    // it, so the marker would be noise).
    filterProjectId: {
        type: String,
        default: null
    }
})

const emit = defineEmits(['select', 'drop-data', 'selection-click'])

// ═══════════════════════════════════════════════════════════════════════════
// Drag-hover: spring-loaded session switching (hover 1s while dragging to switch)
// ═══════════════════════════════════════════════════════════════════════════

const { onDragenter, onDragleave, onDragover, onDrop, isPending: isDragPending, cancel: cancelDragHover } = useDragHover({
    onActivate: () => emit('select', props.session),
    shouldActivate: () => !props.active,
    onDropData: (data) => {
        // onActivate already navigated to this session — just forward the drop data
        emit('drop-data', { session: props.session, ...data })
    },
})

// Cancel pending drag timer when the virtual scroller recycles this component for a different session
watch(() => props.session.id, () => {
    cancelDragHover()
})

const route = useRoute()
const router = useRouter()
const store = useDataStore()
const settingsStore = useSettingsStore()
const codeCommentsStore = useCodeCommentsStore()
const selectionStore = useSessionSelectionStore()

/** Whether this item is selected in the multi-select mode. */
const selected = computed(() =>
    selectionStore.active && selectionStore.selectedIds.has(props.session.id)
)

const hasCodeComments = computed(() =>
    codeCommentsStore.countBySession(props.session.project_id, props.session.id) > 0
)

// ═══════════════════════════════════════════════════════════════════════════
// Cached store lookups (the whole point of this component)
// ═══════════════════════════════════════════════════════════════════════════

/** Process state for this session — single lookup, used everywhere in template. */
const processState = computed(() => store.getProcessState(props.session.id))

/** Human-readable provider label, used in tooltips/dropdown labels. */
const providerLabel = computed(() => getProviderLabel(props.session.provider))

/** Web Awesome icon name for the session's provider. */
const providerIcon = computed(() => getProviderIcon(props.session.provider))

/** Whether a share host is configured — gates the "Share" menu action (drafts
 *  have no transcript to share, so they're excluded too). Mirrors SessionHeader. */
const sharingEnabled = computed(() => !!settingsStore.getUsableShareBaseUrl)

/** Whether this session has any pending request waiting for user response. */
const pendingRequest = computed(() => store.getPendingRequests(props.session.id).length > 0)

/** Whether the process has active cron jobs. */
const hasActiveCrons = computed(() => processState.value?.active_crons?.length > 0)

/** Number of active cron jobs (for tooltip). */
const activeCronCount = computed(() => processState.value?.active_crons?.length || 0)

/** Project for this session — single lookup. */
const project = computed(() => store.getProject(props.session.project_id))

/** Whether this session's project is a git worktree of another project. */
const isProjectWorktree = computed(() => !!project.value?.worktree_of)

/**
 * Whether to show the worktree marker (a code-branch icon) before the title.
 * Shown for any worktree session in every filter context, except when we're
 * filtering directly on that worktree — there, all sessions belong to it, so
 * the marker would add nothing. (filterProjectId is null in workspace /
 * all-projects mode, so the marker shows there too.)
 */
const showWorktreeIcon = computed(() =>
    isProjectWorktree.value && props.session.project_id !== props.filterProjectId
)

/**
 * Color for the project dot. A worktree with no color of its own inherits its
 * main repository's color (matching the dot shown elsewhere for worktrees).
 */
const projectDotColor = computed(() => {
    const p = project.value
    if (!p) return null
    if (p.worktree_of) return p.color || store.getProject(p.worktree_of)?.color || null
    return p.color || null
})

/**
 * Whether to show the project badge for this specific session.
 * True when the parent explicitly asked (all-projects mode with multiple
 * visible projects), or when the session falls outside the current filter's
 * natural scope (cross-filter deep link or cross-filter pinned session —
 * the badge tells the user it belongs elsewhere).
 */
const effectiveShowProjectName = computed(() => {
    if (props.showProjectName) return true
    if (!props.scopeProjectIds) return false
    return !props.scopeProjectIds.includes(props.session.project_id)
})

/**
 * Whether the session has unread content (new assistant messages since last view).
 *
 * Delegates to the canonical `isSessionUnread` predicate so this per-row eye
 * stays in lockstep with the unread badges/favicon and never drifts on the
 * archived/hidden/subagent guards — an archived session surfaced via "Show
 * archived" must not read as unread. The only extra rule here is the `active`
 * guard: the row the user is currently viewing never shows the eye (UI guard
 * against the race where last_new_content_at updates while the session is on
 * screen).
 */
const hasUnread = computed(() => {
    if (props.active) return false
    return isSessionUnread(props.session, processState.value)
})

/**
 * Whether the mark as read/unread menu items should be shown.
 * Hidden when: draft, archived, or process running but not in user_turn.
 * Archived sessions never read as unread (see `hasUnread` / `isSessionUnread`),
 * so toggling their read state is meaningless — both items are dropped from the
 * Session Actions menu. Active session is allowed (mark-unread will deselect it).
 */
const canToggleReadState = computed(() => {
    if (props.session.draft || props.session.ephemeral || props.session.archived) return false
    if (processState.value && processState.value.state !== PROCESS_STATE.USER_TURN) return false
    return true
})

/**
 * Whether non-danger menu items are shown after the pin block (mark-read/unread,
 * archive/unarchive). Used to decide whether to render a divider between the pin
 * block and the rest. The danger section has its own divider, so we only need
 * our own if there's something between pin and danger.
 */
const hasItemsAfterPinBlock = computed(() => {
    const s = props.session
    return canToggleReadState.value || (!s.draft && !s.archived) || s.archived
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
    if (props.session.ephemeral && !props.session.draft) return props.session.ephemeralPhase === 'running'
    const state = processState.value?.state
    return state && state !== PROCESS_STATE.DEAD
})

// Stopping state, shared across all UIs via the store.
const stoppingProcess = computed(() => store.isSessionStopping(props.session.id))

// ═══════════════════════════════════════════════════════════════════════════
// Link href for native "open in new tab" support
// ═══════════════════════════════════════════════════════════════════════════

const sessionHref = computed(() => router.resolve(sessionRouteLocation(props.session, route)).href)

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
    // Auto-enter: Shift+clicking while a session is open reads as "select
    // from the open session to here" — enter the mode with the open session
    // as anchor and process the click as a range selection.
    if (event.shiftKey && route.params.sessionId) {
        event.preventDefault()
        selectionStore.enter(route.params.sessionId)
        emit('selection-click', {
            session: props.session,
            shift: true,
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
    // handler runs; suppress it whenever the click will drive the selection
    // (mode active, or about to auto-enter via Shift+click on an open session).
    if (!event.shiftKey) return
    if (selectionStore.active || route.params.sessionId) event.preventDefault()
}

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
    if (session.ephemeral && !session.draft) {
        if (action === 'stop') store.stopEphemeralSession(session.id)
        if (action === 'delete-draft') store.discardEphemeralSession(session.id)
        return
    }
    if (action === 'rename') {
        openRenameDialog(session)
    } else if (action === 'share') {
        // Open the globally-mounted ShareDialog (ProjectView) for this session.
        window.dispatchEvent(new CustomEvent('twicc:open-share-dialog', {
            detail: { sessionId: session.id },
        }))
    } else if (action === 'stop') {
        if (!stoppingProcess.value) stopSessionProcess(session.id)
    } else if (action === 'delete-draft') {
        // If viewing this draft, deselect it first (navigate to project home)
        if (props.active) {
            emit('select', session)
        }
        store.deleteDraftSession(session.id)
    } else if (action === 'archive') {
        // Delegates to the composable: it handles the active-crons confirmation
        // and the combined kill+archive in one place.
        stopSessionProcess(session.id, { archive: true })
    } else if (action === 'unarchive') {
        store.setSessionArchived(session.project_id, session.id, false)
    } else if (action === 'pin-none' || action === 'pin-project' || action === 'pin-workspace' || action === 'pin-all') {
        const requested = action === 'pin-none' ? null : action.slice(4)
        // Re-selecting the currently active mode toggles it off.
        const current = session.pinned || null
        const mode = requested !== null && requested === current ? null : requested
        store.setSessionPinMode(session.project_id, session.id, mode)
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
            'session-item-wrapper--compact': compactView,
            'session-item-wrapper--drag-pending': isDragPending,
            'session-item-wrapper--selected': selected,
        }"
        @dragenter="onDragenter"
        @dragleave="onDragleave"
        @dragover="onDragover"
        @drop="onDrop"
    >
        <wa-button
            :id="`session-button-${session.id}`"
            :href="sessionHref"
            :appearance="active ? 'outlined' : 'plain'"
            :variant="active ? 'brand' : 'neutral'"
            class="session-item"
            :class="{
                'session-item--active': active,
                'session-item--highlighted': highlighted
            }"
            @click="handleClick"
            @mousedown="handleMousedown"
        >
            <div class="session-name-row">
                <!-- Compact mode: inline project color dot (instead of full ProjectBadge line) -->
                <ProjectMark
                    v-if="compactView && effectiveShowProjectName"
                    :id="`compact-project-dot-${session.id}`"
                    :icon-url="store.resolvedProjectIcons[session.project_id] || null"
                    :color="projectDotColor"
                />
                <AppTooltip v-if="compactView && effectiveShowProjectName" :for="`compact-project-dot-${session.id}`">
                    <WorktreeBadge v-if="isProjectWorktree" :project-id="session.project_id" :dot="false" />
                    <template v-else>{{ projectPathTitle(store.getProject(session.project_id)) || store.getProjectDisplayName(session.project_id) }}</template>
                </AppTooltip>
                <wa-icon v-if="session.pinned" name="thumbtack" class="pinned-icon"></wa-icon>
                <wa-tag v-if="session.archived" size="small" variant="neutral" class="archived-tag">Arch.</wa-tag>
                <wa-icon v-else-if="session.ephemeral && !session.draft" name="ghost" label="Ephemeral session" class="ephemeral-icon" :class="session.ephemeralPhase"></wa-icon>
                <wa-tag v-else-if="session.draft && !processState" size="small" variant="warning" class="draft-tag">Draft</wa-tag>
                <ProviderIcon v-if="providerIcon" :provider="session.provider" :colored="false" class="provider-icon" />
                <wa-icon
                    v-if="showWorktreeIcon"
                    :id="`session-worktree-${session.id}`"
                    name="code-branch"
                    auto-width
                    class="session-worktree-icon"
                ></wa-icon>
                <AppTooltip v-if="showWorktreeIcon" :for="`session-worktree-${session.id}`">
                    <WorktreeBadge :project-id="session.project_id" :dot="false" />
                </AppTooltip>
                <span class="session-name">{{ getSessionDisplayName(session) }}</span>
                <!-- Compact mode: code comments indicator -->
                <wa-icon
                    v-if="compactView && hasCodeComments"
                    name="comment"
                    variant="regular"
                    class="compact-comments-indicator"
                ></wa-icon>
                <!-- Compact mode: unread indicator (highest priority) -->
                <wa-icon
                    v-if="compactView && hasUnread"
                    :id="`compact-unread-${session.id}`"
                    name="eye"
                    class="compact-unread-indicator"
                ></wa-icon>
                <AppTooltip v-if="compactView && hasUnread" :for="`compact-unread-${session.id}`">New content to read<template v-if="processState"> · {{ providerLabel }} state: {{ PROCESS_STATE_NAMES[processState.state] }}<template v-if="activeCronCount"> ({{ activeCronCount }} active cron{{ activeCronCount > 1 ? 's' : '' }})</template></template></AppTooltip>
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
                    v-if="compactView && processState && !session.ephemeral && !hasUnread && !pendingRequest"
                    :id="`compact-process-indicator-${session.id}`"
                    :state="processState.state"
                    :has-active-crons="hasActiveCrons"
                    size="small"
                    :animate-states="animateStates"
                    class="compact-process-indicator"
                />
                <AppTooltip v-if="compactView && processState && !session.ephemeral && !hasUnread && !pendingRequest" :for="`compact-process-indicator-${session.id}`">{{ providerLabel }} state: {{ PROCESS_STATE_NAMES[processState.state] }}<template v-if="activeCronCount"> ({{ activeCronCount }} active cron{{ activeCronCount > 1 ? 's' : '' }})</template></AppTooltip>
            </div>
            <!-- Project badge line (hidden in compact mode, dot is shown inline instead) -->
            <!-- When unread + no process: show unread indicator on the project line (right-aligned) -->
            <div v-if="!compactView && (effectiveShowProjectName || hasCodeComments || (hasUnread && !processState))" class="session-project-row">
                <WorktreeBadge v-if="effectiveShowProjectName && isProjectWorktree" :project-id="session.project_id" class="session-project" />
                <ProjectBadge v-else-if="effectiveShowProjectName" :project-id="session.project_id" class="session-project" />
                <wa-icon
                    v-if="hasCodeComments"
                    name="comment"
                    variant="regular"
                    class="session-comments-indicator"
                ></wa-icon>
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
                v-if="!compactView && processState && !session.ephemeral"
                class="process-info"
                :style="{ color: getProcessColor(processState.state) }"
            >
                <span :id="`process-memory-${session.id}`" class="process-memory">
                    <template v-if="processState.memory">
                        {{ formatMemory(processState.memory) }}
                    </template>
                </span>
                <AppTooltip :for="`process-memory-${session.id}`">{{ providerLabel }} memory usage</AppTooltip>

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
                    <AppTooltip v-if="hasUnread" :for="`process-unread-${session.id}`">New content to read · {{ providerLabel }} state: {{ PROCESS_STATE_NAMES[processState.state] }}<template v-if="activeCronCount"> ({{ activeCronCount }} active cron{{ activeCronCount > 1 ? 's' : '' }})</template></AppTooltip>
                    <ProcessIndicator
                        v-else
                        :id="`process-indicator-${session.id}`"
                        :state="processState.state"
                        :has-active-crons="hasActiveCrons"
                        size="small"
                        :animate-states="animateStates"
                    />
                    <AppTooltip v-if="!hasUnread" :for="`process-indicator-${session.id}`">{{ providerLabel }} state: {{ PROCESS_STATE_NAMES[processState.state] }}<template v-if="activeCronCount"> ({{ activeCronCount }} active cron{{ activeCronCount > 1 ? 's' : '' }})</template></AppTooltip>
                </span>
            </div>
            <!-- Meta row (not shown for draft sessions, hidden in compact mode) -->
            <div v-if="!compactView && !session.draft && !session.ephemeral" class="session-meta" :class="{ 'session-meta--no-cost': !showCosts }">
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
        <AppTooltip v-if="showTitleTooltip" :for="`session-button-${session.id}`" placement="right">{{ session.title || session.id }}</AppTooltip>
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
                <wa-icon name="ellipsis-v" label="Session menu"></wa-icon>
            </wa-button>
            <!-- Standard actions -->
            <wa-dropdown-item v-if="!session.ephemeral || session.draft" value="rename">
                <wa-icon slot="icon" name="pencil"></wa-icon>
                Rename
            </wa-dropdown-item>
            <wa-dropdown-item v-if="!session.draft && !session.ephemeral && sharingEnabled" value="share">
                <wa-icon slot="icon" name="share-nodes"></wa-icon>
                Share…
            </wa-dropdown-item>
            <template v-if="!session.draft && !session.ephemeral">
                <wa-divider></wa-divider>
                <wa-dropdown-item type="checkbox" :checked="!session.pinned" value="pin-none">
                    Not pinned
                </wa-dropdown-item>
                <wa-dropdown-item type="checkbox" :checked="session.pinned === 'project'" value="pin-project">
                    Pin in project
                </wa-dropdown-item>
                <wa-dropdown-item type="checkbox" :checked="session.pinned === 'workspace'" value="pin-workspace">
                    Pin in workspace
                </wa-dropdown-item>
                <wa-dropdown-item type="checkbox" :checked="session.pinned === 'all'" value="pin-all">
                    Pin everywhere
                </wa-dropdown-item>
                <wa-divider v-if="hasItemsAfterPinBlock"></wa-divider>
            </template>
            <wa-dropdown-item v-if="canToggleReadState && hasUnread" value="mark-read">
                <wa-icon slot="icon" name="eye-slash"></wa-icon>
                Mark as read
            </wa-dropdown-item>
            <wa-dropdown-item v-if="canToggleReadState && !hasUnread" value="mark-unread">
                <wa-icon slot="icon" name="eye"></wa-icon>
                Mark as unread
            </wa-dropdown-item>
            <wa-dropdown-item v-if="!session.draft && !session.ephemeral && !session.archived" value="archive">
                <wa-icon slot="icon" name="box-archive"></wa-icon>
                {{ canStop ? `Archive (it will stop the ${providerLabel} process)` : 'Archive' }}
            </wa-dropdown-item>
            <wa-dropdown-item v-if="session.archived" value="unarchive">
                <wa-icon slot="icon" name="box-open"></wa-icon>
                Unarchive
            </wa-dropdown-item>
            <!-- Danger actions -->
            <template v-if="canStop || session.draft || session.ephemeral">
                <wa-divider></wa-divider>
                <wa-dropdown-item v-if="canStop" value="stop" :disabled="stoppingProcess">
                    <wa-icon slot="icon" name="ban"></wa-icon>
                    {{ stoppingProcess ? 'Stopping…' : `Stop the ${providerLabel} process` }}
                </wa-dropdown-item>
                <wa-dropdown-item v-if="session.draft || session.ephemeral" value="delete-draft" variant="danger">
                    <wa-icon slot="icon" name="trash"></wa-icon>
                    Discard
                </wa-dropdown-item>
            </template>
        </wa-dropdown>
        <AppTooltip :for="`session-menu-trigger-${session.id}`">Session actions</AppTooltip>
    </div>
</template>

<style scoped>
.ephemeral-icon { flex-shrink: 0; color: var(--wa-color-neutral-60); }
.ephemeral-icon.running { color: var(--wa-color-brand-60); }
.ephemeral-icon.done { color: var(--wa-color-success-60); }
.ephemeral-icon.error { color: var(--wa-color-danger-60); }
.ephemeral-icon.lost { color: var(--wa-color-warning-60); }

.session-item-wrapper {
    position: relative;
    width: 100%;
    padding-inline: var(--wa-space-2xs);
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

/* Multi-select mode: selected item */
.session-item-wrapper--selected .session-item::part(base) {
    background-color: var(--wa-color-brand-fill-quiet);
    box-shadow: inset 0 0 0 1px var(--wa-color-brand-border-quiet);
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

.provider-icon {
    flex-shrink: 0;
}

/* Marker shown before the title when the session lives in a git worktree. */
.session-worktree-icon {
    flex-shrink: 0;
    color: var(--wa-color-text-quiet);
    font-size: 0.85em;
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


/* Compact mode: inline code comments indicator */
.compact-comments-indicator {
    margin-left: auto;
    flex-shrink: 0;
    color: var(--wa-color-brand);
    font-size: var(--wa-font-size-xs);
    position: relative;
    left: -1.5rem;
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
    top: var(--wa-space-2xs);
    right: var(--wa-space-xs);
    z-index: 1;
}
.session-item-wrapper--compact .session-menu {
    top: 0;
    right: var(--wa-space-xs);
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

.session-comments-indicator {
    margin-left: auto;
    flex-shrink: 0;
    color: var(--wa-color-brand);
    font-size: var(--wa-font-size-xs);
}

.standalone-unread-indicator {
    margin-left: auto;
}

/* When comments indicator is present, unread doesn't need margin-left: auto */
.session-comments-indicator + .standalone-unread-indicator {
    margin-left: 0;
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
