<script setup>
import { ref, computed, watch, inject } from 'vue'
import { onClickOutside } from '@vueuse/core'
import { useDataStore } from '../../../stores/data'
import { useSettingsStore } from '../../../stores/settings'
import { formatDate } from '../../../utils/date'
import { PROCESS_STATE, DISPLAY_MODE } from '../../../constants'
import {
    archiveStopLabel,
    backgroundShellCount,
    processStateTooltip,
    userTurnBackgroundShellCount,
} from '../../../utils/backgroundWork'
import { getProviderHelpers, getProviderLabel, getProviderIcon } from '../../../providers'
import ProviderIcon from '../../ui/ProviderIcon.vue'
import { compactHeight, rootFontSizePx } from '../../../utils/compactHeight'
import { getAgentDisplay } from '../../../utils/agentLabel'
import { stopSubagent, interruptSession } from '../../../composables/useWebSocket'
import { stopSessionProcess, hardKillSessionProcess } from '../../../composables/useStopSessionProcess'
import ProjectBadge from '../../project/ProjectBadge.vue'
import ProcessIndicator from '../../ui/ProcessIndicator.vue'
import CodeCommentsIndicator from '../../ui/CodeCommentsIndicator.vue'
import ProcessDuration from '../../ui/ProcessDuration.vue'
import CostDisplay from '../../ui/CostDisplay.vue'
import AppTooltip from '../../ui/AppTooltip.vue'
import { useSharesStore } from '../../../stores/shares'
import { toggleSessionMute } from '../../../composables/useSessionMute'
import { useActionsRowLabels } from '../../../composables/useActionsRowLabels'
import { useCodeCommentsStore } from '../../../stores/codeComments'

const props = defineProps({
    sessionId: {
        type: String,
        required: true
    },
    mode: {
        type: String,
        default: 'session',
        validator: (value) => ['session', 'subagent'].includes(value)
    }
})

const store = useDataStore()
const settingsStore = useSettingsStore()
const sharesStore = useSharesStore()
const codeCommentsStore = useCodeCommentsStore()

// Share entry point (main session only). Disabled when no share host is configured.
// Routes through the globally-mounted dialogs in ProjectView (via the shared
// window event) so an already-shared session opens the manager list first, exactly
// like the artifact entry points — rather than jumping straight to create.
const sharingEnabled = computed(() => !!settingsStore.getUsableShareBaseUrl)
const activeShareCount = computed(() => sharesStore.activeCountForSession(props.sessionId))
function openShare() {
    if (!sharingEnabled.value) return
    window.dispatchEvent(new CustomEvent('twicc:open-share-dialog', {
        detail: { sessionId: props.sessionId, title: session.value?.title || displayName.value },
    }))
}

// Costs setting
const showCosts = computed(() => settingsStore.areCostsShown)

// Session data from store
const session = computed(() => store.getSession(props.sessionId))

// Debug display toggle (dev-mode only, main session): whether this session has
// the debug view forced, and whether it effectively renders in debug mode.
const isSessionDebugForced = computed(() => store.isSessionDebugForced(props.sessionId))
const isEffectiveDebug = computed(() => store.getEffectiveDisplayMode(props.sessionId) === DISPLAY_MODE.DEBUG)
function toggleSessionDebug() {
    store.toggleSessionDebug(props.sessionId)
}
// Whether the session's project is a git worktree of another project — drives
// the worktree marker before the title.
const isProjectWorktree = computed(() => !!store.getProject(session.value?.project_id)?.worktree_of)
const providerLabel = computed(() => getProviderLabel(session.value?.provider))
const providerIcon = computed(() => getProviderIcon(session.value?.provider))

// Whether the session's provider is currently usable for runtime calls.
// Stricter than just intent-enabled: a provider in `starting` / `stopping`
// returns false too, matching the back gate.
const isProviderEnabled = computed(() => {
    const p = session.value?.provider
    return p ? store.isProviderAvailable(p) : true
})

// Get display name for header
// - Session mode: title if available, "New session" for drafts without title, otherwise session ID
// - Subagent mode: the name the launcher gave the agent (see
//   utils/agentLabel.js), or ``Agent "<shortId>"`` when nothing named it
const displayName = computed(() => {
    if (props.mode === 'subagent') {
        const { name, isFallback } = getAgentDisplay(props.sessionId, store)
        return isFallback ? `Agent "${name}"` : name
    }
    // For draft sessions without a title, show "New session"
    if (session.value?.draft && !session.value?.title) {
        return 'New session'
    }
    return session.value?.title || props.sessionId
})

// Cost values for header display
const totalCost = computed(() => {
    const sess = session.value
    if (!sess) return null
    return sess.total_cost ?? null
})

// Cost breakdown (self + subagents) - only shown if subagents have cost
const costBreakdown = computed(() => {
    const sess = session.value
    if (!sess) return null

    const subagentsCost = sess.subagents_cost
    if (subagentsCost == null || subagentsCost <= 0) return null

    return {
        self: sess.self_cost ?? null,
        subagents: subagentsCost,
    }
})

// Calculate context usage percentage based on session's effective context_max
// (the store getter applies the auto-force-to-1M rule when usage exceeds 85%
// of the 200K window with no active process).
const contextMax = computed(() => store.getEffectiveContextMax(props.sessionId))

const contextUsagePercentage = computed(() => {
    const usage = session.value?.context_usage
    if (usage == null) return null
    return Math.round((usage / contextMax.value) * 100)
})

// Tooltip text for context usage ring. Resolve the choice label through
// the session's own provider helpers so non-Claude providers (Codex,
// future ones) can render their own ``context_max`` choice catalogue
// (e.g. "272K" for gpt-5). Falls back to a rounded "XK" label when the
// helper returns nothing — covers absent helpers and values not in the
// provider's choice list.
const contextUsageTooltip = computed(() => {
    const helpers = getProviderHelpers(session.value?.provider)
    const label = helpers?.getChoiceLabel('context_max', contextMax.value) || `${Math.round(contextMax.value / 1000)}K`
    return `Context window usage (${label} max)`
})

// Get indicator color for context usage based on thresholds
const contextUsageColor = computed(() => {
    const pct = contextUsagePercentage.value
    if (pct == null) return null
    if (pct > 70) return 'var(--wa-color-danger)'
    if (pct > 50) return 'var(--wa-color-warning)'
    return 'var(--glow-context-ring)'
})

// Display directory: git_directory if available, otherwise cwd. For a draft
// session — which has neither yet — fall back to the target project's path
// (git root if known, else its directory), i.e. where the session will run.
const displayDirectory = computed(() => {
    if (session.value?.git_directory) return session.value.git_directory
    if (session.value?.cwd) return session.value.cwd
    if (session.value?.draft) {
        const project = store.getProject(session.value?.project_id)
        return project?.git_root || project?.directory || null
    }
    return null
})

// Directory split in "parent folders" + "last folder" so the template can emphasise the folder
// that identifies the checkout (the last one, e.g. the worktree name).
const displayDirectoryParts = computed(() => {
    const dir = displayDirectory.value
    if (!dir) return null
    const trimmed = dir.length > 1 ? dir.replace(/\/+$/, '') : dir
    const cut = trimmed.lastIndexOf('/') + 1
    return { head: trimmed.slice(0, cut), last: trimmed.slice(cut) }
})

// Tooltip for directory: indicate whether it's the resolved git directory, the
// cwd fallback, or — for a draft — the target project's git root / directory.
const displayDirectoryTooltip = computed(() => {
    if (session.value?.git_directory) return 'Git working directory'
    if (session.value?.cwd) return 'Working directory (cwd)'
    if (session.value?.draft) {
        const project = store.getProject(session.value?.project_id)
        return project?.git_root ? 'Project git root' : 'Project directory'
    }
    return null
})

// Format model name for display from pre-parsed family and version
const formattedModel = computed(() => {
    const model = session.value?.model
    if (!model?.family || !model?.version) return null
    return `${model.family} ${model.version}`
})

// Process state for current session
const processState = computed(() => store.getProcessState(props.sessionId))

/** Whether the process has active cron jobs. */
const hasActiveCrons = computed(() => processState.value?.active_crons?.length > 0)

