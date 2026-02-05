<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { useDataStore } from '../stores/data'
import { useSettingsStore } from '../stores/settings'
import { formatDate, formatDuration } from '../utils/date'
import { MAX_CONTEXT_TOKENS, PROCESS_STATE, PROCESS_STATE_COLORS, PROCESS_STATE_NAMES } from '../constants'
import { killProcess } from '../composables/useWebSocket'
import ProjectBadge from './ProjectBadge.vue'
import ProcessIndicator from './ProcessIndicator.vue'
import SessionRenameDialog from './SessionRenameDialog.vue'

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
     * Whether the header is hidden (for auto-hide on small viewports).
     * When true, the header slides up and out of view.
     */
    hidden: {
        type: Boolean,
        default: false
    }
})

const store = useDataStore()
const settingsStore = useSettingsStore()

// Tooltips setting
const tooltipsEnabled = computed(() => settingsStore.areTooltipsEnabled)

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

// Timer for updating state durations
const now = ref(Date.now() / 1000)
let durationTimer = null

onMounted(() => {
    durationTimer = setInterval(() => {
        now.value = Date.now() / 1000
    }, 1000)
})

onUnmounted(() => {
    if (durationTimer) {
        clearInterval(durationTimer)
    }
})

/**
 * Calculate state duration for a process.
 * @param {object} procState
 * @returns {number} Duration in seconds
 */
function getStateDuration(procState) {
    if (!procState?.state_changed_at) return 0
    return Math.max(0, Math.floor(now.value - procState.state_changed_at))
}

// Rename dialog
const renameDialogRef = ref(null)

// Reference to the header element (for auto-hide height calculation)
const headerRef = ref(null)

/**
 * Open the rename dialog.
 * @param {Object} options
 * @param {boolean} options.showHint - Show contextual hint (when opened during message send)
 */
function openRenameDialog({ showHint = false } = {}) {
    renameDialogRef.value?.open({ showHint })
}

/**
 * Archive the current session.
 */
