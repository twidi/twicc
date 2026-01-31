<script setup>
import { computed } from 'vue'
import { useDataStore } from '../stores/data'
import { formatDate } from '../utils/date'
import { MAX_CONTEXT_TOKENS } from '../constants'
import ProjectBadge from './ProjectBadge.vue'

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

// Session data from store
const session = computed(() => store.getSession(props.sessionId))

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

// Format model name for display from pre-parsed family and version
const formattedModel = computed(() => {
    const model = session.value?.model
    if (!model?.family || !model?.version) return null
    return `${model.family} ${model.version}`
})

</script>

<template>
    <header class="session-header" v-if="session">
        <div v-if="mode === 'session'" class="session-title">
            <h2 :title="displayName">{{ displayName }}</h2>
            <ProjectBadge v-if="session.project_id" :project-id="session.project_id" class="session-project" />
        </div>

        <div class="session-meta">
            <span class="meta-item">
                <wa-icon auto-width name="comment" variant="regular"></wa-icon>
                <span>
                    {{ session.message_count ?? '??' }}
                    <span class="nb_lines">({{ session.last_line }} lines)</span>
                </span>
            </span>
            <span class="meta-item">
                <wa-icon auto-width name="clock" variant="regular"></wa-icon>
                <span>
                    {{ formatDate(session.mtime) }}
                </span>
            </span>
            <span v-if="formattedTotalCost" class="meta-item">
                <wa-icon auto-width name="dollar-sign" variant="solid"></wa-icon>
                <span>
                    {{ formattedTotalCost }}
                    <span v-if="formattedCostBreakdown" class="cost-breakdown">{{ formattedCostBreakdown }}</span>
                </span>
            </span>
            <span v-if="formattedModel" class="meta-item">
                <wa-icon auto-width name="robot" variant="classic"></wa-icon>
                <span>
                    {{ formattedModel }}
                </span>
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
        </div>
    </header>
    <wa-divider></wa-divider>
</template>

<style scoped>
.session-header {
    padding: var(--wa-space-xs);
}

.session-title {
    flex: 1;
    min-width: 0;  /* Allow text truncation */
}

.session-title h2 {
    margin: 0;
    font-size: var(--wa-font-size-l);
    font-weight: 600;
    color: var(--wa-color-text-normal);
    /* Truncate with ellipsis */
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
}

.session-project {
    font-size: var(--wa-font-size-xs);
    color: var(--wa-color-text-quiet);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
}

.session-meta {
    width: 100%;
    display: flex;
    flex-wrap: wrap;
    column-gap: var(--wa-space-m);
    row-gap: var(--wa-space-xs);
    font-size: var(--wa-font-size-s);
}

.meta-item {
    display: flex;
    align-items: center;
    gap: var(--wa-space-xs);
}
.meta-item :deep( > span ) {
    display: flex;
    align-items: baseline;
    gap: 0.5em;
}

.meta-item :deep( > span > span ) {
    font-size: var(--wa-font-size-xs);
    color: var(--wa-color-text-quiet);
}

.context-usage-ring {
    --size: 2rem;
    --track-width: 3px;
    font-size: var(--wa-font-size-2xs);
}

wa-divider {
    --width: 4px;
    margin: 0;
}

</style>