/** Background shells still running behind a finished turn (terminal icon). */
const userTurnBackgroundShells = computed(() => userTurnBackgroundShellCount(processState.value))

/** Process-state tooltip: state, background shells, active crons. */
const processTooltip = computed(() =>
    processState.value ? processStateTooltip(providerLabel.value, processState.value) : ''
)

/**
 * Format memory in bytes to a human-readable string.
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

// Only assistant_turn should animate
const animateStates = ['assistant_turn']

// Check if process can be stopped (any state except dead, and not a synthetic process state)
const canStopProcess = computed(() => {
    const ps = processState.value
    return ps && !ps.synthetic && ps.state && ps.state !== PROCESS_STATE.DEAD
})

// Check if this is a background agent that can be stopped
const canStopAgent = computed(() => {
    if (props.mode !== 'subagent') return false
    const ps = processState.value
    if (session.value?.ephemeral || !ps || !ps.synthetic || !ps.state || ps.state === PROCESS_STATE.DEAD) return false
    const parentId = session.value?.parent_session_id
    if (!parentId) return false
    if (!store.isAgentRunning(props.sessionId) || !store.getAgentRunState(props.sessionId)?.runBackground) return false
    // Provider opt-out for backends that don't (or can't) stop a
    // running subagent — see ``BaseProviderHelpers.canStopSubagent``.
    return !!getProviderHelpers(session.value?.provider)?.canStopSubagent()
})

// Track when a stop request has been sent and we're waiting for the process to die.
// Sourced from the store so it stays in sync across all UIs (sidebar, header, shortcut).
const stoppingProcess = computed(() => store.isSessionStopping(props.sessionId))
const stoppingAgent = ref(false)

// Reset stoppingAgent when the agent stops running
watch(canStopAgent, (canStop) => {
    if (!canStop) {
        stoppingAgent.value = false
    }
})

/**
 * Stop the current process. A plain click runs the graceful stop; Shift-click,
 * or clicking again while a stop is already in flight (escalation), hard-kills
 * the process tree now — no grace window, no confirmation.
 */
function handleStopProcess(event) {
    if (session.value?.ephemeral && !session.value?.draft) {
        store.stopEphemeralSession(props.sessionId)
        return
    }
    if (event?.shiftKey || stoppingProcess.value) {
        hardKillSessionProcess(props.sessionId)
        return
    }
    stopSessionProcess(props.sessionId)
}

/**
 * Stop the current agent via the SDK's stop_task.
 * No confirmation dialog needed for agents (no crons).
 */
function handleStopAgent() {
    const parentId = session.value?.parent_session_id
    if (canStopAgent.value && !stoppingAgent.value && parentId) {
        stoppingAgent.value = true
        stopSubagent(parentId, props.sessionId)
    }
}

// Whether the current turn can be interrupted in place (without killing the
// session). Only while a real process is actively working (ASSISTANT_TURN), on
// a provider whose runtime wired the soft-interrupt hook (Claude Code SDK +
// hybrid, Codex). The button auto-hides as soon as the turn ends (state leaves
// ASSISTANT_TURN).
const canInterruptTurn = computed(() => {
    const ps = processState.value
    if (session.value?.ephemeral || !ps || ps.synthetic || ps.state !== PROCESS_STATE.ASSISTANT_TURN) return false
    return !!getProviderHelpers(session.value?.provider)?.canInterruptTurn()
})

// Transient "interrupt sent, awaiting the turn to wind down" feedback. Resets
// itself once the turn ends (the button hides), so a stale flag can't linger.
const interrupting = ref(false)
watch(canInterruptTurn, (can) => {
    if (!can) interrupting.value = false
})

/**
 * Interrupt the current turn while keeping the session alive (back to
 * USER_TURN). No confirmation: it is non-destructive and recoverable.
 */
function handleInterrupt() {
    if (!canInterruptTurn.value || interrupting.value) return
    interrupting.value = true
    interruptSession(props.sessionId)
    // Safety net: the watch above clears the spinner on the normal USER_TURN
    // transition. But a hybrid interrupt can fail (a TUI dialog stays up past
    // its ~15s backend cap), leaving the turn running — clear the transient
    // feedback anyway so the button doesn't spin forever.
    setTimeout(() => { interrupting.value = false }, 17000)
}


// ═══════════════════════════════════════════════════════════════════════════
// Compact header mode on small viewports
// ═══════════════════════════════════════════════════════════════════════════

// Track expanded state of the compact header overlay
const isCompactExpanded = ref(false)

// Rename dialog (provided by ProjectView)
const injectedOpenRenameDialog = inject('openRenameDialog')

// Reference to the header element
const headerRef = ref(null)

function toggleCompact() {
    isCompactExpanded.value = !isCompactExpanded.value
}


// ═══════════════════════════════════════════════════════════════════════════
// Action buttons: layout follows the header width
// ═══════════════════════════════════════════════════════════════════════════

// The actions row spells out each button's name when it fits on one line (see useActionsRowLabels).
// What the row holds, or the size of its text, changing forgets the recorded wrap width.
const actionsRef = ref(null)
const actionsLabels = useActionsRowLabels(headerRef, actionsRef, () => [
    props.sessionId, session.value?.draft, session.value?.ephemeral, session.value?.archived,
    settingsStore.isDevMode, compactHeight.value, rootFontSizePx.value,
])

// The compact panel is a popup: a click anywhere else closes it. The panel is
// a child of the header, so one target covers it, and VueUse walks the
// composed path — a click inside a Web Awesome popup (pin dropdown, tooltip)
// stays "inside". Clicks inside an iframe (Browser pane, artifact preview)
// never reach this document, so they cannot close it.
onClickOutside(headerRef, () => {
    isCompactExpanded.value = false
})

// Unsent code comments of the session, whichever tab they were written in. The indicator is part of the
// session's status, with the process state: next to the process icon when the header is compact and
// closed, in the process chip of the panel and of the full header otherwise.
const codeCommentsCount = computed(() => codeCommentsStore.countBySession(session.value?.project_id, props.sessionId))

// A request waiting for the user. Its hand lives with the actions, which the
// collapsed compact header hides, so the compact live group shows it too.
const hasPendingRequest = computed(() => store.getPendingRequests(props.sessionId).length > 0)

/**
 * Open the rename dialog.
 * @param {Object} options
 * @param {boolean} options.showHint - Show contextual hint (when opened during message send)
 */
function openRenameDialog({ showHint = false } = {}) {
    if (session.value) {
        injectedOpenRenameDialog(session.value, { showHint })
    }
}

/**
 * Archive the current session.
 * Also stops the process if running — archived and running are mutually exclusive.
 * If the process has active crons or background shells, the composable shows the confirmation dialog.
 */
function handleArchive() {
    if (!session.value || session.value.archived || (session.value.draft || session.value.ephemeral)) return
    stopSessionProcess(props.sessionId, { archive: true })
}

/**
 * Unarchive the current session.
 */
function handleUnarchive() {
    if (session.value?.archived) {
        store.setSessionArchived(session.value.project_id, props.sessionId, false)
    }
}

/**
 * Open/close the in-session search bar — the clickable equivalent of Ctrl+F.
 * Dispatches the same window event the keyboard shortcut uses; the currently
 * active SessionItemsList toggles its search bar (prefilling from the current
 * selection when opening). Stateless here: the button is a pure trigger, so the
 * mobile/keyboard-less path matches Ctrl+F exactly.
 *
 * An expanded compact header sits on top of the search bar that appears just
 * below it, so collapse it on click (no-op when it is already collapsed, and on
 * tall viewports).
 */
function toggleSessionSearch() {
    isCompactExpanded.value = false
    window.dispatchEvent(new CustomEvent('twicc:toggle-session-search', { detail: { handled: false } }))
}

const searchTooltip = computed(() => `Search in conversation (${settingsStore.isMac ? '⌘F' : 'Ctrl+F'})`)

/**
 * Label shown on the pin button tooltip, reflecting the current pin mode.
 */
const PIN_MODE_LABELS = { project: 'Project', workspace: 'Workspace', all: 'All projects' }
const pinTooltip = computed(() => {
    if (!session.value?.pinned) return 'Pin session'
    return `Pinned: ${PIN_MODE_LABELS[session.value.pinned] || session.value.pinned}`
})

