<script setup>
// ContributionSparklines.vue - Three sparkline curves (sessions, messages, cost)
// rendered from daily activity data over 365 days, using the exact same SVG style
// as ActivitySparkline.vue. Each curve is independently scaled to its own max value,
// filling the full height of its graph area.
// Supports a "combined" mode that overlays all three curves in a single SVG
// with distinct colors (green for messages, blue for sessions, red for cost).

import { computed } from 'vue'

const props = defineProps({
    /** Daily activity data: array of { date, user_message_count, session_count, cost } */
    dailyActivity: {
        type: Array,
        required: true,
    },
    /** Whether to display all three curves combined in a single SVG */
    combined: {
        type: Boolean,
        default: false,
    },
    /** Number of most recent days to display (default: 365 = full year) */
    days: {
        type: Number,
        default: 365,
    },
})

const DAYS = 365
const SVG_HEIGHT = 150
const GRAPH_HEIGHT = 146
const VIEWBOX_HEIGHT = 200 // viewBox goes from 0 to 200, graph draws within 0..SVG_HEIGHT
const MIN_Y = 1.0

/**
 * Build a full 365-day array from sparse daily activity data.
 * Days with no data get 0 values. The array covers [today - 364 days, today].
 */
const dailyMap = computed(() => {
    const map = new Map()
    for (const d of props.dailyActivity) {
        map.set(d.date, d)
    }
    return map
})

const fullYearData = computed(() => {
    const today = new Date()
    today.setHours(0, 0, 0, 0)
    const result = []
    for (let i = DAYS - 1; i >= 0; i--) {
        const d = new Date(today)
        d.setDate(d.getDate() - i)
        const dateStr = toDateStr(d)
        const entry = dailyMap.value.get(dateStr)
        result.push({
            date: dateStr,
            sessions: entry ? entry.session_count || 0 : 0,
            messages: entry ? entry.user_message_count || 0 : 0,
            cost: entry ? (parseFloat(entry.cost) || 0) : 0,
        })
    }
    return result
})

function toDateStr(date) {
    const y = date.getFullYear()
    const m = String(date.getMonth() + 1).padStart(2, '0')
    const d = String(date.getDate()).padStart(2, '0')
    return `${y}-${m}-${d}`
}

// Displayed data: slice the full year to the requested number of days
const displayData = computed(() => {
    const data = fullYearData.value
    if (props.days >= DAYS) return data
    return data.slice(data.length - props.days)
})

// SVG width is always based on 365 days to keep a consistent stroke thickness
// regardless of the displayed time range (avoids stroke scaling with preserveAspectRatio="none")
const svgWidth = computed(() => DAYS * 3 - 1)

/**
 * Build polyline points for a given data array with its max value.
 * Each curve fills the full graph height based on its own maximum.
 */
function buildPolylinePoints(values, maxValue, graphHeight) {
    if (!values.length) return ''
    const xStep = svgWidth.value / (values.length - 1)
    return values
        .map((count, i) => {
            const x = Math.round(i * xStep * 100) / 100
            const y = maxValue > 0
                ? MIN_Y + (count / maxValue) * (graphHeight - MIN_Y)
                : MIN_Y
            return `${x},${Math.round(y * 100) / 100}`
        })
        .join(' ')
}

// Extract arrays for each metric from the displayed window
const sessionsValues = computed(() => displayData.value.map(d => d.sessions))
const messagesValues = computed(() => displayData.value.map(d => d.messages))
const costValues = computed(() => displayData.value.map(d => d.cost))

// Max values for each metric (each graph is independently scaled)
const sessionsMax = computed(() => Math.max(...sessionsValues.value))
const messagesMax = computed(() => Math.max(...messagesValues.value))
const costMax = computed(() => Math.max(...costValues.value))

// Build polyline points for each metric
const sessionsPoints = computed(() => buildPolylinePoints(sessionsValues.value, sessionsMax.value, GRAPH_HEIGHT))
const messagesPoints = computed(() => buildPolylinePoints(messagesValues.value, messagesMax.value, GRAPH_HEIGHT))
const costPoints = computed(() => buildPolylinePoints(costValues.value, costMax.value, GRAPH_HEIGHT))

