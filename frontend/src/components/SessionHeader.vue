<script setup>
import { ref, computed, watch, inject } from 'vue'
import { useDataStore } from '../stores/data'
import { useSettingsStore } from '../stores/settings'
import { formatDate, formatDuration } from '../utils/date'
import { MAX_CONTEXT_TOKENS, PROCESS_STATE, PROCESS_STATE_COLORS, PROCESS_STATE_NAMES } from '../constants'
import { killProcess } from '../composables/useWebSocket'
import ProjectBadge from './ProjectBadge.vue'
import ProcessIndicator from './ProcessIndicator.vue'

const props = defineProps({
    sessionId: {
        type: String,
        required: true
    },
    mode: {
        type: String,
        default: 'session',
        validator: (value) => ['session', 'subagent'].includes(value)
    },
    /**
     * Label of the currently active tab (e.g., "Chat", "Files").
     * Shown in the compact header when collapsed, replacing the action buttons.
     */
    activeTabLabel: {
        type: String,
        default: null
    }
})

const store = useDataStore()
const settingsStore = useSettingsStore()

// Tooltips setting
const tooltipsEnabled = computed(() => settingsStore.areTooltipsEnabled)
// Costs setting
const showCosts = computed(() => settingsStore.areCostsShown)

// Session data from store
const session = computed(() => store.getSession(props.sessionId))

// Get display name for header
// - Session mode: title if available, "New session" for drafts without title, otherwise session ID
// - Subagent mode: "Agent {agent_id}"
const displayName = computed(() => {
    if (props.mode === 'subagent') {
        return `Agent ${props.sessionId}`
    }
    // For draft sessions without a title, show "New session"
    if (session.value?.draft && !session.value?.title) {
        return 'New session'
    }
    return session.value?.title || props.sessionId
})

// Format cost as USD string (e.g., "$0.42")
function formatCost(cost) {
    if (cost == null) return null
    return `${cost.toFixed(2)}`
}

// Format cost display for header
const formattedTotalCost = computed(() => {
    const sess = session.value
    if (!sess || sess.total_cost == null) return null
    return formatCost(sess.total_cost)
})

// Cost breakdown (self + subagents) - only shown if subagents have cost
const formattedCostBreakdown = computed(() => {
    const sess = session.value
    if (!sess) return null

    const subagentsCost = sess.subagents_cost
    if (subagentsCost == null || subagentsCost <= 0) return null

    const self = formatCost(sess.self_cost)
    const subagents = formatCost(subagentsCost)
    return `${self} + ${subagents}`
})

// Calculate context usage percentage
const contextUsagePercentage = computed(() => {
    const usage = session.value?.context_usage
    if (usage == null) return null
    return Math.round((usage / MAX_CONTEXT_TOKENS) * 100)
})

// Get indicator color for context usage based on thresholds
const contextUsageColor = computed(() => {
    const pct = contextUsagePercentage.value
    if (pct == null) return null
    if (pct > 70) return 'var(--wa-color-danger)'
    if (pct > 50) return 'var(--wa-color-warning)'
    return 'var(--wa-color-primary)'
})

// Calculate indicator width multiplier (1x at 0%, 2x at 80%+)
const contextUsageIndicatorWidth = computed(() => {
    const pct = contextUsagePercentage.value
    if (pct == null) return null
    // Linear interpolation from 1x (at 0%) to 1.5x (at 80%), capped at 1.5x
    const multiplier = Math.min(1 + (pct / 80), 1.5)
    return `calc(var(--track-width) * ${multiplier.toFixed(2)})`
})

// Display directory: git_directory if available, otherwise cwd
const displayDirectory = computed(() => {
    return session.value?.git_directory || session.value?.cwd || null
})

