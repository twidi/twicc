<script setup>
// The four summary tiles under the Orchestration header: the number of nodes, one icon row per non-empty
// state, total cost, cumulative duration. Counts are over the displayed nodes (descendants only for
// sessions); cost and cumulative duration are passed in already resolved. Every tile is vertically centred.
import { computed } from 'vue'
import CostDisplay from '../ui/CostDisplay.vue'
import { formatDuration } from '../../utils/date'
import { BUCKET_COLORS, BUCKET_ORDER } from '../../utils/orchestrationView'

const props = defineProps({
    kind: { type: String, required: true, validator: (v) => ['sessions', 'agents'].includes(v) },
    counts: { type: Object, required: true }, // { working, awaiting, idle, stopped }
    cost: { type: Number, default: null },
    showCosts: { type: Boolean, default: true },
    // Sum of the durations of every node below the current session (null: none).
    cumulativeSeconds: { type: Number, default: null },
})

const total = computed(() => BUCKET_ORDER.reduce((n, bucket) => n + props.counts[bucket], 0))

const SESSION_STATES = [
    { bucket: 'working', icon: 'robot', label: 'Working', color: BUCKET_COLORS.working },
    { bucket: 'awaiting', icon: 'hand', label: 'Awaiting', color: BUCKET_COLORS.awaiting },
    { bucket: 'idle', icon: 'check', label: 'Idle', color: BUCKET_COLORS.idle },
    { bucket: 'stopped', icon: 'circle-stop', label: 'Stopped', color: BUCKET_COLORS.stopped },
]
// A subagent is running or done: done is green, not neutral.
const AGENT_STATES = [
    { bucket: 'working', icon: 'robot', label: 'Running', color: BUCKET_COLORS.working },
    { bucket: 'stopped', icon: 'check', label: 'Done', color: BUCKET_COLORS.idle },
]
const stateRows = computed(() => (props.kind === 'agents' ? AGENT_STATES : SESSION_STATES)
    .map(state => ({ ...state, count: props.counts[state.bucket] }))
    .filter(row => row.count > 0))

const countLabel = computed(() => (props.kind === 'agents' ? 'Subagents' : 'Spawned sessions'))
const cumulative = computed(() => (props.cumulativeSeconds == null ? '-' : formatDuration(props.cumulativeSeconds)))
</script>

<template>
    <div class="osum">
        <div class="osum-tile">
            <span class="osum-label">{{ countLabel }}</span>
            <span class="osum-value">{{ total }}</span>
        </div>
        <div class="osum-tile">
            <span class="osum-label">State</span>
            <span v-if="stateRows.length" class="osum-states">
                <span
                    v-for="row in stateRows"
                    :key="row.bucket"
                    class="osum-state"
                    :style="{ color: row.color }"
                    :title="row.label"
                    role="img"
                    :aria-label="`${row.label}: ${row.count}`"
                >
                    <wa-icon :name="row.icon"></wa-icon>
                    <span class="osum-state-count">{{ row.count }}</span>
                </span>
            </span>
            <span v-else class="osum-value">-</span>
        </div>
        <div v-if="showCosts" class="osum-tile">
            <span class="osum-label">Total cost</span>
            <span class="osum-value"><CostDisplay :cost="cost" /></span>
        </div>
        <div class="osum-tile">
            <span class="osum-label">Cumulative time</span>
            <span class="osum-value">{{ cumulative }}</span>
        </div>
    </div>
</template>

<style scoped>
.osum {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(7.5rem, 1fr));
    gap: var(--wa-space-xs);
}

.osum-tile {
    display: flex;
    flex-direction: column;
    justify-content: center;
    gap: var(--wa-space-3xs);
    min-width: 0;
    padding: var(--wa-space-2xs) var(--wa-space-xs);
    border-radius: var(--wa-border-radius-m);
    border: 1px solid var(--wa-color-surface-border);
    background: color-mix(in oklab, var(--wa-color-surface-raised) 55%, transparent);
}

.osum-label {
    font-size: var(--wa-font-size-2xs);
    text-transform: uppercase;
    letter-spacing: 0.05em;
    color: var(--wa-color-text-quiet);
}

.osum-value {
    display: flex;
    align-items: baseline;
    gap: var(--wa-space-xs);
    font-size: var(--wa-font-size-m);
    font-weight: 650;
    line-height: 1.2;
    font-variant-numeric: tabular-nums;
}

/* One row per non-empty state: the state's icon and its count, in the state's colour. */
.osum-states {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-3xs);
}

.osum-state {
    display: inline-flex;
    align-items: center;
    gap: var(--wa-space-2xs);
    font-size: var(--wa-font-size-m);
    font-weight: 650;
    line-height: 1.2;
    font-variant-numeric: tabular-nums;
}

/* Narrow pane: two columns, so the header stays short. */
@container orch (max-width: 480px) {
    .osum {
        grid-template-columns: repeat(2, minmax(0, 1fr));
    }
}
</style>