// Separate curves config (one SVG per metric, each with its own color)
const separateCurves = computed(() => [
    {
        key: 'sessions',
        label: 'Sessions created per day',
        points: sessionsPoints.value,
        gradientId: 'sparkline-contrib-sessions-gradient',
        maskId: 'sparkline-contrib-sessions-mask',
        colorPrefix: 'blue',
    },
    {
        key: 'messages',
        label: 'Message turns per day',
        points: messagesPoints.value,
        gradientId: 'sparkline-contrib-messages-gradient',
        maskId: 'sparkline-contrib-messages-mask',
        colorPrefix: 'green',
    },
    {
        key: 'cost',
        label: 'Cost per day',
        points: costPoints.value,
        gradientId: 'sparkline-contrib-cost-gradient',
        maskId: 'sparkline-contrib-cost-mask',
        colorPrefix: 'red',
    },
])

// Combined curves config (all three overlaid in one SVG, distinct colors)
const combinedCurves = computed(() => [
    {
        key: 'sessions',
        label: 'Sessions',
        points: sessionsPoints.value,
        gradientId: 'sparkline-combined-sessions-gradient',
        maskId: 'sparkline-combined-sessions-mask',
        colorPrefix: 'blue',
    },
    {
        key: 'messages',
        label: 'Message turns',
        points: messagesPoints.value,
        gradientId: 'sparkline-combined-messages-gradient',
        maskId: 'sparkline-combined-messages-mask',
        colorPrefix: 'green',
    },
    {
        key: 'cost',
        label: 'Cost',
        points: costPoints.value,
        gradientId: 'sparkline-combined-cost-gradient',
        maskId: 'sparkline-combined-cost-mask',
        colorPrefix: 'red',
    },
])

/**
 * Return CSS variable names for gradient stops and stroke based on color prefix.
 * 'green' uses the default sparkline vars, 'blue' and 'red' use the new dedicated vars.
 */
function colorVars(prefix) {
    if (prefix === 'green') {
        return {
            g1: 'var(--sparkline-project-gradient-color-1)',
            g2: 'var(--sparkline-project-gradient-color-2)',
            g3: 'var(--sparkline-project-gradient-color-3)',
            g4: 'var(--sparkline-project-gradient-color-4)',
            stroke: 'var(--sparkline-project-stroke-color)',
        }
    }
    return {
        g1: `var(--sparkline-${prefix}-gradient-color-1)`,
        g2: `var(--sparkline-${prefix}-gradient-color-2)`,
        g3: `var(--sparkline-${prefix}-gradient-color-3)`,
        g4: `var(--sparkline-${prefix}-gradient-color-4)`,
        stroke: `var(--sparkline-${prefix}-stroke-color)`,
    }
}
</script>

