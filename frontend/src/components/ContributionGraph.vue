<script setup>
// ContributionGraph.vue - GitHub-style contribution heatmap showing daily activity
// Uses vue3-calendar-heatmap to render an SVG heatmap calendar.
// Fetches daily activity data from the API for a specific project or globally.
// Colors come from CSS custom properties (--sparkline-project-gradient-color-0..4),
// read reactively when the effective theme changes via settingsStore.

import { ref, computed, watch, onMounted } from 'vue'
import { useElementSize } from '@vueuse/core'
import { CalendarHeatmap } from 'vue3-calendar-heatmap'
import 'vue3-calendar-heatmap/dist/style.css'
import { apiFetch } from '../utils/api'
import { ALL_PROJECTS_ID } from '../stores/data'
import { useSettingsStore } from '../stores/settings'

const props = defineProps({
    /** Project ID or ALL_PROJECTS_ID for global view */
    projectId: {
        type: String,
        required: true,
    },
})

const settingsStore = useSettingsStore()

const graphContainer = ref(null)
const { width: containerWidth } = useElementSize(graphContainer)
const isVertical = computed(() => containerWidth.value > 0 && containerWidth.value < 600)

const dailyActivity = ref([])
const loading = ref(false)

const isDark = computed(() => settingsStore.getEffectiveTheme === 'dark')

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
    return dailyActivity.value.map(d => ({
        date: d.date,
        count: d.count,
    }))
})

/**
 * Custom tooltip formatter: "N messages on Jan 1, 2026"
 */
function tooltipFormatter(item, unit) {
    const date = item.date
    const dateStr = date.toLocaleDateString('en-US', {
        month: 'short',
        day: 'numeric',
        year: 'numeric',
    })
    const count = item.count || 0
    if (count === 0) {
        return `No ${unit} on ${dateStr}`
    }
    return `${count} ${count === 1 ? unit.replace(/s$/, '') : unit} on ${dateStr}`
}

async function fetchDailyActivity() {
    loading.value = true
    try {
        const url = props.projectId === ALL_PROJECTS_ID
            ? '/api/daily-activity/'
            : `/api/projects/${encodeURIComponent(props.projectId)}/daily-activity/`

        const res = await apiFetch(url)
        if (res.ok) {
            const data = await res.json()
            dailyActivity.value = data.daily_activity || []
        }
    } catch (error) {
        console.error('Failed to load daily activity:', error)
    } finally {
        loading.value = false
    }
}

// Fetch on mount and when projectId changes
onMounted(fetchDailyActivity)
watch(() => props.projectId, fetchDailyActivity)
</script>

<template>
    <h3 class="contribution-graph-title">User messages graph</h3>
    <div ref="graphContainer" class="contribution-graph" :class="{ vertical: isVertical }">
        <CalendarHeatmap
            v-if="heatmapValues.length > 0 && rangeColor.length === 6"
            :key="`${rangeColor.join(',')}-${isVertical}`"
            :values="heatmapValues"
            :end-date="endDate"
            :range-color="rangeColor"
            :round="2"
            :tooltip="true"
            tooltip-unit="user messages"
            :tooltip-formatter="tooltipFormatter"
            :dark-mode="isDark"
            :vertical="isVertical"
        />
        <div v-else-if="!loading" class="no-data">
            No activity data
        </div>
    </div>
</template>

<style scoped>
.contribution-graph {
    margin-top: var(--wa-space-l);
    width: 100%;
    display: flex;
    justify-content: center;
    padding-inline: var(--wa-space-m);
}

.contribution-graph :deep(.vch__container) {
    width: 100%;
    max-width: 80rem;
}
.contribution-graph.vertical :deep(.vch__container) {
    max-width: 20rem;
}

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

/* The legend SVG has a height attr but no width; Firefox collapses it in flex layouts.
   Setting overflow:visible + min-width ensures the rects are shown cross-browser. */
.contribution-graph :deep(svg.vch__external-legend-wrapper) {
    overflow: visible;
    min-width: 5rem;
}

/* Hide the first color as we use the same one for "no data" and "0 entries" */
.contribution-graph :deep(.vch__legend__wrapper) rect:first-of-type {
    display: none;
}
.contribution-graph :deep(.vch__legend__wrapper) {
    translate: 0 -1rem;
}
.contribution-graph :deep(.vch__legend-right .vch__legend__wrapper) {
    translate: 0 0;
}

.contribution-graph :deep(.vch__legend__wrapper text:first-child) {
    translate: 0 .5rem;
}
.contribution-graph :deep(.vch__legend-right > div > div:first-child) {
    position: relative;
    left: 1rem;
}
.contribution-graph :deep(.vch__legend-right > div > div) {
    color: var(--wa-color-text-quiet);
    font-size: var(--wa-font-size-2xs);
}

.contribution-graph-title {
    margin: 0;
    margin-top: var(--wa-space-l);
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
