<script setup>
// ActivitySparkline.vue - GitHub-style sparkline graph showing weekly activity
import { computed } from 'vue'

const props = defineProps({
    /** Unique suffix for SVG IDs (prevents collisions when multiple sparklines on page) */
    idSuffix: {
        type: String,
        default: 'global',
    },
    /** Array of { date, user_message_count } objects to render */
    data: {
        type: Array,
        default: () => [],
    },
})

const SVG_HEIGHT = 30
const GRAPH_HEIGHT = 28
const MIN_Y = 1.0

// SVG width adapts to number of weeks: n * 3 - 1 (e.g. 52 weeks = 155px)
const svgWidth = computed(() => props.data.length * 3 - 1)

const gradientId = computed(() => `sparkline-${props.idSuffix}-gradient`)
const maskId = computed(() => `sparkline-${props.idSuffix}-graph`)

const polylinePoints = computed(() => {
    if (!props.data.length) return ''

    const counts = props.data.map((w) => w.user_message_count)
    const maxCount = Math.max(...counts)

    const xStep = svgWidth.value / (counts.length - 1)

    return counts
        .map((count, i) => {
            const x = Math.round(i * xStep * 100) / 100
            // Scale: 0 -> MIN_Y, maxCount -> GRAPH_HEIGHT + 1
            const y = maxCount > 0 ? MIN_Y + (count / maxCount) * (GRAPH_HEIGHT - MIN_Y) : MIN_Y
            return `${x},${Math.round(y * 100) / 100}`
        })
        .join(' ')
})
</script>

<template>
    <svg v-if="data.length" :width="svgWidth" aria-hidden="true" :height="SVG_HEIGHT" class="activity-sparkline">
        <defs>
            <linearGradient :id="gradientId" x1="0" x2="0" y1="1" y2="0">
                <stop offset="0%" stop-color="var(--sparkline-project-gradient-color-1)"></stop>
                <stop offset="10%" stop-color="var(--sparkline-project-gradient-color-2)"></stop>
                <stop offset="25%" stop-color="var(--sparkline-project-gradient-color-3)"></stop>
                <stop offset="50%" stop-color="var(--sparkline-project-gradient-color-4)"></stop>
            </linearGradient>
            <mask :id="maskId" x="0" y="0" :width="svgWidth" :height="GRAPH_HEIGHT">
                <polyline
                    :transform="`translate(0, ${GRAPH_HEIGHT}) scale(1,-1)`"
                    :points="polylinePoints"
                    fill="transparent"
                    stroke="var(--sparkline-project-stroke-color)"
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
                :style="`stroke: none; fill: url(#${gradientId}); mask: url(#${maskId});`"
            ></rect>
        </g>
    </svg>
</template>

<style scoped>
.activity-sparkline {
    display: block;
}
</style>