// The mute button suppresses two things at once: the "finished working"
// notifications (toast, sound, browser, Apprise) and the session's unread
// state. The unread half always applies, so the button is never a no-op.
const muteTooltip = computed(() => (
    session.value?.mute_on_user_turn
        ? 'Muted — click to restore the "finished working" notification and the unread flag'
        : 'Notifications on — click to silence the "finished working" notification and stop this session showing as unread'
))

function handleMuteToggle() {
    toggleSessionMute(props.sessionId)
}

/**
 * Handle pin mode selection from the dropdown.
 * @param {CustomEvent} event - The wa-select event (event.detail.item.value)
 */
function handlePinSelect(event) {
    if (!session.value || (session.value.draft || session.value.ephemeral)) return
    const value = event.detail.item.value
    const requested = value === 'none' ? null : value
    // Re-selecting the currently active mode toggles it off.
    const current = session.value.pinned || null
    const mode = requested !== null && requested === current ? null : requested
    store.setSessionPinMode(session.value.project_id, props.sessionId, mode)
}

// Expose methods and refs for parent components
defineExpose({
    openRenameDialog,
    headerRef,
    isCompactExpanded,
})
</script>

<template>
    <header ref="headerRef" class="session-header" :class="{ 'compact-expanded': isCompactExpanded, 'compact-collapsed': !isCompactExpanded, 'effective-debug': isEffectiveDebug, 'actions-labels': actionsLabels }" :data-session-type="mode" v-if="session">
        <!-- Row 1: state markers, provider, title, context ring (compact only). A click on the row
             toggles the compact panel. -->
        <div v-if="mode === 'session'" class="session-title" @click="toggleCompact">
            <!-- Ephemeral marker: the sidebar's ghost, with the same colours. A state like draft, so it stays
                 too; a draft can be ephemeral, and then both icons show. -->
            <wa-icon
                v-if="session.ephemeral"
                :id="`session-header-${sessionId}-ephemeral-icon`"
                name="ghost"
                label="Ephemeral session"
                class="session-state-icon session-state-icon--ephemeral"
                :class="session.ephemeralPhase"
            ></wa-icon>
            <AppTooltip v-if="session.ephemeral" :for="`session-header-${sessionId}-ephemeral-icon`">Ephemeral session</AppTooltip>

            <!-- Stale marker: the session's files are gone from disk. Same family as draft. -->
            <wa-icon
                v-if="session.stale"
                :id="`session-header-${sessionId}-stale-icon`"
                name="link-slash"
                label="Session files deleted"
                class="session-state-icon session-state-icon--stale"
            ></wa-icon>
            <AppTooltip v-if="session.stale" :for="`session-header-${sessionId}-stale-icon`">Session files were deleted from disk</AppTooltip>

            <!-- Archived marker: the sidebar's archive icon, in its yellow. A state, so it shows in every
                 header state; the unarchive button, with the actions, is the way out. -->
            <wa-icon
                v-if="session.archived"
                :id="`session-header-${sessionId}-archived-icon`"
                name="box-archive"
                label="Archived session"
                class="session-state-icon session-state-icon--archived"
            ></wa-icon>
            <AppTooltip v-if="session.archived" :for="`session-header-${sessionId}-archived-icon`">Archived session</AppTooltip>

            <!-- Draft marker: a state, not an action, so it shows whether the compact panel is open or not.
                 Archived has its own marker above. -->
            <wa-icon
                v-if="!session.archived && session.draft && !processState"
                :id="`session-header-${sessionId}-draft-icon`"
                name="file-pen"
                label="Draft"
                class="session-state-icon session-state-icon--draft"
            ></wa-icon>
            <AppTooltip v-if="!session.archived && session.draft && !processState" :for="`session-header-${sessionId}-draft-icon`">Draft</AppTooltip>

            <!-- Worktree marker: only when the session's project is a git worktree.
                 Its tooltip restates that the session runs in a worktree and embeds the same
                 worktree badge shown on the project row (parent repo + branch icon + worktree folder). -->
            <wa-icon
                v-if="isProjectWorktree"
                :id="`session-header-${sessionId}-worktree`"
                auto-width
                name="code-branch"
                class="worktree-title-icon"
            ></wa-icon>
            <AppTooltip v-if="isProjectWorktree" :for="`session-header-${sessionId}-worktree`">
                <div class="worktree-title-tooltip">
                    <span>This session runs in a git worktree</span>
                    <ProjectBadge :project-id="session.project_id" />
                </div>
            </AppTooltip>

            <ProviderIcon
                v-if="providerIcon"
                :provider="session?.provider"
                class="session-provider-icon"
            />

            <h2 :id="`session-header-${sessionId}-title`">{{ displayName }}</h2>
            <AppTooltip :for="`session-header-${sessionId}-title`">{{ displayName }}</AppTooltip>

            <!-- Compact status (visible only at compact height, panel closed: the panel shows the same things).
                 The two facts to know at a glance: how full the context is, and what the agent is doing. A
                 pending request takes the place of the process indicator: its hand is with the actions, which
                 the collapsed header hides. Unframed. Its cells are as wide as the segments of the controls
                 under them, and sit right above: the process state over the stop button, the ring over the
                 panel toggle, so the right edge of the two rows reads as one column. -->
            <div v-if="contextUsagePercentage != null || hasPendingRequest || processState || codeCommentsCount > 0" class="compact-status">
                <!-- The status icons (code comments, then the process state) are one group, centred over the stop
                     segment below; the context ring has the toggle's share. -->
                <div v-if="codeCommentsCount > 0 || hasPendingRequest || processState" class="compact-status-cell compact-status-cell--state">
                    <CodeCommentsIndicator v-if="codeCommentsCount > 0" :count="codeCommentsCount" />

                    <wa-icon
                        v-if="hasPendingRequest"
                        :id="`session-header-${sessionId}-compact-pending`"
                        name="hand"
                        class="pending-request-indicator"
                    ></wa-icon>
                    <AppTooltip v-if="hasPendingRequest" :for="`session-header-${sessionId}-compact-pending`">Waiting for your response</AppTooltip>

                    <ProcessIndicator
                        v-else-if="processState"
                        class="compact-process-indicator"
                        :state="processState.state"
                        :has-active-crons="hasActiveCrons"
                        :background-shells="userTurnBackgroundShells"
                        size="small"
                        :animate-states="animateStates"
                    />
                </div>
                <div class="compact-status-cell compact-status-cell--ring">
                    <wa-progress-ring
                        v-if="contextUsagePercentage != null"
                        class="context-usage-ring compact-context-ring"
                        :value="Math.min(contextUsagePercentage, 100)"
                        :style="{
                            '--indicator-color': contextUsageColor
                        }"
                    ><span class="wa-font-weight-bold">{{ contextUsagePercentage }}</span></wa-progress-ring>
                </div>
            </div>
        </div>

        <!-- Row 2: the project (or worktree) badge, always under the markers. At compact height it also
             carries the process controls and the button that opens the panel. -->
        <div v-if="mode === 'session'" class="session-project-row" @click="toggleCompact">
            <!-- The arrow after the badge says the link goes up to the project; it is part of the link, so it is
                 part of what is clickable (the project header's navigation puts it before, here it follows). -->
            <router-link
                v-if="session.project_id"
                :id="`session-header-${sessionId}-project-link`"
                :to="{ name: 'project', params: { projectId: session.project_id } }"
                class="session-project"
                @click.stop
            >
                <ProjectBadge :project-id="session.project_id" />
                <wa-icon name="arrow-up" auto-width class="session-project-up"></wa-icon>
            </router-link>
            <AppTooltip v-if="session.project_id" :for="`session-header-${sessionId}-project-link`">Go to the project</AppTooltip>

            <!-- Compact controls (compact height only), in one pill like the outlined buttons at the top of the
                 sidebar: the interrupt and stop buttons (panel closed only: the panel has the same buttons) and
                 the panel toggle. The tooltips share the v-if of their button, see the note in the process zone. -->
            <div class="compact-pill">
            <div class="compact-live">
                <wa-button
                    v-if="canInterruptTurn"
                    :id="`session-header-${sessionId}-compact-interrupt-button`"
                    variant="brand"
                    appearance="plain"
                    size="small"
                    class="compact-live-button reduced-height"
                    :loading="interrupting"
                    :disabled="interrupting"
                    @click.stop="handleInterrupt"
                >
                    <wa-icon name="circle-stop" label="Interrupt"></wa-icon>
                </wa-button>
                <AppTooltip v-if="canInterruptTurn" :for="`session-header-${sessionId}-compact-interrupt-button`">Interrupt the current turn (keeps the session alive)</AppTooltip>

                <wa-button
                    v-if="canStopProcess"
                    :id="`session-header-${sessionId}-compact-stop-button`"
                    variant="danger"
                    appearance="plain"
                    size="small"
                    class="compact-live-button reduced-height"
                    :class="{ forcing: stoppingProcess }"
                    @click.stop="handleStopProcess($event)"
                >
                    <wa-icon :name="stoppingProcess ? 'skull-crossbones' : 'ban'" :label="stoppingProcess ? 'Force kill' : 'Stop'"></wa-icon>
                </wa-button>
                <AppTooltip v-if="canStopProcess" :for="`session-header-${sessionId}-compact-stop-button`">{{ stoppingProcess ? 'Force kill' : `Stop the ${providerLabel} process` }}</AppTooltip>
            </div>

            <!-- Panel toggle (compact height only): the tools icon says there is more, the chevron says
                 it unfolds. The open state reads from the active styling. -->
            <wa-button
                :id="`session-header-${sessionId}-compact-toggle`"
                variant="brand"
                appearance="plain"
                size="small"
                :class="['compact-tool-button', 'reduced-height', { 'compact-tool-button--active': isCompactExpanded, 'compact-tool-button--pending': hasPendingRequest }]"
                @click.stop="toggleCompact"
            >
                <wa-icon name="screwdriver-wrench" label="Toggle details"></wa-icon>
                <wa-icon class="compact-tool-chevron" :name="isCompactExpanded ? 'chevron-up' : 'chevron-down'"></wa-icon>
            </wa-button>
            </div>
        </div>

        <!-- Collapsible rows: identity + stats + process (overlay on small viewports) -->
        <div class="session-collapsible-rows" :class="{ 'glass-surface': compactHeight }">

            <!-- Identity: directory (truncated from the left, so the last folder always stays visible) and
                 branch. For a draft, displayDirectory falls back to the project path and there is no
                 branch yet, so only the folder shows. -->
            <div v-if="displayDirectory || session.git_branch" class="session-git-info">
                <span v-if="displayDirectory" :id="`session-header-${sessionId}-git-directory`" class="git-info-item git-directory">
                    <wa-icon auto-width name="folder-open" variant="regular"></wa-icon>
                    <span class="git-directory-text"><span class="git-directory-inner">{{ displayDirectoryParts.head }}<strong>{{ displayDirectoryParts.last }}</strong></span></span>
                </span>
                <AppTooltip v-if="displayDirectory" :for="`session-header-${sessionId}-git-directory`">{{ displayDirectoryTooltip }}</AppTooltip>

                <span v-if="session.git_branch" :id="`session-header-${sessionId}-git-branch`" class="git-info-item git-branch">
                    <wa-icon auto-width name="code-branch"></wa-icon>
                    <span class="git-branch-name">{{ session.git_branch }}</span>
                </span>
                <AppTooltip v-if="session.git_branch" :for="`session-header-${sessionId}-git-branch`">Git branch</AppTooltip>
            </div>

            <!-- Action buttons (main session), right under the identity row: part of the compact panel, and
                 always visible at full height. -->
            <div v-if="mode === 'session'" ref="actionsRef" class="session-actions">
                <!-- In-session search trigger: clickable equivalent of Ctrl+F (not for drafts) -->
                <wa-button
                    v-if="!session.draft && !session.ephemeral"
                    :id="`session-header-${sessionId}-search-button`"
                    variant="neutral"
                    appearance="plain"
                    size="small"
                    class="search-button reduced-height"
                    @click="toggleSessionSearch"
                >
                    <wa-icon auto-width name="magnifying-glass" label="Search"></wa-icon>
                    <span class="action-label">Search</span>
                </wa-button>
                <AppTooltip v-if="!session.draft && !session.ephemeral" :for="`session-header-${sessionId}-search-button`">{{ searchTooltip }}</AppTooltip>

                <!-- Pin mode dropdown (not for drafts) -->
                <wa-dropdown
                    v-if="!session.draft && !session.ephemeral"
                    class="pin-dropdown"
                    placement="bottom-start"
                    @wa-select="handlePinSelect"
                >
                    <wa-button
                        :id="`session-header-${sessionId}-pin-button`"
                        slot="trigger"
                        :variant="session.pinned ? 'brand' : 'neutral'"
                        appearance="plain"
                        size="small"
                        :class="['pin-button', 'reduced-height', { 'pin-button--active': session.pinned }]"
                    >
                        <wa-icon auto-width name="thumbtack" label="Pin"></wa-icon>
                        <span class="action-label">Pin</span>
                    </wa-button>
                    <wa-dropdown-item type="checkbox" :checked="!session.pinned" value="none">
                        Not pinned
                    </wa-dropdown-item>
                    <wa-dropdown-item type="checkbox" :checked="session.pinned === 'project'" value="project">
                        Pin in project
                    </wa-dropdown-item>
                    <wa-dropdown-item type="checkbox" :checked="session.pinned === 'workspace'" value="workspace">
                        Pin in workspace
                    </wa-dropdown-item>
                    <wa-dropdown-item type="checkbox" :checked="session.pinned === 'all'" value="all">
                        Pin everywhere
                    </wa-dropdown-item>
                </wa-dropdown>
                <AppTooltip v-if="!session.draft && !session.ephemeral" :for="`session-header-${sessionId}-pin-button`">{{ pinTooltip }}</AppTooltip>

                <wa-button
                    v-if="!session.draft && !session.ephemeral"
                    :id="`session-header-${sessionId}-mute-button`"
                    :variant="session.mute_on_user_turn ? 'warning' : 'neutral'"
                    appearance="plain"
                    size="small"
                    :class="['mute-button', 'reduced-height', {
                        'mute-button--active': session.mute_on_user_turn,
                    }]"
                    @click="handleMuteToggle"
                >
                    <wa-icon
                        :name="session.mute_on_user_turn ? 'bell-slash' : 'bell'"
                        :label="session.mute_on_user_turn ? 'Muted' : 'Notifications on'"
                    ></wa-icon>
                    <span class="action-label">{{ session.mute_on_user_turn ? 'Muted' : 'Mute' }}</span>
                </wa-button>
                <AppTooltip
                    v-if="!session.draft && !session.ephemeral"
                    :for="`session-header-${sessionId}-mute-button`"
                >{{ muteTooltip }}</AppTooltip>

                <!-- Archive button (not for drafts or already archived) -->
                <wa-button
                    v-if="!session.archived && !session.draft && !session.ephemeral"
                    :id="`session-header-${sessionId}-archive-button`"
                    variant="neutral"
                    appearance="plain"
                    size="small"
                    class="archive-button reduced-height"
                    @click="handleArchive"
                >
                    <wa-icon auto-width name="box-archive" label="Archive"></wa-icon>
                    <span class="action-label">Archive</span>
                </wa-button>
                <AppTooltip v-if="!session.archived && !session.draft && !session.ephemeral" :for="`session-header-${sessionId}-archive-button`">{{ canStopProcess ? archiveStopLabel('Archive session', providerLabel, backgroundShellCount(processState)) : 'Archive session' }}</AppTooltip>

                <!-- Unarchive button (archived sessions only) -->
                <wa-button
                    v-if="session.archived"
                    :id="`session-header-${sessionId}-unarchive-button`"
                    variant="neutral"
                    appearance="plain"
                    size="small"
                    class="archive-button archive-button--archived reduced-height"
                    @click="handleUnarchive"
                >
                    <wa-icon auto-width name="box-archive" label="Unarchive"></wa-icon>
                    <span class="action-label">Unarchive</span>
                </wa-button>
                <AppTooltip v-if="session.archived" :for="`session-header-${sessionId}-unarchive-button`">Unarchive session</AppTooltip>

                <!-- Rename button (only for main session) -->
                <wa-button
                    v-if="mode === 'session'"
                    :id="`session-header-${sessionId}-rename-button`"
                    variant="neutral"
                    appearance="plain"
                    size="small"
                    class="rename-button reduced-height"
                    :disabled="!isProviderEnabled"
                    @click="openRenameDialog"
                >
                    <wa-icon auto-width name="pencil" label="Rename"></wa-icon>
                    <span class="action-label">Rename</span>
                </wa-button>
                <AppTooltip :for="`session-header-${sessionId}-rename-button`">{{ isProviderEnabled ? 'Rename session' : 'Cannot rename: provider is disabled.' }}</AppTooltip>

                <!-- Debug view toggle (dev mode only, main session): forces the debug
                     display mode for this session without touching the global setting -->
                <wa-button
                    v-if="mode === 'session' && !session.ephemeral && settingsStore.isDevMode"
                    :id="`session-header-${sessionId}-debug-button`"
                    :variant="isSessionDebugForced ? 'brand' : 'neutral'"
                    appearance="plain"
                    size="small"
                    :class="['debug-button', 'reduced-height', { 'debug-button--active': isSessionDebugForced }]"
                    @click="toggleSessionDebug"
                >
                    <wa-icon auto-width name="bug" label="Debug view"></wa-icon>
                    <span class="action-label">Debug</span>
                </wa-button>
                <AppTooltip v-if="mode === 'session' && !session.ephemeral && settingsStore.isDevMode" :for="`session-header-${sessionId}-debug-button`">{{ isSessionDebugForced ? 'Debug view forced for this session — click to restore the global mode' : 'Force the debug view for this session only' }}</AppTooltip>

                <!-- Share button (main session only) -->
                <wa-button
                    v-if="mode === 'session' && !session.draft && !session.ephemeral"
                    :id="`session-header-${sessionId}-share-button`"
                    :variant="activeShareCount > 0 ? 'brand' : 'neutral'"
                    appearance="plain"
                    size="small"
                    :class="['share-button', 'reduced-height', { 'share-button--active': activeShareCount > 0 }]"
                    :disabled="!sharingEnabled"
                    @click="openShare"
                >
                    <wa-icon auto-width name="share-nodes" label="Share"></wa-icon>
                    <span class="action-label">Share</span>
                </wa-button>
                <AppTooltip :for="`session-header-${sessionId}-share-button`">
                    {{ sharingEnabled
                        ? (activeShareCount > 0 ? `Share session (${activeShareCount} active link${activeShareCount > 1 ? 's' : ''})` : 'Share session')
                        : 'Configure a share host in Settings → Sharing to create links' }}
                </AppTooltip>

                <!-- Pending request indicator (shown when waiting for user response) -->
                <wa-icon
                    v-if="store.getPendingRequests(sessionId).length > 0"
                    :id="`session-header-${sessionId}-pending-request`"
                    name="hand"
                    class="pending-request-indicator"
                ></wa-icon>
                <AppTooltip v-if="store.getPendingRequests(sessionId).length > 0" :for="`session-header-${sessionId}-pending-request`">Waiting for your response</AppTooltip>
            </div>

            <!-- Stats container (not shown for draft sessions): labelled segments + the context ring -->
            <div v-if="!session.draft && !session.ephemeral" class="session-stats">
                <div class="stats-grid">

                    <div :id="`session-header-${sessionId}-messages`" class="stat">
                        <span class="stat-label">Messages</span>
                        <span class="stat-value">
                            <wa-icon auto-width name="comment" variant="regular"></wa-icon>
                            <span>{{ session.user_message_count ?? '??' }}</span>
                        </span>
                    </div>
                    <AppTooltip :for="`session-header-${sessionId}-messages`">Number of message turns</AppTooltip>

                    <!-- Debug only -->
                    <div :id="`session-header-${sessionId}-lines`" class="stat stat-debug">
                        <span class="stat-label">Lines</span>
                        <span class="stat-value">
                            <wa-icon auto-width name="bars"></wa-icon>
                            <span>{{ session.last_line }}</span>
                        </span>
                    </div>
                    <AppTooltip :for="`session-header-${sessionId}-lines`">Lines in the JSONL file</AppTooltip>

                    <div :id="`session-header-${sessionId}-mtime`" class="stat">
                        <span class="stat-label">Activity</span>
                        <span class="stat-value">
                            <wa-icon auto-width name="clock" variant="regular"></wa-icon>
                            <span>{{ formatDate(session.mtime, { smart: true }) }}</span>
                        </span>
                    </div>
                    <AppTooltip :for="`session-header-${sessionId}-mtime`">Last activity</AppTooltip>

                    <template v-if="showCosts && totalCost != null">
                        <div :id="`session-header-${sessionId}-cost`" class="stat">
                            <span class="stat-label">Cost</span>
                            <CostDisplay :cost="totalCost" class="stat-value" />
                        </div>
                        <AppTooltip :for="`session-header-${sessionId}-cost`">Total session cost</AppTooltip>
                    </template>

                    <!-- Debug only -->
                    <template v-if="showCosts && costBreakdown">
                        <div :id="`session-header-${sessionId}-cost-breakdown`" class="stat stat-debug">
                            <span class="stat-label">Cost split</span>
                            <span class="stat-value">
                                <CostDisplay :cost="costBreakdown.self" />
                                <span class="cost-breakdown-separator">+</span>
                                <CostDisplay :cost="costBreakdown.subagents" />
                            </span>
                        </div>
                        <AppTooltip :for="`session-header-${sessionId}-cost-breakdown`">Main agent cost + sub-agents cost</AppTooltip>
                    </template>

                    <template v-if="formattedModel">
                        <div :id="`session-header-${sessionId}-model`" class="stat">
                            <span class="stat-label">Model</span>
                            <span class="stat-value">
                                <ProviderIcon v-if="providerIcon" :provider="session?.provider" />
                                <wa-icon v-else auto-width name="robot" variant="classic"></wa-icon>
                                <span>{{ formattedModel }}</span>
                            </span>
                        </div>
                        <AppTooltip :for="`session-header-${sessionId}-model`">Last used model</AppTooltip>
                    </template>

                </div>

                <template v-if="contextUsagePercentage != null">
                    <div class="stat stat-context">
                        <span class="stat-label">Context</span>
                        <wa-progress-ring
                            :id="`session-header-${sessionId}-context`"
                            class="context-usage-ring"
                            :value="Math.min(contextUsagePercentage, 100)"
                            :style="{
                                '--indicator-color': contextUsageColor
                            }"
                        ><span class="wa-font-weight-bold">{{ contextUsagePercentage }}%</span></wa-progress-ring>
                    </div>
                    <AppTooltip :for="`session-header-${sessionId}-context`">{{ contextUsageTooltip }}</AppTooltip>
                </template>
            </div>

            <!-- Process: status chip (indicator first, then turn duration and memory) + control buttons -->
            <div v-if="!session.draft && !session.ephemeral && (processState || codeCommentsCount > 0)" class="meta-process">
                <span class="process-chip">
                    <!-- The code comments come first: they belong to the status, with the process state. -->
                    <CodeCommentsIndicator :count="codeCommentsCount" />

                    <ProcessIndicator
                        v-if="processState"
                        :id="`session-header-${sessionId}-process-indicator`"
                        :state="processState.state"
                        :has-active-crons="hasActiveCrons"
                        :background-shells="userTurnBackgroundShells"
                        size="small"
                        :animate-states="animateStates"
                    />
                    <AppTooltip v-if="processState" :for="`session-header-${sessionId}-process-indicator`">{{ processTooltip }}</AppTooltip>

                    <ProcessDuration
                        v-if="processState?.state === PROCESS_STATE.ASSISTANT_TURN && processState.state_changed_at"
                        :state-changed-at="processState.state_changed_at"
                        :id="`session-header-${sessionId}-process-duration`"
                        class="process-duration"
                    />
                    <AppTooltip v-if="processState?.state === PROCESS_STATE.ASSISTANT_TURN && processState.state_changed_at" :for="`session-header-${sessionId}-process-duration`">Assistant turn duration</AppTooltip>

                    <span
                        v-if="processState?.memory"
                        :id="`session-header-${sessionId}-process-memory`"
                        class="process-memory"
                    >
                        {{ formatMemory(processState.memory) }}
                    </span>
                    <AppTooltip v-if="processState?.memory" :for="`session-header-${sessionId}-process-memory`">{{ providerLabel }} memory usage</AppTooltip>
                </span>

                <wa-button-group v-if="canInterruptTurn || canStopProcess || canStopAgent" class="process-actions" label="Process controls">
                    <wa-button
                        v-if="canInterruptTurn"
                        :id="`session-header-${sessionId}-interrupt-button`"
                        variant="neutral"
                        appearance="outlined"
                        size="small"
                        class="stop-button reduced-height"
                        :loading="interrupting"
                        :disabled="interrupting"
                        @click="handleInterrupt"
                    >
                        <wa-icon slot="start" auto-width name="circle-stop"></wa-icon>
                        Interrupt
                    </wa-button>

                    <wa-button
                        v-if="canStopProcess"
                        :id="`session-header-${sessionId}-stop-button`"
                        variant="danger"
                        appearance="outlined"
                        size="small"
                        class="stop-button reduced-height"
                        :class="{ forcing: stoppingProcess }"
                        @click="handleStopProcess($event)"
                    >
                        <span slot="start" class="stop-icon-wrap">
                            <wa-icon
                                auto-width
                                :name="stoppingProcess ? 'skull-crossbones' : 'ban'"
                                :variant="stoppingProcess ? 'solid' : undefined"
                            ></wa-icon>
                            <wa-spinner
                                v-if="stoppingProcess"
                                class="stop-overlay-spinner"
                            ></wa-spinner>
                        </span>
                        {{ stoppingProcess ? 'Force kill' : 'Stop' }}
                    </wa-button>

                    <wa-button
                        v-if="canStopAgent"
                        :id="`session-header-${sessionId}-stop-agent-button`"
                        variant="danger"
                        appearance="outlined"
                        size="small"
                        class="stop-button reduced-height"
                        :loading="stoppingAgent"
                        :disabled="stoppingAgent"
                        @click="handleStopAgent"
                    >
                        <wa-icon slot="start" auto-width name="ban"></wa-icon>
                        Stop agent
                    </wa-button>
                </wa-button-group>
                <!-- Same v-if as the buttons: wa-tooltip resolves its anchor once, when it connects, and
                     never re-resolves. The interrupt button only appears in ASSISTANT_TURN, after this
                     block mounted, so an always-mounted tooltip would stay anchorless. Tooltips live
                     outside the button group so they do not take part in its joined layout. -->
                <AppTooltip v-if="canInterruptTurn" :for="`session-header-${sessionId}-interrupt-button`">Interrupt the current turn (keeps the session alive)</AppTooltip>
                <AppTooltip v-if="canStopProcess" :for="`session-header-${sessionId}-stop-button`">{{ stoppingProcess ? 'Force kill' : `Stop the ${providerLabel} process` }}</AppTooltip>
                <AppTooltip v-if="canStopAgent" :for="`session-header-${sessionId}-stop-agent-button`">Stop this agent</AppTooltip>
            </div>

        </div><!-- /.session-collapsible-rows -->

        <wa-divider></wa-divider>

        <!-- Compact mode toggle for non main session headers (no .session-title row to host it) -->
        <wa-button
            v-if="mode !== 'session'"
            class="compact-toggle-button compact-toggle-button--non-main-session reduced-height"
            variant="neutral"
            appearance="plain"
            size="small"
            @click="isCompactExpanded = !isCompactExpanded"
        >
            <wa-icon :name="isCompactExpanded ? 'chevron-up' : 'chevron-down'" label="Toggle details"></wa-icon>
        </wa-button>
    </header>