// Tooltip for directory: indicate whether it's the resolved git directory or cwd fallback
const displayDirectoryTooltip = computed(() => {
    if (session.value?.git_directory) return 'Git working directory'
    if (session.value?.cwd) return 'Working directory (cwd)'
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

// Check if process can be stopped (any state except dead)
const canStopProcess = computed(() => {
    const state = processState.value?.state
    return state && state !== PROCESS_STATE.DEAD
})

/**
 * Stop the current process.
 */
function handleStopProcess() {
    if (canStopProcess.value) {
        killProcess(props.sessionId)
    }
}

// KeepAlive active state (provided by SessionView)
const sessionActive = inject('sessionActive', ref(true))

// Timer for updating state durations — paused when the session is inactive
const now = ref(Date.now() / 1000)
let durationTimer = null

watch(sessionActive, (active) => {
    if (active) {
        now.value = Date.now() / 1000
        durationTimer = setInterval(() => {
            now.value = Date.now() / 1000
        }, 1000)
    } else if (durationTimer) {
        clearInterval(durationTimer)
        durationTimer = null
    }
}, { immediate: true })

/**
 * Calculate state duration for a process.
 * @param {object} procState
 * @returns {number} Duration in seconds
 */
function getStateDuration(procState) {
    if (!procState?.state_changed_at) return 0
    return Math.max(0, Math.floor(now.value - procState.state_changed_at))
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
 */
function handleArchive() {
    if (session.value && !session.value.archived && !session.value.draft) {
        if (canStopProcess.value) {
            killProcess(props.sessionId)
        }
        store.setSessionArchived(session.value.project_id, props.sessionId, true)
    }
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
 * Toggle the pinned state of the current session.
 */
function handleTogglePin() {
    if (session.value && !session.value.draft) {
        store.setSessionPinned(session.value.project_id, props.sessionId, !session.value.pinned)
    }
}

// Expose methods and refs for parent components
defineExpose({
    openRenameDialog,
    headerRef,
    isCompactExpanded,
})
</script>

<template>
    <header ref="headerRef" class="session-header" :class="{ 'compact-expanded': isCompactExpanded, 'compact-collapsed': !isCompactExpanded }" :data-session-type="mode" v-if="session">
        <div v-if="mode === 'session'" class="session-title">
            <!-- Action buttons group: hidden in compact collapsed mode, replaced by active tab label -->
            <div class="session-title-actions">
                <wa-tag v-if="session.archived" :id="`session-header-${sessionId}-archived-tag`" size="small" variant="neutral" class="archived-tag" @click="handleUnarchive">Archived</wa-tag>
                <wa-tooltip v-if="tooltipsEnabled && session.archived" :for="`session-header-${sessionId}-archived-tag`">Click to unarchive</wa-tooltip>
                <wa-tag v-else-if="session.draft" size="small" variant="warning" class="draft-tag">Draft</wa-tag>

                <!-- Pin/Unpin button (not for drafts) -->
                <wa-button
                    v-if="!session.draft"
                    :id="`session-header-${sessionId}-pin-button`"
                    :variant="session.pinned ? 'brand' : 'neutral'"
                    appearance="plain"
                    size="small"
                    :class="['pin-button', { 'pin-button--active': session.pinned }]"
                    @click="handleTogglePin"
                >
                    <wa-icon name="thumbtack" :label="session.pinned ? 'Unpin' : 'Pin'"></wa-icon>
                </wa-button>
                <wa-tooltip v-if="tooltipsEnabled && !session.draft" :for="`session-header-${sessionId}-pin-button`">{{ session.pinned ? 'Unpin session' : 'Pin session' }}</wa-tooltip>

                <!-- Archive button (not for drafts or already archived) -->
                <wa-button
                    v-if="!session.archived && !session.draft"
                    :id="`session-header-${sessionId}-archive-button`"
                    variant="neutral"
                    appearance="plain"
                    size="small"
                    class="archive-button"
                    @click="handleArchive"
                >
                    <wa-icon name="box-archive" label="Archive"></wa-icon>
                </wa-button>
                <wa-tooltip v-if="tooltipsEnabled && !session.archived && !session.draft" :for="`session-header-${sessionId}-archive-button`">{{ canStopProcess ? 'Archive session (it will stop the Claude Code process)' : 'Archive session' }}</wa-tooltip>

                <!-- Rename button (only for main session) -->
                <wa-button
                    v-if="mode === 'session'"
                    :id="`session-header-${sessionId}-rename-button`"
                    variant="neutral"
                    appearance="plain"
                    size="small"
                    class="rename-button"
                    @click="openRenameDialog"
                >
                    <wa-icon name="pencil" label="Rename"></wa-icon>
                </wa-button>
                <wa-tooltip v-if="tooltipsEnabled" :for="`session-header-${sessionId}-rename-button`">Rename session</wa-tooltip>

                <!-- Pending request indicator (shown when waiting for user response) -->
                <wa-icon
                    v-if="store.getPendingRequest(sessionId)"
                    :id="`session-header-${sessionId}-pending-request`"
                    name="hand"
                    class="pending-request-indicator"
                ></wa-icon>
                <wa-tooltip v-if="tooltipsEnabled && store.getPendingRequest(sessionId)" :for="`session-header-${sessionId}-pending-request`">Waiting for your response</wa-tooltip>
            </div>

            <!-- Clickable zone: title + project + context ring + chevron toggle compact mode -->
            <div class="compact-toggle-zone" @click="isCompactExpanded = !isCompactExpanded">
                <!-- Active tab label: shown only in compact collapsed mode, replacing action buttons -->
                <span v-if="activeTabLabel" class="compact-active-tab-label">{{ activeTabLabel }}</span>

                <h2 :id="`session-header-${sessionId}-title`">{{ displayName }}</h2>
                <wa-tooltip v-if="tooltipsEnabled" :for="`session-header-${sessionId}-title`">{{ displayName }}</wa-tooltip>

                <ProjectBadge v-if="session.project_id" :project-id="session.project_id" class="session-project" />

                <!-- Context usage ring duplicate for compact mode (visible only on small viewports when not expanded) -->
                <wa-progress-ring
                    v-if="contextUsagePercentage != null"
                    class="context-usage-ring compact-context-ring"
                    :value="Math.min(contextUsagePercentage, 100)"
                    :style="{
                        '--indicator-color': contextUsageColor,
                        '--indicator-width': contextUsageIndicatorWidth
                    }"
                ><span class="wa-font-weight-bold">{{ contextUsagePercentage }}%</span></wa-progress-ring>

                <!-- Compact mode: expand/collapse chevron (only visible on small viewports via CSS) -->
                <wa-icon
                    v-if="!session.draft"
                    class="compact-toggle-chevron"
                    :name="isCompactExpanded ? 'chevron-up' : 'chevron-down'"
                    label="Toggle details"
                ></wa-icon>
            </div>
        </div>

        <!-- Collapsible rows: git info + meta (overlay on small viewports) -->
        <div class="session-collapsible-rows">

            <!-- Git info row: directory @ branch (not shown for draft sessions) -->
            <div v-if="!session.draft && (displayDirectory || session.git_branch)" class="session-git-info">
                <span v-if="displayDirectory" :id="`session-header-${sessionId}-git-directory`" class="git-info-item">
                    <wa-icon auto-width name="folder-open" variant="regular"></wa-icon>
                    <span>{{ displayDirectory }}</span>
                </span>
                <wa-tooltip v-if="tooltipsEnabled && displayDirectory" :for="`session-header-${sessionId}-git-directory`">{{ displayDirectoryTooltip }}</wa-tooltip>

                <span v-if="session.git_branch" :id="`session-header-${sessionId}-git-branch`" class="git-info-item">
                    <wa-icon auto-width name="code-branch"></wa-icon>
                    <span>{{ session.git_branch }}</span>
                </span>
                <wa-tooltip v-if="tooltipsEnabled && session.git_branch" :for="`session-header-${sessionId}-git-branch`">Git branch</wa-tooltip>
            </div>

            <!-- Meta row (not shown for draft sessions) -->
            <div v-if="!session.draft" class="session-meta">

                <span :id="`session-header-${sessionId}-messages`" class="meta-item">
                    <wa-icon auto-width name="comment" variant="regular"></wa-icon>
                    <span>{{ session.message_count ?? '??' }}</span>
                </span>
                <wa-tooltip v-if="tooltipsEnabled" :for="`session-header-${sessionId}-messages`">Number of user and assistant messages</wa-tooltip>

                <span :id="`session-header-${sessionId}-lines`" class="meta-item nb_lines">
                    <wa-icon auto-width name="bars"></wa-icon>
                    <span>{{ session.last_line }}</span>
                </span>
                <wa-tooltip v-if="tooltipsEnabled" :for="`session-header-${sessionId}-lines`">Lines in the JSONL file</wa-tooltip>

                <span :id="`session-header-${sessionId}-mtime`" class="meta-item">
                    <wa-icon auto-width name="clock" variant="regular"></wa-icon>
                    <span>{{ formatDate(session.mtime, { smart: true }) }}</span>
                </span>
                <wa-tooltip v-if="tooltipsEnabled" :for="`session-header-${sessionId}-mtime`">Last activity</wa-tooltip>

                <template v-if="showCosts && formattedTotalCost">
                    <span :id="`session-header-${sessionId}-cost`" class="meta-item">
                        <wa-icon auto-width name="dollar-sign" variant="solid"></wa-icon>
                        {{ formattedTotalCost }}
                    </span>
                    <wa-tooltip v-if="tooltipsEnabled" :for="`session-header-${sessionId}-cost`">Total session cost</wa-tooltip>
                </template>

                <template v-if="showCosts && formattedCostBreakdown">
                    <span :id="`session-header-${sessionId}-cost-breakdown`" class="meta-item cost-breakdown-item">
                        <span>(
                        <span>
                            <wa-icon auto-width name="dollar-sign" variant="solid"></wa-icon>
                            <span class="cost-breakdown">{{ formattedCostBreakdown }}</span>
                        </span>
                        )</span>
                    </span>
                    <wa-tooltip v-if="tooltipsEnabled" :for="`session-header-${sessionId}-cost-breakdown`">Main agent cost + sub-agents cost</wa-tooltip>
                </template>

                <template v-if="formattedModel">
                    <span :id="`session-header-${sessionId}-model`" class="meta-item">
                        <wa-icon auto-width name="robot" variant="classic"></wa-icon>
                        <span>{{ formattedModel }}</span>
                    </span>
                    <wa-tooltip v-if="tooltipsEnabled" :for="`session-header-${sessionId}-model`">Last used model</wa-tooltip>
                </template>

                <template v-if="contextUsagePercentage != null">
                    <wa-progress-ring
                        :id="`session-header-${sessionId}-context`"
                        class="context-usage-ring"
                        :value="Math.min(contextUsagePercentage, 100)"
                        :style="{
                            '--indicator-color': contextUsageColor,
                            '--indicator-width': contextUsageIndicatorWidth
                        }"
                    ><span class="wa-font-weight-bold">{{ contextUsagePercentage }}%</span></wa-progress-ring>
                    <wa-tooltip v-if="tooltipsEnabled" :for="`session-header-${sessionId}-context`">Context window usage</wa-tooltip>
                </template>

                <template
                    v-if="processState"
                >
                    <div style="flex-grow: 1"></div>
                    <span
                        v-if="processState.state === PROCESS_STATE.ASSISTANT_TURN && processState.state_changed_at"
                        :id="`session-header-${sessionId}-process-duration`"
                        class="process-duration"
                        :style="{ color: getProcessColor(processState.state) }"
                    >
                        {{ formatDuration(getStateDuration(processState)) }}
                    </span>
                    <wa-tooltip v-if="tooltipsEnabled && processState.state === PROCESS_STATE.ASSISTANT_TURN && processState.state_changed_at" :for="`session-header-${sessionId}-process-duration`">Assistant turn duration</wa-tooltip>

                    <span
                        v-if="processState.memory"
                        :id="`session-header-${sessionId}-process-memory`"
                        class="process-memory"
                        :style="{ color: getProcessColor(processState.state) }"
                    >
                        {{ formatMemory(processState.memory) }}
                    </span>
                    <wa-tooltip v-if="tooltipsEnabled && processState.memory" :for="`session-header-${sessionId}-process-memory`">Claude Code memory usage</wa-tooltip>

                    <ProcessIndicator
                        :id="`session-header-${sessionId}-process-indicator`"
                        :state="processState.state"
                        size="small"
                        :animate-states="animateStates"
                    />
                    <wa-tooltip v-if="tooltipsEnabled" :for="`session-header-${sessionId}-process-indicator`">Claude Code state: {{ PROCESS_STATE_NAMES[processState.state] }}</wa-tooltip>

                    <wa-button
                        v-if="canStopProcess"
                        :id="`session-header-${sessionId}-stop-button`"
                        variant="danger"
                        appearance="filled"
                        size="small"
                        class="stop-button"
                        @click="handleStopProcess"
                    >
                        <wa-icon name="ban" label="Stop"></wa-icon>
                    </wa-button>
                    <wa-tooltip v-if="tooltipsEnabled" :for="`session-header-${sessionId}-stop-button`">Stop the Claude Code process</wa-tooltip>
                </template>
            </div>

            <!-- Slot for extra compact-mode content (e.g. tab nav from SessionView) -->
            <slot name="compact-extra"></slot>

        </div><!-- /.session-collapsible-rows -->

        <wa-divider></wa-divider>

        <!-- Compact mode toggle for non main session headers (no .session-title row to host it) -->
        <wa-button
            v-if="mode !== 'session'"
            class="compact-toggle-button compact-toggle-button--non-main-session"
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
    flex: 1;
    display: flex;
    justify-content: start;
    align-items: baseline;
    gap: var(--wa-space-xs);
    min-width: 0;  /* Allow text truncation */
    padding-inline: var(--wa-space-xs);
    padding-top: var(--wa-space-xs);
}

/* Action buttons wrapper: transparent by default, hidden in compact collapsed mode */
.session-title-actions {
    display: contents;
}

/* Active tab label: hidden by default, shown in compact collapsed mode */
.compact-active-tab-label {
    display: none;
    font-size: var(--wa-font-size-s);
    font-weight: 700;
    color: var(--wa-color-brand-on-quiet);
    flex-shrink: 0;
    border-color: var(--wa-color-brand-border-loud);
    border-radius: var(--wa-form-control-border-radius);
    border-style: var(--wa-border-style);
    border-width: var(--wa-border-width-s);
    padding: var(--wa-space-2xs) var(--wa-space-xs);
    box-shadow: var(--wa-shadow-offset-x-s) var(--wa-shadow-offset-y-s) 0 0 var(--wa-color-brand-border-loud);
}

.draft-tag, .archived-tag {
    flex-shrink: 0;
    line-height: unset;
    height: unset;
    align-self: stretch;
}

.archived-tag {
    cursor: pointer;
}

.session-title h2 {
    margin: 0;
    font-size: var(--wa-font-size-l);
    font-weight: 600;
    color: var(--wa-color-text-normal);
    margin-right: var(--wa-space-xs);
    /* Truncate with ellipsis */
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
}

.session-project {
    margin-left: auto;
    font-size: var(--wa-font-size-xs);
    color: var(--wa-color-text-quiet);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    width: 25%;
    min-width: 3rem;
    max-width: max-content;
}

/* Clickable zone for compact toggle: wraps title, project badge, context ring, and chevron */
.compact-toggle-zone {
    display: contents;
}

/* Compact chevron icon: hidden by default, shown only on small viewports */
.compact-toggle-chevron {
    display: none;
    flex-shrink: 0;
    opacity: 0.6;
    transition: opacity 0.15s;
    font-size: var(--wa-font-size-xs);
    align-self: center;
}

.session-git-info {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    column-gap: var(--wa-space-l);
    row-gap: var(--wa-space-3xs);
    padding-inline: var(--wa-space-m);
    font-size: var(--wa-font-size-s);
    color: var(--wa-color-text-quiet);
    overflow: hidden;
    margin-top: var(--wa-space-xs);
}

.git-info-item {
    display: flex;
    align-items: center;
    gap: var(--wa-space-xs);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
}

.session-meta {
    display: flex;
    justify-content: start;
    align-items: center;
    gap: var(--wa-space-l);
    padding-inline: var(--wa-space-m);
}

.session-meta {
    display: flex;
    flex-wrap: wrap;
    column-gap: var(--wa-space-l);
    row-gap: var(--wa-space-xs);
    font-size: var(--wa-font-size-s);
}

.meta-item {
    display: flex;
    align-items: center;
    gap: var(--wa-space-xs);
}

.cost-breakdown-item {
    gap: 0;
    > span {
        --parentheses-offset: 1.5px;
        position: relative;
        top: calc(-1 * var(--parentheses-offset));
        gap: 0.2em;
        > span {
            position: relative;
            top: var(--parentheses-offset);
        }
    }
}

.nb_lines, .cost-breakdown-item {
    font-size: var(--wa-font-size-xs);
    color: var(--wa-color-text-quiet);
}

body:not([data-display-mode="debug"]) .nb_lines,
body:not([data-display-mode="debug"]) .cost-breakdown-item {
    display: none;
}

.context-usage-ring {
    --size: 2rem;
    --track-width: 3px;
    font-size: var(--wa-font-size-2xs);
}

/* Compact context ring: hidden by default, shown in compact mode when not expanded */
.compact-context-ring {
    display: none;
    align-self: center;
}

wa-divider {
    --width: 4px;
    --spacing: 0;
}

.stop-button {
    opacity: 0.6;
    transition: opacity 0.15s;
    flex-shrink: 0;
    font-size: var(--wa-font-size-3xs);
    &::part(label) {
        scale: 1.5;
    }
}

.stop-button:hover {
    opacity: 1;
}

.pin-button,
.archive-button,
.rename-button {
    opacity: 0.6;
    transition: opacity 0.15s;
    flex-shrink: 0;
    font-size: var(--wa-font-size-3xs);
    &::part(label) {
        scale: 1.5;
    }
    margin-block: calc(-3 * var(--wa-space-2xs));
    position: relative;
    top: calc(-1 * var(--wa-space-2xs));
}

.pin-button {
    &::part(label) {
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
.archive-button:hover,
.rename-button:hover {
    opacity: 1;
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
   Compact header mode — toggle button + collapsible rows
   ═══════════════════════════════════════════════════════════════════════════ */

/* Toggle button: hidden by default, shown only on small viewports */
.compact-toggle-button {
    display: none;
    flex-shrink: 0;
    opacity: 0.6;
    transition: opacity 0.15s;
    font-size: var(--wa-font-size-3xs);
    &::part(label) {
        scale: 1.5;
    }
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

/* Collapsible rows wrapper: transparent on large viewports */
.session-collapsible-rows {
    display: contents;
}

@media (max-height: 900px) {
    /* Show the compact toggle chevron */
    .compact-toggle-chevron {
        display: inline-flex;
    }

    /* Show the compact toggle button for non-main sessions */
    .compact-toggle-button {
        display: inline-flex;
    }

    /* Make the toggle zone a clickable flex row */
    .compact-toggle-zone {
        display: flex;
        align-items: center;
        gap: var(--wa-space-s);
        min-width: 0;
        cursor: pointer;
        flex: 1;
    }

    .compact-toggle-zone:hover .compact-toggle-chevron {
        opacity: 1;
    }

    .draft-tag {
        margin-bottom: var(--wa-space-xs);
    }

    .session-header.compact-collapsed {
        border-bottom: solid var(--wa-color-surface-border) 4px;
    }

    /* In compact collapsed mode: hide action buttons, show active tab label */
    .session-header.compact-collapsed .session-title-actions {
        display: none;
    }
    .session-header.compact-collapsed .compact-active-tab-label {
        display: inline;
    }

    /* In compact expanded mode: show action buttons, hide active tab label */
    .session-header.compact-expanded .compact-active-tab-label {
        display: none;
    }

    /* Dont show divider when compact mode is active */
    .session-header wa-divider {
        display: none;
    }

    /* Add some padding on the bottom of the first line */
    .session-header.compact-collapsed .session-title {
        padding-bottom: var(--wa-space-xs);
        align-items: center;
    }

    /* Show the compact context ring when not expanded */
    .session-header.compact-collapsed .compact-context-ring {
        display: inline-flex;
    }

    /* Collapsible rows become an overlay panel */
    .session-collapsible-rows {
        display: flex;
        flex-direction: column;
        gap: var(--wa-space-xs);
        position: absolute;
        top: 100%;
        left: 0;
        right: 0;
        z-index: 20;
        background: var(--wa-color-surface-default);
        box-shadow: var(--wa-shadow-s);
        border-bottom: solid var(--wa-color-surface-border) 4px;

        /* Hidden by default */
        opacity: 0;
        visibility: hidden;
        transform: translateY(-8px);
        transition: opacity 0.2s ease, transform 0.2s ease, visibility 0.2s;
    }
    .session-header:not([data-session-type="session"]) .session-collapsible-rows {
        z-index: 19;
    }


    /* When expanded: reveal the overlay */
    .session-header.compact-expanded .session-collapsible-rows {
        opacity: 1;
        visibility: visible;
        transform: translateY(0);
    }

    .session-header.compact-expanded .compact-toggle-button--non-main-session {
        bottom: -100%;
    }

}

</style>