function handleArchive() {
    if (session.value && !session.value.archived && !session.value.draft) {
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

// Expose methods and refs for parent components
defineExpose({
    openRenameDialog,
    headerRef,
})
</script>

<template>
    <header ref="headerRef" class="session-header" :class="{ 'auto-hide-hidden': hidden }" v-if="session">
        <div v-if="mode === 'session'" class="session-title">
            <wa-tag v-if="session.archived" id="session-header-archived-tag" size="small" variant="neutral" class="archived-tag" @click="handleUnarchive">Archived</wa-tag>
            <wa-tooltip v-if="tooltipsEnabled && session.archived" for="session-header-archived-tag">Click to unarchive</wa-tooltip>
            <wa-tag v-else-if="session.draft" size="small" variant="warning" class="draft-tag">Draft</wa-tag>

            <!-- Archive button (not for drafts or already archived) -->
            <wa-button
                v-if="!session.archived && !session.draft"
                id="session-header-archive-button"
                variant="neutral"
                appearance="plain"
                size="small"
                class="archive-button"
                @click="handleArchive"
            >
                <wa-icon name="box-archive" label="Archive"></wa-icon>
            </wa-button>
            <wa-tooltip v-if="tooltipsEnabled && !session.archived && !session.draft" for="session-header-archive-button">Archive session</wa-tooltip>

            <!-- Rename button (not for subagents) -->
            <wa-button
                v-if="mode === 'session'"
                id="session-header-rename-button"
                variant="neutral"
                appearance="plain"
                size="small"
                class="rename-button"
                @click="openRenameDialog"
            >
                <wa-icon name="pencil" label="Rename"></wa-icon>
            </wa-button>
            <wa-tooltip v-if="tooltipsEnabled" for="session-header-rename-button">Rename session</wa-tooltip>

            <h2 id="session-header-title">{{ displayName }}</h2>
            <wa-tooltip v-if="tooltipsEnabled" for="session-header-title">{{ displayName }}</wa-tooltip>

            <ProjectBadge v-if="session.project_id" :project-id="session.project_id" class="session-project" />
        </div>


        <!-- Meta row (not shown for draft sessions) -->
        <div v-if="!session.draft" class="session-meta">

            <span id="session-header-messages" class="meta-item">
                <wa-icon auto-width name="comment" variant="regular"></wa-icon>
                <span>{{ session.message_count ?? '??' }}</span>
            </span>
            <wa-tooltip v-if="tooltipsEnabled" for="session-header-messages">Number of user and assistant messages</wa-tooltip>

            <span id="session-header-lines" class="meta-item nb_lines">
                <wa-icon auto-width name="bars"></wa-icon>
                <span>{{ session.last_line }}</span>
            </span>
            <wa-tooltip v-if="tooltipsEnabled" for="session-header-lines">Lines in the JSONL file</wa-tooltip>

            <span id="session-header-mtime" class="meta-item">
                <wa-icon auto-width name="clock" variant="regular"></wa-icon>
                <span>{{ formatDate(session.mtime, { smart: true }) }}</span>
            </span>
            <wa-tooltip v-if="tooltipsEnabled" for="session-header-mtime">Last activity</wa-tooltip>

            <template v-if="formattedTotalCost">
                <span id="session-header-cost" class="meta-item">
                    <wa-icon auto-width name="dollar-sign" variant="solid"></wa-icon>
                    {{ formattedTotalCost }}
                </span>
                <wa-tooltip v-if="tooltipsEnabled" for="session-header-cost">Total session cost</wa-tooltip>
            </template>

            <template v-if="formattedCostBreakdown">
                <span id="session-header-cost-breakdown" class="meta-item">
                    <span>(
                    <span>
                        <wa-icon auto-width name="dollar-sign" variant="solid"></wa-icon>
                        <span class="cost-breakdown">{{ formattedCostBreakdown }}</span>
                    </span>
                    )</span>
                </span>
                <wa-tooltip v-if="tooltipsEnabled" for="session-header-cost-breakdown">Main agent cost + sub-agents cost</wa-tooltip>
            </template>

            <template v-if="formattedModel">
                <span id="session-header-model" class="meta-item">
                    <wa-icon auto-width name="robot" variant="classic"></wa-icon>
                    <span>{{ formattedModel }}</span>
                </span>
                <wa-tooltip v-if="tooltipsEnabled" for="session-header-model">Last used model</wa-tooltip>
            </template>

            <template v-if="contextUsagePercentage != null">
                <wa-progress-ring
                    id="session-header-context"
                    class="context-usage-ring"
                    :value="Math.min(contextUsagePercentage, 100)"
                    :style="{
                        '--indicator-color': contextUsageColor,
                        '--indicator-width': contextUsageIndicatorWidth
                    }"
                ><span class="wa-font-weight-bold">{{ contextUsagePercentage }}%</span></wa-progress-ring>
                <wa-tooltip v-if="tooltipsEnabled" for="session-header-context">Context window usage</wa-tooltip>
            </template>

            <template
                v-if="processState"
            >
                <div style="flex-grow: 1"></div>
                <span
                    v-if="processState.state === PROCESS_STATE.ASSISTANT_TURN && processState.state_changed_at"
                    id="session-header-process-duration"
                    class="process-duration"
                    :style="{ color: getProcessColor(processState.state) }"
                >
                    {{ formatDuration(getStateDuration(processState)) }}
                </span>
                <wa-tooltip v-if="tooltipsEnabled && processState.state === PROCESS_STATE.ASSISTANT_TURN && processState.state_changed_at" for="session-header-process-duration">Assistant turn duration</wa-tooltip>

                <span
                    v-if="processState.memory"
                    id="session-header-process-memory"
                    class="process-memory"
                    :style="{ color: getProcessColor(processState.state) }"
                >
                    {{ formatMemory(processState.memory) }}
                </span>
                <wa-tooltip v-if="tooltipsEnabled && processState.memory" for="session-header-process-memory">Claude Code memory usage</wa-tooltip>

                <ProcessIndicator
                    id="session-header-process-indicator"
                    :state="processState.state"
                    size="small"
                    :animate-states="animateStates"
                />
                <wa-tooltip v-if="tooltipsEnabled" for="session-header-process-indicator">Claude Code state: {{ PROCESS_STATE_NAMES[processState.state] }}</wa-tooltip>

                <wa-button
                    v-if="canStopProcess"
                    id="session-header-stop-button"
                    variant="danger"
                    appearance="filled"
                    size="small"
                    class="stop-button"
                    @click="handleStopProcess"
                >
                    <wa-icon name="ban" label="Stop"></wa-icon>
                </wa-button>
                <wa-tooltip v-if="tooltipsEnabled" for="session-header-stop-button">Stop the Claude Code process</wa-tooltip>
            </template>
        </div>

        <wa-divider></wa-divider>
    </header>

    <!-- Rename dialog -->
    <SessionRenameDialog
        ref="renameDialogRef"
        :session="session"
    />
</template>

<style scoped>
.session-header {
    gap: var(--wa-space-xs);
    display: flex;
    flex-direction: column;
    background: var(--main-header-footer-bg-color);
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

.draft-tag, .archived-tag {
    flex-shrink: 0;
    line-height: unset;
    height: unset;
    align-self: stretch;
    margin-bottom: -2px;
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
    max-width: max-content;
}

.session-meta {
    display: flex;
    justify-content: start;
    align-items: center;
    gap: var(--wa-space-l);
    padding-inline: var(--wa-space-xs);
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

#session-header-cost-breakdown {
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

#session-header-lines, #session-header-cost-breakdown {
    font-size: var(--wa-font-size-xs);
    color: var(--wa-color-text-quiet);
}

body:not([data-display-mode="debug"]) .nb_lines {
    display: none;
}

.context-usage-ring {
    --size: 2rem;
    --track-width: 3px;
    font-size: var(--wa-font-size-2xs);
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

.archive-button:hover,
.rename-button:hover {
    opacity: 1;
}

/* Auto-hide header on small viewport heights */
@media (max-height: 800px) {
    .session-header {
        transition: transform 0.3s ease, opacity 0.3s ease;
    }

    .session-header.auto-hide-hidden {
        position: absolute;
        top: 0;
        left: 0;
        right: 0;
        z-index: 10;
        transform: translateY(-100%);
        opacity: 0;
        pointer-events: none;
    }
}

</style>