</template>

<style scoped>
.session-header {
    gap: var(--wa-space-xs);
    display: flex;
    flex-direction: column;
    background: var(--main-header-footer-bg-color);
    position: relative;
}

.session-title {
    display: flex;
    justify-content: start;
    align-items: center;
    gap: var(--wa-space-xs);
    min-width: 0;  /* Allow text truncation */
    padding-inline: var(--wa-space-xs);
}

/* Row 2: the project badge under the markers, left-aligned; the compact-only pieces sit on the right. */
.session-project-row {
    display: flex;
    align-items: center;
    gap: var(--wa-space-xs);
    min-width: 0;
    padding-inline: var(--wa-space-xs);
    margin-top: calc(-1 * var(--wa-space-2xs));
}

/* Action buttons: a row of their own under the identity row, in the full header and in the compact
   panel. The buttons' boxes are taller than their icon and name, so the row pulls its neighbours in: the
   visible gap above it matches the one between the project row and the identity row. */
.session-actions {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    /* Row gap a quarter of the column gap. The buttons' boxes are taller than their content, so the pull-in
       is on each button (below), not on the row: a wrapped line is then pulled in too, and not set far
       below the one above it. */
    gap: var(--wa-space-3xs) var(--wa-space-xs);
    padding-inline: var(--wa-space-xs);
    margin-block: 0 calc(0.5 * var(--wa-space-2xs));
}
.session-actions > wa-button,
.session-actions > wa-dropdown {
    margin-block: calc(-1.5 * var(--wa-space-2xs));
}
/* A step above the 3xs the buttons had in the title row (the global .reduced-height): the names and the
   icons read too small at the full-height header. The 1.3 scale of .reduced-height is replaced by a real
   1.3em on the icon and the name: a scale grows from the middle of the label and overflows its box, which
   made a long name run into the next button's icon. The button's height follows the font size set here,
   not the inner 1.3em, so the box keeps its size. */
