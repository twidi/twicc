<script setup>
// AnimatedNumber.vue - A number that counts up when its screen opens (visual refresh step 7a,
// docs/plans/2026-09-30-stats-motion-design.md §4).
// The count starts on the `animationstart` of the root's CSS fade, which restarts every time
// the element goes from not rendered to rendered (tab shown again, KeepAlive re-attach...):
// from 0 to the value, 1000ms. A value change after that counts from the displayed value to
// the new one, 600ms. Under reduced motion, or before any fade started, the value shows as is.

import { ref, watch, onBeforeUnmount, onDeactivated } from 'vue'
import CostDisplay from './CostDisplay.vue'
import { tweenValue, formatAvg } from '../../utils/countUp.js'

const props = defineProps({
    /** The number to show (null shows a dash) */
    value: {
        type: Number,
        default: null,
    },
    /** How to render it: 'integer' (rounded), 'average' (one decimal) or 'cost' (CostDisplay) */
    format: {
        type: String,
        default: 'integer',
        validator: v => ['integer', 'average', 'cost'].includes(v),
    },
})

const ENTRANCE_DURATION = 1000
const UPDATE_DURATION = 600

const displayed = ref(props.value)
let frame = null
let hasCounted = false

const isNumber = (v) => typeof v === 'number' && Number.isFinite(v)
const motionAllowed = () => !window.matchMedia('(prefers-reduced-motion: reduce)').matches

function cancelTween() {
    if (frame !== null) {
        cancelAnimationFrame(frame)
        frame = null
    }
}

/** Tween `displayed` from `from` to the current value over `duration` ms. */
function startTween(from, duration) {
    cancelTween()
    const start = performance.now()
    const step = (now) => {
        const k = Math.min(1, (now - start) / duration)
        displayed.value = tweenValue(from, props.value, k)
        frame = k < 1 ? requestAnimationFrame(step) : null
    }
    frame = requestAnimationFrame(step)
}

/** Stop any count and show the current value. */
function settle() {
    cancelTween()
    displayed.value = props.value
}

function onAnimationStart(event) {
    if (event.target !== event.currentTarget || !event.animationName.startsWith('animated-number-in')) return
    if (motionAllowed() && isNumber(props.value)) {
        hasCounted = true
        startTween(0, ENTRANCE_DURATION)
    } else {
        settle()
    }
}

watch(() => props.value, (value) => {
    if (hasCounted && motionAllowed() && isNumber(displayed.value) && isNumber(value)) {
        startTween(displayed.value, UPDATE_DURATION)
    } else {
        settle()
    }
})

onDeactivated(settle)
onBeforeUnmount(settle)

function formatInteger(value) {
    return isNumber(value) ? String(Math.round(value)) : '-'
}
</script>

<template>
    <span class="animated-number" @animationstart="onAnimationStart">
        <CostDisplay v-if="format === 'cost'" :cost="displayed" />
        <template v-else-if="format === 'average'">{{ formatAvg(displayed) }}</template>
        <template v-else>{{ formatInteger(displayed) }}</template>
    </span>
</template>

<style scoped>
/* Paints the first frame transparent, so the final value never flashes before the count. */
.animated-number {
    animation: animated-number-in var(--motion-dur-2) ease-in-out backwards;
}
@keyframes animated-number-in { from { opacity: 0; } }
</style>
