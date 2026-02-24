<script setup>
// ContributionGraphs.vue - Fetches daily activity data once and renders
// the user messages, sessions, and cost contribution graphs.
// Supports toggling between heatmap view and sparkline view.

import { ref, computed, watch, nextTick, onMounted } from 'vue'
import { apiFetch } from '../utils/api'
import { ALL_PROJECTS_ID } from '../stores/data'
import ActivityDashboard from './ActivityDashboard.vue'
import ContributionGraph from './ContributionGraph.vue'
import ContributionSparklines from './ContributionSparklines.vue'

const props = defineProps({
    /** Project ID or ALL_PROJECTS_ID for global view */
    projectId: {
        type: String,
        required: true,
    },
})

const dailyActivity = ref([])
const activityTotals = ref(null)

/** View mode: 'heatmap' (default) or 'sparkline' */
const viewMode = ref('heatmap')
const isSparkline = ref(false)
const viewSwitchRef = ref(null)

/** Combined mode: show all three sparklines overlaid in a single graph */
const isCombined = ref(false)
const combinedSwitchRef = ref(null)

/** Time range slider: discrete steps mapped to day counts */
const rangeSteps = [
    { days: 7, label: '1 week' },
    { days: 14, label: '2 weeks' },
    { days: 30, label: '1 month' },
    { days: 90, label: '3 months' },
    { days: 182, label: '6 months' },
    { days: 365, label: '1 year' },
]
const rangeSliderRef = ref(null)
const rangeStepIndex = ref(rangeSteps.length - 1) // default: 1Y (last step)
const displayDays = computed(() => rangeSteps[rangeStepIndex.value].days)
const rangeLabel = computed(() => rangeSteps[rangeStepIndex.value].label)

function onRangeChange(e) {
    rangeStepIndex.value = parseInt(e.target.value, 10)
}

function onViewModeChange(e) {
    isSparkline.value = e.target.checked
    viewMode.value = isSparkline.value ? 'sparkline' : 'heatmap'
}

/** Click on the left "Heatmap" label toggles the switch back to heatmap mode */
function onHeatmapLabelClick() {
    if (isSparkline.value && viewSwitchRef.value) {
        viewSwitchRef.value.click()
    }
}

function onCombinedModeChange(e) {
    isCombined.value = e.target.checked
}

// Sync Web Component states when they re-mount after being hidden by v-if.
// Web Components don't reliably pick up Vue's prop bindings on mount.
watch(isSparkline, (val) => {
    if (val) {
        nextTick(() => {
            if (combinedSwitchRef.value && combinedSwitchRef.value.checked !== isCombined.value) {
                combinedSwitchRef.value.checked = isCombined.value
            }
            if (rangeSliderRef.value && rangeSliderRef.value.value !== rangeStepIndex.value) {
                rangeSliderRef.value.value = rangeStepIndex.value
            }
        })
    }
})

/** Click on the left "Separate" label toggles the switch back to separate mode */
function onSeparateLabelClick() {
    if (isCombined.value && combinedSwitchRef.value) {
        combinedSwitchRef.value.click()
    }
}

async function fetchDailyActivity() {
    try {
        const url = props.projectId === ALL_PROJECTS_ID
            ? '/api/daily-activity/'
            : `/api/projects/${encodeURIComponent(props.projectId)}/daily-activity/`

        const res = await apiFetch(url)
        if (res.ok) {
            const data = await res.json()
            dailyActivity.value = data.daily_activity || []
            activityTotals.value = data.totals || null
        }
    } catch (error) {
        console.error('Failed to load daily activity:', error)
    }
}

onMounted(fetchDailyActivity)
watch(() => props.projectId, fetchDailyActivity)
</script>

<template>
    <ActivityDashboard :daily-activity="dailyActivity" :totals="activityTotals" mode="sessions" />
    <ActivityDashboard :daily-activity="dailyActivity" :totals="activityTotals" mode="messages" />

    <!-- Toggle switches -->
    <div v-if="dailyActivity.length > 0" class="view-toggles">
        <!-- Heatmap / Graph switch -->
        <div class="view-toggle">
            <span
                class="view-toggle-label"
                :class="{ active: !isSparkline }"
                @click="onHeatmapLabelClick"
            >Heatmap</span>
            <wa-switch
                ref="viewSwitchRef"
                size="small"
                :checked="isSparkline"
                @change="onViewModeChange"
            ><span class="view-toggle-label" :class="{ active: isSparkline }">Graph</span></wa-switch>
        </div>

        <!-- Separate / Combined switch (only visible in graph mode) -->
        <div v-if="isSparkline" class="view-toggle">
            <span
                class="view-toggle-label"
                :class="{ active: !isCombined }"
                @click="onSeparateLabelClick"
            >Separate</span>
            <wa-switch
                ref="combinedSwitchRef"
                size="small"
                :checked="isCombined"
                @change="onCombinedModeChange"
            ><span class="view-toggle-label" :class="{ active: isCombined }">Combined</span></wa-switch>
        </div>

        <!-- Time range slider (only visible in graph mode) -->
        <div v-if="isSparkline" class="range-control">
            <span class="range-label">{{ rangeLabel }}</span>
            <wa-slider
                ref="rangeSliderRef"
                :min.prop="0"
                :max.prop="rangeSteps.length - 1"
                :step.prop="1"
                :value.prop="rangeStepIndex"
                @input="onRangeChange"
                size="small"
                class="range-slider"
            ></wa-slider>
        </div>
    </div>

    <!-- Heatmap view (default) -->
    <template v-if="viewMode === 'heatmap'">
        <ContributionGraph :daily-activity="dailyActivity" mode="sessions" />
        <ContributionGraph :daily-activity="dailyActivity" mode="messages" />
        <ContributionGraph :daily-activity="dailyActivity" mode="cost" />
    </template>

    <!-- Sparkline view -->
    <template v-else>
        <ContributionSparklines :daily-activity="dailyActivity" :combined="isCombined" :days="displayDays" />
    </template>
</template>

<style scoped>
.view-toggles {
    display: flex;
    align-items: center;
    justify-content: center;
    flex-wrap: wrap;
    gap: var(--wa-space-xl);
    margin-top: var(--wa-space-l);
}

.view-toggle {
    display: flex;
    align-items: center;
    gap: var(--wa-space-s);
}

.view-toggle-label {
    font-size: var(--wa-font-size-s);
    color: var(--wa-color-text-quiet);
    cursor: pointer;
    user-select: none;
    transition: color 0.2s;
}

.view-toggle-label.active {
    color: var(--wa-color-text-normal);
}

.range-control {
    display: flex;
    align-items: center;
    gap: var(--wa-space-s);
}

.range-label {
    font-size: var(--wa-font-size-s);
    color: var(--wa-color-text-normal);
    font-weight: 600;
    min-width: 5.5em;
    text-align: center;
}

.range-slider {
    width: 8rem;
}
</style>