.session-actions wa-button {
    font-size: var(--wa-font-size-2xs);
}
.session-actions wa-button::part(base) {
    padding-inline: var(--wa-space-xs);
}
.session-actions wa-button::part(label) {
    scale: 1;
}
.session-actions wa-button wa-icon,
.session-actions .action-label {
    font-size: 1.3em;
}
/* The first button's icon sits on the header's icon axis (see --header-icon-col): no padding before it and
   an icon box of the column's width. */
.session-actions > wa-button:first-child::part(base) {
    padding-inline-start: 0;
}
.session-actions > wa-button:first-child wa-icon {
    width: var(--header-icon-col);
}
/* A step above the 3xs the buttons had in the title row (the global .reduced-height): the names and the
   icons read too small at the full-height header. */
.session-actions wa-button {
    font-size: var(--wa-font-size-2xs);
}

/* The button names show only when the row fits on one line (see actionsLabels in the script). */
.action-label {
    display: none;
    /* The same between the icon and the name of every button. */
    margin-inline-start: calc(0.75 * var(--wa-space-xs));
    white-space: nowrap;
}
.session-header.actions-labels .action-label {
    display: inline;
}

.session-title h2 {
    margin: 0;
    font-size: var(--wa-font-size-l);
    font-weight: 650;
    letter-spacing: -0.015em;
    color: var(--wa-color-text-normal);
    margin-right: var(--wa-space-xs);
    flex: 1 1 0;
    min-width: 0;
    /* Truncate with ellipsis */
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
}

