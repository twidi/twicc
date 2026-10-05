<script setup>
// The four summary tiles under the Orchestration header: the number of nodes, one icon row per non-empty
// state (with a donut when there are several), total cost, cumulative duration. Counts are over the displayed nodes (descendants only for
// sessions); cost and cumulative duration are passed in already resolved. Every tile is centred both ways.
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
// A subagent is running or done (stopped, grey like a stopped session).
const AGENT_STATES = [
    { bucket: 'working', icon: 'robot', label: 'Running', color: BUCKET_COLORS.working },
    { bucket: 'stopped', icon: 'circle-stop', label: 'Done', color: BUCKET_COLORS.stopped },
]
const stateRows = computed(() => (props.kind === 'agents' ? AGENT_STATES : SESSION_STATES)
    .map(state => ({ ...state, count: props.counts[state.bucket] }))
    .filter(row => row.count > 0))

// Conic gradient: one arc per non-empty bucket. The ring is only drawn with two states or more (a single state
// would be a plain full ring, which says nothing the row does not).
const donut = computed(() => {
    if (!total.value) return 'conic-gradient(var(--wa-color-neutral-50) 0 100%)'
    let from = 0
    const stops = []
    for (const bucket of BUCKET_ORDER) {
        if (!props.counts[bucket]) continue
        const to = from + (props.counts[bucket] / total.value) * 100
        stops.push(`${BUCKET_COLORS[bucket]} ${from}% ${to}%`)
        from = to
    }
    return `conic-gradient(${stops.join(', ')})`
})

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
            <span class="osum-states-group">
                <span v-if="stateRows.length > 1" class="osum-donut" :style="{ background: donut }" aria-hidden="true"></span>
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
            </span>
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
    align-items: center;
    justify-content: center;
    text-align: center;
    gap: var(--wa-space-3xs);
    min-width: 0;
    padding: var(--wa-space-2xs) var(--wa-space-xs);
    border-radius: var(--wa-border-radius-m);
    border: 1px solid var(--orch-card-border);
    background: var(--orch-card-bg);
    box-shadow: var(--orch-card-shadow);
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
    justify-content: center;
    gap: var(--wa-space-xs);
    font-size: var(--wa-font-size-m);
    font-weight: 650;
    line-height: 1.2;
    font-variant-numeric: tabular-nums;
}

/* The donut and the state rows, side by side, centred in the tile. */
.osum-states-group {
    display: flex;
    align-items: center;
    justify-content: center;
    gap: var(--wa-space-xs);
}

.osum-donut {
    flex: none;
    width: 1.6rem;
    height: 1.6rem;
    border-radius: 50%;
    display: grid;
    place-items: center;
}

/* The hole: the page surface as an opaque colour (the cards' own surface token is transparent). */
.osum-donut::before {
    content: '';
    width: 64%;
    height: 64%;
    border-radius: 50%;
    background: var(--surface-solid);
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
