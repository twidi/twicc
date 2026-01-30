<script setup>
import { computed } from 'vue'
import { useDataStore } from '../stores/data'
import { formatDate } from '../utils/date'
import { MAX_CONTEXT_TOKENS, DISPLAY_MODE } from '../constants'

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

const emit = defineEmits(['modeChange'])

const store = useDataStore()

// Session data from store
const session = computed(() => store.getSession(props.sessionId))

// Display mode (global, from store) - only used in session mode
const displayMode = computed(() => store.getDisplayMode)

// Get display name for header
// - Session mode: title if available, otherwise session ID
// - Subagent mode: "Agent {agent_id}"
const displayName = computed(() => {
    if (props.mode === 'subagent') {
        return `Agent ${props.sessionId}`
    }
    return session.value?.title || props.sessionId
})

// Format cost as USD string (e.g., "$0.42")
function formatCost(cost) {
    if (cost == null) return null
    return `$${cost.toFixed(2)}`
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
    return `(${self} + ${subagents})`
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

/**
 * Handle display mode change from the selector.
 */
function onModeChange(event) {
    const newMode = event.target.value
    emit('modeChange', newMode)
}
</script>

<template>
    <header class="session-header" v-if="session">
        <div class="session-title">
            <h2 :title="displayName">{{ displayName }}</h2>
        </div>

        <!-- Mode selector (only in session mode) -->
        <div v-if="mode === 'session'" class="session-controls">
            <wa-select
                :value="displayMode"
                @change="onModeChange"
                size="small"
                class="mode-selector"
            >
                <wa-option :value="DISPLAY_MODE.DEBUG">Debug</wa-option>
                <wa-option :value="DISPLAY_MODE.NORMAL">Normal</wa-option>
                <wa-option :value="DISPLAY_MODE.SIMPLIFIED">Simplified</wa-option>
            </wa-select>
        </div>

        <div class="session-meta">
            <span class="meta-item">
                <wa-icon name="comment" variant="regular"></wa-icon>
                {{ session.message_count ?? '??' }} <span class="nb_lines">({{ session.last_line }} lines)</span>
            </span>
            <span v-if="formattedTotalCost" class="meta-item">
                <wa-icon name="coins" variant="regular"></wa-icon>
                {{ formattedTotalCost }}
                <span v-if="formattedCostBreakdown" class="cost-breakdown">{{ formattedCostBreakdown }}</span>
            </span>
            <wa-progress-ring
                v-if="contextUsagePercentage != null"
                class="context-usage-ring"
                :value="Math.min(contextUsagePercentage, 100)"
                :style="{
                    '--indicator-color': contextUsageColor,
                    '--indicator-width': contextUsageIndicatorWidth
                }"
            ><span class="wa-font-weight-bold">{{ contextUsagePercentage }}%</span></wa-progress-ring>
            <span class="meta-item">
                <wa-icon name="clock" variant="regular"></wa-icon>
                {{ formatDate(session.mtime) }}
            </span>
        </div>
    </header>
</template>

<style scoped>
.session-header {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: var(--wa-space-m);
    padding: var(--wa-space-l);
}

.session-title {
    flex: 1;
    min-width: 0;  /* Allow text truncation */
}

.session-title h2 {
    margin: 0;
    font-size: var(--wa-font-size-l);
    font-weight: 600;
    color: var(--wa-color-text);
    /* Truncate with ellipsis */
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
}

.session-controls {
    flex-shrink: 0;
}

.mode-selector {
    min-width: 120px;
}

.session-meta {
    width: 100%;
    display: flex;
    gap: var(--wa-space-l);
    margin-top: var(--wa-space-s);
    font-size: var(--wa-font-size-s);
    color: var(--wa-color-text-quiet);
}

.meta-item {
    display: flex;
    align-items: center;
    gap: var(--wa-space-xs);
}

.nb_lines {
    font-size: var(--wa-font-size-xs);
    color: var(--wa-color-text-quiet);
}

.cost-breakdown {
    font-size: var(--wa-font-size-xs);
    color: var(--wa-color-text-quiet);
}

.context-usage-ring {
    --size: 2rem;
    --track-width: 3px;
    font-size: var(--wa-font-size-2xs);
}
</style>