.session-project {
    display: inline-flex;
    align-items: center;
    gap: var(--wa-space-2xs);
    font-size: var(--wa-font-size-xs);
    color: var(--wa-color-text-quiet);
    text-decoration: none;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    min-width: 0;
}
/* The up arrow of the project link: never squeezed by a long badge. */
.session-project-up {
    flex-shrink: 0;
}
/* In a worktree badge the parent repo's name gives way first: the worktree's name is what tells two
   sessions of one repo apart. */
.session-project :deep(.project-badge-name:has(+ .project-badge-sep)) {
    flex-shrink: 5;
}
.session-project:hover {
    color: var(--wa-color-text);
}

/* Zone 1 — identity: directory (left-truncated, last folder emphasised) + branch pill. */
.session-git-info {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    column-gap: var(--wa-space-m);
    row-gap: var(--wa-space-3xs);
    padding-inline: var(--wa-space-xs);
    font-size: var(--wa-font-size-s);
    color: var(--wa-color-text-quiet);
}
/* Full height only (the compact panel keeps its spacing): the project row above it left too much white
   under the badge, so the identity row comes a little closer. */
:where(html:not(.compact-height)) .session-header[data-session-type="session"] .session-git-info {
    margin-top: calc(-1.5 * var(--wa-space-2xs));
}
/* No project row above it in a subagent header: it keeps a top space. */
.session-header[data-session-type="subagent"] .session-git-info {
    margin-top: var(--wa-space-xs);
}

