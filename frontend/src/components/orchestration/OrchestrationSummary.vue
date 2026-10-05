<script setup>
// The four summary tiles under the Orchestration header: count with a donut of the state buckets,
// working (sessions) or stopped (subagents), total cost, span. Counts and donut are over the displayed
// nodes (descendants only for sessions); cost and span are passed in already resolved (spec 3.2).
import { computed } from 'vue'
import CostDisplay from '../ui/CostDisplay.vue'
import { formatDuration } from '../../utils/date'
import { BUCKET_COLORS, BUCKET_ORDER } from '../../utils/orchestrationView'

const props = defineProps({
    kind: { type: String, required: true, validator: (v) => ['sessions', 'agents'].includes(v) },
    counts: { type: Object, required: true }, // { working, awaiting, idle, stopped }
    cost: { type: Number, default: null },
    showCosts: { type: Boolean, default: true },
    spanSeconds: { type: Number, default: null },
})

const total = computed(() => BUCKET_ORDER.reduce((n, bucket) => n + props.counts[bucket], 0))
const running = computed(() => total.value - props.counts.stopped)

// Conic gradient: one arc per non-empty bucket; a neutral full ring with no node.
const donut = computed(() => {
    if (!total.value) return `conic-gradient(${BUCKET_COLORS.stopped} 0 100%)`
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
const span = computed(() => (props.spanSeconds == null ? '-' : formatDuration(props.spanSeconds)))
</script>

<template>
    <div class="osum">
        <div class="osum-tile osum-tile--donut">
            <span class="osum-donut" :style="{ background: donut }" aria-hidden="true"></span>
            <div class="osum-body">
                <span class="osum-label">{{ countLabel }}</span>
                <span class="osum-value">{{ total }}<small>{{ running }} running</small></span>
            </div>
        </div>
        <div v-if="kind === 'sessions'" class="osum-tile">
            <span class="osum-label">Working</span>
            <span class="osum-value osum-value--working">{{ counts.working }}<small>{{ counts.awaiting }} awaiting</small></span>
        </div>
        <div v-else class="osum-tile">
            <span class="osum-label">Stopped</span>
            <span class="osum-value">{{ counts.stopped }}</span>
        </div>
        <div v-if="showCosts" class="osum-tile">
            <span class="osum-label">Total cost</span>
            <span class="osum-value"><CostDisplay :cost="cost" /></span>
        </div>
        <div class="osum-tile">
            <span class="osum-label">Span</span>
            <span class="osum-value">{{ span }}</span>
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
    gap: var(--wa-space-3xs);
    min-width: 0;
    padding: var(--wa-space-2xs) var(--wa-space-xs);
    border-radius: var(--wa-border-radius-m);
    border: 1px solid var(--wa-color-surface-border);
    background: color-mix(in oklab, var(--wa-color-surface-raised) 55%, transparent);
}

.osum-tile--donut {
    flex-direction: row;
    align-items: center;
    gap: var(--wa-space-xs);
}

.osum-body {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-3xs);
    min-width: 0;
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

.osum-value small {
    font-size: var(--wa-font-size-xs);
    font-weight: 400;
    color: var(--wa-color-text-quiet);
}

.osum-value--working {
    color: var(--wa-color-blue-60);
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

/* Narrow pane: two columns, so the header stays short. */
@container orch (max-width: 480px) {
    .osum {
        grid-template-columns: repeat(2, minmax(0, 1fr));
    }
}
</style>
