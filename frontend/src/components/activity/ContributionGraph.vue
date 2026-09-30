<script setup>
// ContributionGraph.vue - Pure presentation component for a GitHub-style contribution heatmap.
// Receives pre-fetched daily activity data via props.
// Supports three modes: 'messages' (user message count), 'sessions' (session count), and 'cost' (USD cost).
// Colors come from CSS custom properties (--sparkline-project-gradient-color-0..4),
// read reactively when the effective theme changes via settingsStore.

import { computed } from 'vue'
import { CalendarHeatmap } from 'vue3-calendar-heatmap'
import 'vue3-calendar-heatmap/dist/style.css'
import { useSettingsStore } from '../../stores/settings'

const props = defineProps({
    /** Daily activity data: array of { date, user_message_count, session_count, cost } */
    dailyActivity: {
        type: Array,
        required: true,
    },
    /** Display mode: 'messages' for user message count, 'sessions' for session count, 'cost' for USD cost */
    mode: {
        type: String,
        default: 'messages',
        validator: v => ['messages', 'sessions', 'cost'].includes(v),
    },
})

const isCostMode = computed(() => props.mode === 'cost')
const isSessionsMode = computed(() => props.mode === 'sessions')

const settingsStore = useSettingsStore()

const isDark = computed(() => settingsStore.getEffectiveColorScheme === 'dark')

/**
 * Read heatmap colors from CSS custom properties (--sparkline-project-gradient-color-0..4).
 * Returns a 6-element array for the heatmap:
 *   [0] = null days (no data)   → gradient-color-0
 *   [1] = count: 0 days         → gradient-color-0 (same, since backend never sends count=0)
 *   [2..5] = activity levels    → gradient-color-1..4
 */
function readCssColors() {
    const style = getComputedStyle(document.documentElement)
    const colors = []
    for (let i = 0; i <= 4; i++) {
        const color = style.getPropertyValue(`--sparkline-project-gradient-color-${i}`).trim()
        if (color) {
            colors.push(color)
        }
    }
    if (colors.length === 5) {
        // Duplicate color-0 for both null and count=0 slots
        return [colors[0], colors[0], colors[1], colors[2], colors[3], colors[4]]
    }
    return ['#ebedf0', '#ebedf0', '#aceebb', '#4ac26b', '#2da44e', '#116329']
}

// Re-read CSS colors whenever the effective theme changes
const rangeColor = computed(() => {
    // Access isDark.value to create the reactive dependency on theme changes
    isDark.value
    return readCssColors()
})

const endDate = computed(() => new Date())

const heatmapValues = computed(() => {
    return props.dailyActivity.map(d => {
        let count
        if (isCostMode.value) {
            count = parseFloat(d.cost) || 0
        } else if (isSessionsMode.value) {
            count = d.session_count
        } else {
            count = d.user_message_count
        }
        return { date: d.date, count }
    })
})

// Custom max: 50% of peak value (instead of library default 80%).
// This widens the top color bucket, giving more contrast to lower activity days.
const heatmapMax = computed(() => {
    const counts = heatmapValues.value.map(v => v.count)
    const peak = Math.max(...counts, 0)
    return Math.ceil(peak / 2) || 1
})

/**
 * Custom tooltip formatter.
 * Messages mode: "N messages on Jan 1, 2026"
 * Sessions mode: "N sessions on Jan 1, 2026"
 * Cost mode: "$1.23 on Jan 1, 2026"
 */
function tooltipFormatter(item, unit) {
    const date = item.date
    const dateStr = date.toLocaleDateString('en-US', {
        month: 'short',
        day: 'numeric',
        year: 'numeric',
    })
    const count = item.count || 0
    if (isCostMode.value) {
        if (count === 0) {
            return `No cost on ${dateStr}`
        }
        return `$${count.toFixed(2)} on ${dateStr}`
    }
    if (count === 0) {
        return `No ${unit} on ${dateStr}`
    }
    return `${count} ${count === 1 ? unit.replace(/s$/, '') : unit} on ${dateStr}`
}
</script>

<template>
    <div class="contribution-graph">
        <h3 class="contribution-graph-title">{{ isCostMode ? 'Cost per day' : isSessionsMode ? 'Sessions created per day' : 'Message turns per day' }}</h3>
        <CalendarHeatmap
            v-if="heatmapValues.length > 0 && rangeColor.length === 6"
            :key="`${rangeColor.join(',')}-${mode}`"
            :values="heatmapValues"
            :end-date="endDate"
            :max="heatmapMax"
            :range-color="rangeColor"
            :round="2"
            :tooltip="true"
            :tooltip-unit="isCostMode ? 'cost' : isSessionsMode ? 'sessions created' : 'message turns'"
            :tooltip-formatter="tooltipFormatter"
            :dark-mode="isDark"
        />
        <div v-else-if="dailyActivity.length === 0" class="no-data">
            No activity data
        </div>
    </div>
</template>

<style scoped>
.contribution-graph {
    margin-top: var(--wa-space-l);
    display: flex;
    flex-direction: column;
    justify-content: center;
    align-items: center;
    padding-inline: var(--wa-space-m);
}

.contribution-graph :deep(.vch__container) {
    width: 100%;
    max-width: 80rem;
}

/* The wave (visual refresh step 7a, stats motion design §5.2): the cells fade in week by
   week, then day by day. Opacity only: the cell's own SVG transform attribute places it, a
   CSS transform would override it. --heat-week-delay is registered in motion.css, so it
   computes sibling-index() on the week group and its cells inherit the resulting time.
   Without sibling-index() every cell fades together. */
.contribution-graph :deep(.vch__day__square) {
    animation: heatmap-cell-in 360ms ease-in-out backwards;
}
@supports (animation-delay: calc(sibling-index() * 1ms)) {
    .contribution-graph :deep(.vch__month__wrapper) {
        --heat-week-delay: calc(sibling-index() * 14ms);
    }
    .contribution-graph :deep(.vch__day__square) {
        animation-delay: calc(var(--heat-week-delay) + sibling-index() * 10ms);
    }
}
@keyframes heatmap-cell-in { from { opacity: 0; } }

/* Override vue3-calendar-heatmap styles for theme integration */
.contribution-graph :deep(svg.vch__wrapper) {
    width: 100%;
    height: auto;
}

.contribution-graph :deep(.vch__months__labels__wrapper text.vch__month__label) {
    fill: var(--wa-color-text-quiet);
}

.contribution-graph :deep(.vch__days__labels__wrapper text.vch__day__label) {
    fill: var(--wa-color-text-quiet);
}

.contribution-graph :deep(.vch__legend__wrapper), .contribution-graph :deep(.vch__legend) {
    display: none;
}

.contribution-graph-title {
    margin-block: var(--wa-space-l);
    padding-inline: var(--wa-space-m);
    color: var(--wa-color-text-quiet);
    font-size: var(--wa-font-size-l);
}

.no-data {
    font-size: var(--wa-font-size-s);
    color: var(--wa-color-text-quiet);
    text-align: center;
    padding: var(--wa-space-m);
}
</style>