.git-info-item {
    display: flex;
    align-items: center;
    gap: var(--wa-space-xs);
    min-width: 0;
    white-space: nowrap;
}

.git-directory {
    flex: 1 1 14rem;
}

/* direction: rtl moves the ellipsis to the START of the path; the inner span restores ltr so the
   slashes stay in place. */
.git-directory-text {
    direction: rtl;
    text-align: left;
    overflow: hidden;
    text-overflow: ellipsis;
    min-width: 0;
}

.git-directory-inner {
    direction: ltr;
    unicode-bidi: embed;
}

.git-directory-inner strong {
    color: var(--wa-color-text-normal);
    font-weight: 650;
}

.git-branch {
    flex: 0 1 auto;
    padding: 0.125rem var(--wa-space-s) 0.125rem var(--wa-space-xs);
    border-radius: 999px;
    background: color-mix(in oklab, var(--wa-color-brand-60) 14%, transparent);
    color: var(--wa-color-text-normal);
    font-size: var(--wa-font-size-xs);
}

.git-branch wa-icon {
    color: var(--wa-color-brand-60);
}

.git-branch-name {
    overflow: hidden;
    text-overflow: ellipsis;
}

/* Zone 2 — stats container: labelled segments, with the context ring as a closing column. */
.session-stats {
    display: grid;
    grid-template-columns: minmax(0, 1fr) auto;
    margin-inline: var(--wa-space-xs);
    border: var(--divider-size) solid var(--wa-color-surface-border);
    border-radius: var(--wa-border-radius-l);
    background: color-mix(in oklab, var(--wa-color-brand-60) 8%, transparent);
    font-size: var(--wa-font-size-s);
}

/* Cells size to their content (never truncated) and share the leftover width. */
.stats-grid {
    display: flex;
    flex-wrap: wrap;
}

.stat {
    display: flex;
    flex-direction: column;
    justify-content: center;
    gap: 2px;
    flex: 1 1 auto;
    padding: calc(var(--wa-space-xs) / 2) calc(var(--wa-space-m) / 2);
}

.stat-label {
    font-size: var(--wa-font-size-3xs);
    letter-spacing: 0.07em;
    text-transform: uppercase;
    color: var(--wa-color-text-quiet);
}

.stat-value {
    display: flex;
    align-items: center;
    gap: var(--wa-space-xs);
    font-weight: 600;
    white-space: nowrap;
}

.stat-value wa-icon {
    color: var(--wa-color-brand-60);
}

.stat-context {
    align-items: center;
    border-inline-start: var(--divider-size) solid var(--wa-color-surface-border);
}

.cost-breakdown-separator {
    color: var(--wa-color-text-quiet);
}

.session-header:not(.effective-debug) .stat-debug {
    display: none;
}

.context-usage-ring {
    --size: 2rem;
    --track-width: 3px;
    font-size: var(--wa-font-size-2xs);
}

/* The leading icon of each row, centred on the header's icon axis. The title row's is the first marker, or
   the provider icon; the project row's is the project's mark; the identity row's is the folder. */
.session-title > :is(wa-icon, .session-provider-icon):first-child {
    display: inline-flex;
    justify-content: center;
    width: var(--header-icon-col);
}
.session-project :deep(.project-badge > :first-child) {
    display: inline-flex;
    justify-content: center;
    width: var(--header-icon-col);
}
.session-git-info .git-directory > wa-icon:first-child {
    width: var(--header-icon-col);
}

/* Provider icon, between the state markers and the title. */
.session-provider-icon {
    align-self: center;
    flex-shrink: 0;
}

/* Worktree marker icon before the provider (only for worktree projects). */
.worktree-title-icon {
    align-self: center;
    flex-shrink: 0;
    color: var(--wa-color-text-quiet);
    font-size: var(--wa-font-size-s);
}

/* Tooltip body: intro line stacked above the embedded worktree badge. */
.worktree-title-tooltip {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-2xs);
}

wa-divider {
    --width: var(--divider-size);
    --spacing: 0;
}

/* Zone 3 — process: status chip on the left, control buttons pushed to the right edge. A wrapped
   button group stays right-aligned thanks to the auto start margin. */
.meta-process {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: var(--wa-space-xs) var(--wa-space-s);
    padding-inline: var(--wa-space-xs);
    font-size: var(--wa-font-size-s);
}

.process-chip {
    display: inline-flex;
    align-items: center;
    gap: var(--wa-space-m);
    padding: 0.125rem var(--wa-space-m) 0.125rem var(--wa-space-xs);
    border-radius: 999px;
    border: var(--divider-size) solid var(--wa-color-surface-border);
    background: color-mix(in oklab, var(--wa-color-brand-60) 8%, transparent);
}

.process-actions {
    margin-inline-start: auto;
}

.stop-button {
    opacity: 0.85;
    transition: opacity 0.15s;
    flex-shrink: 0;
}

.stop-button:hover {
    opacity: 1;
}

/* While a stop is in flight the button stays fully lit and clickable: clicking
   (or Shift-clicking) escalates to a force kill. */
.stop-button.forcing {
    opacity: 1;
}

/* Stop button content: the icon (skull while stopping), with a spinner overlaid
   on top to keep the "in progress" cue. The spinner is click-through so the
   button still escalates to a force kill when clicked. */
.stop-icon-wrap {
    position: relative;
    display: inline-flex;
    align-items: center;
    justify-content: center;
}

.stop-overlay-spinner {
    position: absolute;
    top: 50%;
    left: 50%;
    transform: translate(-50%, -50%);
    pointer-events: none;
    --size: 1.5em;
    --track-width: 2px;
    --indicator-color: white;
    --track-color: transparent;
}

.pin-button,
.mute-button,
.archive-button,
.rename-button,
.share-button,
.search-button,
.debug-button {
    opacity: 0.6;
    transition: opacity 0.15s;
    flex-shrink: 0;
}

.debug-button.debug-button--active {
    opacity: 1;
}

/* Unarchive button: same icon as archive, in yellow like the compact archived marker. */
.archive-button.archive-button--archived {
    opacity: 1;
    &::part(base) {
        color: var(--wa-color-yellow-80);
    }
}

/* The pin button lives inside a wa-dropdown; the dropdown itself is the flex child. */
.pin-dropdown {
    flex-shrink: 0;
}

.pin-button {
    /* The icon tilts, not the label part: the button can carry its name next to the icon. */
    & wa-icon {
        transform: rotate(30deg);
    }
    &.pin-button--active {
        opacity: 1;
        &::part(base) {
            color: var(--wa-color-yellow-80);
        }
    }
}

.pin-button:hover,
.mute-button:hover,
.archive-button:hover,
.rename-button:hover,
.share-button:hover,
.search-button:hover,
.debug-button:hover {
    opacity: 1;
}

.mute-button.mute-button--active {
    opacity: 1;

    &::part(base) {
        color: var(--wa-color-warning-60);
    }
}

/* Active share links → the button wears the brand colour (no count badge). */
.share-button--active {
    opacity: 1;
    &::part(base) {
        color: var(--wa-color-brand-60);
    }
}

.pending-request-indicator {
    color: var(--wa-color-warning-60);
    font-size: var(--wa-font-size-s);
    animation: pending-pulse 1.5s ease-in-out infinite;
    flex-shrink: 0;
    align-self: center;
}

@keyframes pending-pulse {
    0%, 100% { opacity: 1; }
    50% { opacity: 0.3; }
}

/* ═══════════════════════════════════════════════════════════════════════════
   Compact header mode — panel toggle + live group + collapsible rows
   ═══════════════════════════════════════════════════════════════════════════ */