<template>
    <div v-if="dailyActivity.length > 0" class="contribution-sparklines">

        <!-- Combined mode: single SVG with all three curves overlaid -->
        <template v-if="combined">
            <div class="sparkline-row">
                <h3 class="sparkline-title">Activity per day</h3>
                <svg
                    :width="svgWidth"
                    aria-hidden="true"
                    :height="SVG_HEIGHT"
                    class="contribution-sparkline"
                    :viewBox="`0 0 ${svgWidth} ${VIEWBOX_HEIGHT}`"
                    preserveAspectRatio="none"
                >
                    <defs>
                        <template v-for="curve in combinedCurves" :key="curve.key">
                            <linearGradient :id="curve.gradientId" x1="0" x2="0" y1="1" y2="0">
                                <stop offset="0%" :stop-color="colorVars(curve.colorPrefix).g1"></stop>
                                <stop offset="10%" :stop-color="colorVars(curve.colorPrefix).g2"></stop>
                                <stop offset="25%" :stop-color="colorVars(curve.colorPrefix).g3"></stop>
                                <stop offset="50%" :stop-color="colorVars(curve.colorPrefix).g4"></stop>
                            </linearGradient>
                            <mask :id="curve.maskId" x="0" y="0" :width="svgWidth" :height="GRAPH_HEIGHT">
                                <polyline
                                    :transform="`translate(0, ${GRAPH_HEIGHT}) scale(1,-1)`"
                                    :points="curve.points"
                                    fill="transparent"
                                    :stroke="colorVars(curve.colorPrefix).stroke"
                                    stroke-width="2"
                                ></polyline>
                            </mask>
                        </template>
                    </defs>

                    <g transform="translate(0, 2.0)">
                        <rect
                            v-for="curve in combinedCurves"
                            :key="curve.key"
                            x="0"
                            y="-2"
                            :width="svgWidth"
                            :height="GRAPH_HEIGHT + 2"
                            :style="`stroke: none; fill: url(#${curve.gradientId}); mask: url(#${curve.maskId});`"
                        ></rect>
                    </g>
                </svg>

                <!-- Legend -->
                <div class="sparkline-legend">
                    <div v-for="curve in combinedCurves" :key="curve.key" class="sparkline-legend-item">
                        <span class="sparkline-legend-swatch" :class="`sparkline-legend-swatch--${curve.colorPrefix}`"></span>
                        <span class="sparkline-legend-text">{{ curve.label }}</span>
                    </div>
                </div>
            </div>
        </template>

        <!-- Separate mode: one SVG per metric (all green) -->
        <template v-else>
            <div v-for="curve in separateCurves" :key="curve.key" class="sparkline-row">
                <h3 class="sparkline-title">{{ curve.label }}</h3>
                <svg
                    :width="svgWidth"
                    aria-hidden="true"
                    :height="SVG_HEIGHT"
                    class="contribution-sparkline"
                    :viewBox="`0 0 ${svgWidth} ${VIEWBOX_HEIGHT}`"
                    preserveAspectRatio="none"
                >
                    <defs>
                        <linearGradient :id="curve.gradientId" x1="0" x2="0" y1="1" y2="0">
                            <stop offset="0%" :stop-color="colorVars(curve.colorPrefix).g1"></stop>
                            <stop offset="10%" :stop-color="colorVars(curve.colorPrefix).g2"></stop>
                            <stop offset="25%" :stop-color="colorVars(curve.colorPrefix).g3"></stop>
                            <stop offset="50%" :stop-color="colorVars(curve.colorPrefix).g4"></stop>
                        </linearGradient>
                        <mask :id="curve.maskId" x="0" y="0" :width="svgWidth" :height="GRAPH_HEIGHT">
                            <polyline
                                :transform="`translate(0, ${GRAPH_HEIGHT}) scale(1,-1)`"
                                :points="curve.points"
                                fill="transparent"
                                :stroke="colorVars(curve.colorPrefix).stroke"
                                stroke-width="2"
                            ></polyline>
                        </mask>
                    </defs>

                    <g transform="translate(0, 2.0)">
                        <rect
                            x="0"
                            y="-2"
                            :width="svgWidth"
                            :height="GRAPH_HEIGHT + 2"
                            :style="`stroke: none; fill: url(#${curve.gradientId}); mask: url(#${curve.maskId});`"
                        ></rect>
                    </g>
                </svg>
            </div>
        </template>

    </div>
    <div v-else class="no-data">
        No activity data
    </div>
</template>

<style scoped>
.contribution-sparklines {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-l);
    padding-inline: var(--wa-space-m);
    max-width: 80rem;
    margin-inline: auto;
    width: 100%;
}

.sparkline-row {
    display: flex;
    flex-direction: column;
    align-items: center;
}

.sparkline-title {
    margin-block: var(--wa-space-l) var(--wa-space-s);
    padding-inline: var(--wa-space-m);
    color: var(--wa-color-text-quiet);
    font-size: var(--wa-font-size-l);
}

.contribution-sparkline {
    display: block;
    width: 100%;
    height: 200px;
}

/* Legend */
.sparkline-legend {
    display: flex;
    justify-content: center;
    gap: var(--wa-space-l);
    margin-top: var(--wa-space-s);
}

.sparkline-legend-item {
    display: flex;
    align-items: center;
    gap: var(--wa-space-xs);
}

.sparkline-legend-swatch {
    display: inline-block;
    width: 12px;
    height: 3px;
    border-radius: 1px;
}

.sparkline-legend-swatch--green {
    background: var(--sparkline-project-gradient-color-3);
}

.sparkline-legend-swatch--blue {
    background: var(--sparkline-blue-gradient-color-3);
}

.sparkline-legend-swatch--red {
    background: var(--sparkline-red-gradient-color-3);
}

.sparkline-legend-text {
    font-size: var(--wa-font-size-xs);
    color: var(--wa-color-text-quiet);
}

.no-data {
    font-size: var(--wa-font-size-s);
    color: var(--wa-color-text-quiet);
    text-align: center;
    padding: var(--wa-space-m);
}
</style>