/* Non main session toggle: hidden by default, shown only at compact height */
.compact-toggle-button {
    display: none;
    flex-shrink: 0;
    opacity: 0.6;
    transition: opacity 0.15s;
    margin-block: calc(-3 * var(--wa-space-2xs));
    position: relative;
    top: calc(-1 * var(--wa-space-2xs));
}

.compact-toggle-button:hover {
    opacity: 1;
}

/* Non main session toggle: positioned absolutely below the header */
.compact-toggle-button--non-main-session {
    position: absolute;
    bottom: calc( -1 * var(--wa-space-xs));
    right: var(--wa-space-xs);
    transform: translateX(0) translateY(100%);
    z-index: 19;
    margin: 0;
    top: auto;
}

/* Compact-only pieces of the project row: hidden by default. */
.session-header {
}

/* Compact pill geometry. The icons of the three buttons sit at the same distance from each other:
   every button is one icon wide, except the toggle, which is also wider by its chevron. Its content is
   centred, so the tools icon lands at the middle of a button-wide share, like the other two. */
.session-header {
    /* The leading icon of the title, project, identity and actions rows is centred in a box of this width,
       starting at the rows' inline padding: one vertical axis for all four. */
    --header-icon-col: 1.5rem;
    --compact-pill-button-width: 2.75rem;
    --compact-pill-chevron-width: 1rem;
}

.compact-pill,
.compact-live,
.compact-tool-button {
    display: none;
    flex-shrink: 0;
}

/* The pill: one outlined brand button cut in segments, like the back / selector / search buttons at the
   top of the sidebar: a brand border, the same raised shadow and top highlight (depth.css, themed for
   light and dark), transparent inside. It is made of buttons only: no padding, no gap, each button fills
   its share and a line in the border colour separates two of them. The overflow clip rounds the buttons'
   hover fill to the pill. Its buttons overhang the row by a negative margin, so it does not make the row
   taller than the badge. */
.compact-pill {
    align-items: stretch;
    overflow: hidden;
    margin-inline-start: auto;
    margin-block: calc(-3 * var(--wa-space-2xs));
    border-radius: var(--wa-form-control-border-radius, var(--wa-border-radius-m));
    border: 1px solid var(--wa-color-brand-border-loud);
    box-shadow: var(--depth-button), var(--depth-highlight);
}

.compact-live {
    align-items: center;
    align-items: stretch;
}

/* Icon-only interrupt / stop, plain like the other header buttons. */
.compact-live-button {
    opacity: 0.85;
    transition: opacity 0.15s;
    /* A flex host: its base stretches to the pill's height, with no percentage height (which resolved
       against a height that itself depends on the buttons). The panel toggle gets it from the compact
       rule that shows it. */
    display: inline-flex;
}
.compact-live-button,
.compact-tool-button {
    width: var(--compact-pill-button-width);
    &::part(base) {
        flex: 1;
        min-width: 0;
        padding-inline: 0;
        border-radius: 0;
    }
}
.compact-tool-button {
    width: calc(var(--compact-pill-button-width) + var(--compact-pill-chevron-width));
}
/* A line in the border colour between two buttons of the pill: on the end of each process control, so
   it needs no sibling selector (the last one separates it from the toggle, which has none of its own). */
.compact-live-button {
    border-inline-end: 1px solid var(--wa-color-brand-border-loud);
}
.compact-live-button:hover,
.compact-live-button.forcing {
    opacity: 1;
}

/* Panel toggle: tools icon + chevron, the faint treatment of the other header buttons; active while
   the panel is open, amber while a request waits for the user. */
.compact-tool-button {
    opacity: 0.6;
    transition: opacity 0.15s;
}
.compact-tool-button:hover,
.compact-tool-button.compact-tool-button--active {
    opacity: 1;
}
.compact-tool-button--pending::part(base) {
    color: var(--wa-color-warning-60);
}
.compact-tool-chevron {
    display: inline-flex;
    justify-content: center;
    width: var(--compact-pill-chevron-width);
    font-size: var(--wa-font-size-2xs);
}

/* Compact status, on the title row: hidden by default, shown in compact mode when not expanded. Its two
   cells have the width of the controls' segments below (see --compact-pill-button-width), the ring one
   that of the toggle, and each centres its content: the icons line up with the controls' icons. */
.compact-status {
    display: none;
    align-items: center;
    flex-shrink: 0;
}
.compact-status-cell {
    display: flex;
    align-items: center;
    justify-content: center;
}
.compact-status-cell--state {
    width: var(--compact-pill-button-width);
    /* The icons sit close together; the group is centred on the cell and may be wider than it. */
    gap: var(--wa-space-xs);
}
.compact-status-cell--ring {
    width: calc(var(--compact-pill-button-width) + var(--compact-pill-chevron-width));
}

/* Collapsible rows wrapper: transparent on large viewports */
.session-collapsible-rows {
    display: contents;
}

/* compact height: see utils/compactHeight.js */
/* Show the compact toggle button for non-main sessions */
:where(html.compact-height) .compact-toggle-button {
    display: inline-flex;
}

/* Show the pill and the panel toggle in it */
:where(html.compact-height) .compact-pill,
:where(html.compact-height) .compact-tool-button {
    display: inline-flex;
}


/* The row is the click target that opens the panel */
:where(html.compact-height) .session-title,
:where(html.compact-height) .session-project-row {
    cursor: pointer;
}

:where(html.compact-height) .session-header.compact-collapsed {
    border-bottom: solid var(--wa-color-surface-border) var(--divider-size);
}

/* Dont show divider when compact mode is active */
:where(html.compact-height) .session-header wa-divider {
    display: none;
}

/* The buttons of the row overhang it by their negative margin (see .compact-live): the row pays that
   back at the bottom so they do not touch the card below. */
:where(html.compact-height) .session-header .session-project-row {
    padding-bottom: calc(1.5 * var(--wa-space-2xs));
}

/* Show the status chip and the process controls when the panel is closed (the panel shows the same
   things, so they leave the rows while it is open and the title and project get the room) */
.compact-context-ring {
    /* Smaller than the stats ring, and without the % sign: it must not reach the controls below. */
    --size: 1.5rem;
    --track-width: 3px;
    /* No height of its own in the title row: it is a little taller than the title's line. */
    margin-block: calc(-0.5 * var(--size));
    /* Nudged up: centred on the row it reads a little low next to the title's text. */
    translate: 0 -2px;
}
:where(html.compact-height) .session-header.compact-collapsed .compact-live,
:where(html.compact-height) .session-header.compact-collapsed .compact-status {
    display: inline-flex;
}

/* Collapsible rows become a glass panel hanging under the title row (the look of the popovers; the
   glass-surface class is set only at compact height, see the template). It reveals like the other
   overlays: --twicc-reveal fades the glass layer, its content and its shadow (an opacity on the panel
   would stop the blur), and the panel itself only moves. */
:where(html.compact-height) .session-collapsible-rows {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-xs);
    position: absolute;
    top: 100%;
    left: var(--wa-space-xs);
    right: var(--wa-space-xs);
    z-index: 20;
    padding: var(--wa-space-xs) 0;
    border-radius: var(--wa-border-radius-l);

    /* Hidden by default */
    visibility: hidden;
    pointer-events: none;
    --twicc-reveal: 0;
    --twicc-reveal-filter: opacity(var(--twicc-reveal));
    translate: 0 calc(-0.5rem * var(--motion-amount));
    transition:
        --twicc-reveal var(--motion-dur-2) ease-in-out,
        translate var(--motion-dur-2) var(--motion-ease-out),
        visibility var(--motion-dur-2);
}
:where(html.compact-height) .session-header:not([data-session-type="session"]) .session-collapsible-rows {
    z-index: 19;
}

/* When expanded: reveal the panel */
:where(html.compact-height) .session-header.compact-expanded .session-collapsible-rows {
    visibility: visible;
    pointer-events: auto;
    --twicc-reveal: 1;
    translate: 0 0;
}

:where(html.compact-height) .session-header.compact-expanded .compact-toggle-button--non-main-session {
    bottom: -100%;
}
</style>
