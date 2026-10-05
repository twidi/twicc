<script setup>
/**
 * QuotaTooltipContent - body of the 5h / 7d quota tooltips (the glass surface itself comes
 * from AppTooltip). Verdict first (on track / quota ends at HH:MM), then the usage and time
 * lanes, the pace figures, the cost estimates and the action buttons.
 */
import { computed } from 'vue'
import { formatRecentDelta, formatQuotaMoment, formatResetTimePrecise } from '../../utils/usage'
import CostDisplay from '../ui/CostDisplay.vue'
import ProviderIcon from '../ui/ProviderIcon.vue'

const props = defineProps({
    /** Computed quota (computeUsageData): utilization, timePct, burnRate, resetsAt, recentLong/Short */
    quota: { type: Object, required: true },
    /** Computed cost block of the same period, or null */
    cost: { type: Object, default: null },
    /** CSS color of the severity (ring color) */
    color: { type: String, required: true },
    providerLabel: { type: String, default: '' },
    provider: { type: String, default: null },
    canCycleProvider: { type: Boolean, default: false },
    canCyclePeriod: { type: Boolean, default: false },
    /** Long period name for the header, e.g. "5 hours" */
    periodLabel: { type: String, required: true },
    /** Short period name for the estimate label, e.g. "5h" */
    periodShort: { type: String, required: true },
    /** Round the "last X" windows to the hour (7d) instead of 10 minutes (5h) */
    roundToHour: { type: Boolean, default: false },
    showCosts: { type: Boolean, default: false },
    externalLink: { type: Object, default: null },
})

const emit = defineEmits(['open-graph', 'cycle-provider', 'cycle-period'])

const exhausted = computed(() => (props.quota.utilization ?? 0) >= 100)
const cutoffAt = computed(() => props.cost?.cutoffAt ?? null)
const hasReset = computed(() => !!props.quota.resetsAt)
const burnRate = computed(() => props.quota.burnRate)
// "at 13:55 on Saturday" / "at 13:55" / "on Saturday 12 October" — for sentences.
function sentenceMoment(when) {
    const { time, day } = formatQuotaMoment(when)
    return [time && `at ${time}`, day && `on ${day}`].filter(Boolean).join(' ')
}
// "Saturday 13:55" / "13:55" / "Saturday 12 October" — compact, for the header.
function compactMoment(when) {
    const { time, day } = formatQuotaMoment(when)
    return [day, time].filter(Boolean).join(' ')
}

// Same wording as the sidebar: "1 h 13 min", "45 min", "2 d 3 h".
function formatSpan(ms) {
    const minutes = Math.max(1, Math.round(ms / 60000))
    if (minutes < 60) return `${minutes} min`
    const hours = Math.floor(minutes / 60)
    if (hours < 48) {
        const rest = minutes % 60
        return rest ? `${hours} h ${String(rest).padStart(2, '0')} min` : `${hours} h`
    }
    const days = Math.floor(hours / 24)
    const restHours = hours % 24
    return restHours ? `${days} d ${restHours} h` : `${days} d`
}

const verdict = computed(() => {
    if (!hasReset.value) {
        return { icon: 'circle-info', title: 'Period not started yet', sub: 'No usage recorded in this period.' }
    }
    if (exhausted.value) {
        return { icon: 'triangle-exclamation', title: 'Quota exhausted', sub: `Back ${sentenceMoment(props.quota.resetsAt)}` }
    }
    if (cutoffAt.value) {
        const gapMs = new Date(props.quota.resetsAt) - new Date(cutoffAt.value)
        return {
            icon: 'triangle-exclamation',
            title: `Quota runs out ${sentenceMoment(cutoffAt.value)}`,
            sub: gapMs > 0 ? `Then ${formatSpan(gapMs)} without quota, until the reset` : 'At the current pace',
        }
    }
    const left = `Up to ${Math.max(0, 100 - (props.quota.utilization ?? 0)).toFixed(0)}% of quota left to use in ${formatSpan(new Date(props.quota.resetsAt) - Date.now())}`
    if (burnRate.value != null && burnRate.value >= 1) {
        return { icon: 'circle-check', title: 'Right on the limit', sub: left }
    }
    return { icon: 'circle-check', title: 'On track', sub: left }
})

// Pace figures: burn rate + the recent windows that carry a real measure (not a fallback).
const paceItems = computed(() => {
    const items = []
    if (burnRate.value != null) {
        items.push({ key: 'burn', label: 'Burn rate', value: `${(burnRate.value * 100).toFixed(0)}%`, hot: burnRate.value >= 1 })
    }
    const long = props.quota.recentLong
    const short = props.quota.recentShort
    const longLabel = long?.rate != null && !long.isFallback ? formatRecentDelta(long.deltaMs, props.roundToHour) : null
    if (longLabel) items.push({ key: 'long', label: `Last ${longLabel}`, value: `${(long.rate * 100).toFixed(0)}%` })
    const shortLabel = short?.rate != null && !short.isFallback ? formatRecentDelta(short.deltaMs, props.roundToHour) : null
    if (shortLabel && shortLabel !== longLabel) items.push({ key: 'short', label: `Last ${shortLabel}`, value: `${(short.rate * 100).toFixed(0)}%` })
    return items
})

const costItems = computed(() => {
    const cost = props.cost
    if (!props.showCosts || !cost || cost.spent == null) return []
    const items = [{ key: 'spent', label: 'Spent', value: cost.spent }]
    if (cost.estimatedPeriod != null) items.push({ key: 'period', label: `Est. ${props.periodShort}`, value: cost.estimatedPeriod })
    if (cost.estimatedMonthly != null) items.push({ key: 'month', label: 'Est. 30 days', value: cost.estimatedMonthly })
    return items
})

// White reads on the red and green fills; only the light warning yellow needs dark text.
const isWarning = computed(() => props.color === 'var(--wa-color-warning)')
const usageFigure = computed(() => `${Math.round(props.quota.utilization ?? 0)}%`)
const timeFigure = computed(() => `${Math.round(props.quota.timePct ?? 0)}%`)
const usageWidth = computed(() => `${Math.min(props.quota.utilization ?? 0, 100)}%`)
const timeWidth = computed(() => `${Math.max(0, Math.min(props.quota.timePct ?? 0, 100))}%`)
</script>

<template>
    <div class="quota-tip" :style="{ '--quota-color': color }">
        <div class="quota-tip-head">
            <div class="quota-tip-title">
                <button
                    type="button"
                    class="quota-tip-control"
                    :disabled="!canCycleProvider"
                    :title="canCycleProvider ? 'Click to switch to the next provider' : undefined"
                    @click="emit('cycle-provider')"
                >
                    <ProviderIcon class="quota-tip-provider-icon" :provider="provider" />
                    <span class="quota-tip-provider-name">{{ providerLabel }}</span>
                    <wa-icon v-if="canCycleProvider" class="quota-tip-switch" name="repeat"></wa-icon>
                </button>
                <span class="quota-tip-period">·</span>
                <button
                    type="button"
                    class="quota-tip-control quota-tip-period"
                    :disabled="!canCyclePeriod"
                    :title="canCyclePeriod ? 'Click to switch between 5 hours and 7 days' : undefined"
                    @click="emit('cycle-period')"
                >
                    <span>{{ periodLabel }}</span>
                    <wa-icon v-if="canCyclePeriod" class="quota-tip-switch" name="repeat"></wa-icon>
                </button>
            </div>
            <span v-if="hasReset" class="quota-tip-reset" :title="formatResetTimePrecise(quota.resetsAt)">Resets {{ compactMoment(quota.resetsAt) }}</span>
        </div>

        <div class="quota-tip-verdict">
            <span class="quota-tip-verdict-icon"><wa-icon :name="verdict.icon"></wa-icon></span>
            <div class="quota-tip-verdict-text">
                <strong>{{ verdict.title }}</strong>
                <span>{{ verdict.sub }}</span>
            </div>
        </div>

        <div class="quota-tip-bars">
            <span class="quota-tip-bar-label">Usage</span>
            <div class="quota-tip-lane">
                <div class="quota-tip-fill" :style="{ width: usageWidth }"><span class="quota-tip-figure" :class="{ 'quota-tip-figure-dark': isWarning }">{{ usageFigure }}</span></div>
            </div>
            <template v-if="quota.timePct != null">
                <span class="quota-tip-bar-label">Time</span>
                <div class="quota-tip-lane">
                    <div class="quota-tip-time" :style="{ width: timeWidth }"><span class="quota-tip-figure quota-tip-figure-time">{{ timeFigure }}</span></div>
                    <template v-if="cost && cost.cutoffPct != null">
                        <div class="quota-tip-hatch" :style="{ left: cost.cutoffPct + '%' }"></div>
                        <div class="quota-tip-tick" :style="{ left: cost.cutoffPct + '%' }"></div>
                    </template>
                </div>
            </template>
        </div>

        <div v-if="paceItems.length" class="quota-tip-stats">
            <div v-for="item in paceItems" :key="item.key" class="quota-tip-stat">
                <small>{{ item.label }}</small>
                <strong :class="{ 'quota-tip-hot': item.hot }">{{ item.value }}</strong>
            </div>
        </div>

        <div v-if="costItems.length" class="quota-tip-costs">
            <div class="quota-tip-stats">
                <div v-for="item in costItems" :key="item.key" class="quota-tip-stat">
                    <small>{{ item.label }}</small>
                    <strong><CostDisplay :cost="item.value" /></strong>
                </div>
            </div>
            <div v-if="cost.capped" class="quota-tip-note">
                <wa-icon name="triangle-exclamation"></wa-icon>
                <span>Capped: burn rate is above 100%. The 30-day estimate uses the capped value.</span>
            </div>
        </div>

        <div class="quota-tip-buttons">
            <wa-button v-if="externalLink" size="small" variant="brand" appearance="outlined" :href="externalLink.url" target="_blank" rel="noopener"><wa-icon slot="start" name="up-right-from-square"></wa-icon>{{ externalLink.label }}</wa-button>
            <wa-button size="small" variant="brand" appearance="outlined" @click="emit('open-graph')"><wa-icon slot="start" name="chart-line"></wa-icon>View graph</wa-button>
        </div>
    </div>
</template>

<style scoped>
.quota-tip {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-s);
    min-width: 17rem;
    font-variant-numeric: tabular-nums;
}

.quota-tip-head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: var(--wa-space-m);
    white-space: nowrap;
}
.quota-tip-title {
    display: flex;
    align-items: center;
    gap: var(--wa-space-2xs);
    font-weight: var(--wa-font-weight-bold);
}
.quota-tip-control {
    display: inline-flex;
    align-items: center;
    gap: var(--wa-space-2xs);
    padding: var(--wa-space-3xs) var(--wa-space-2xs);
    border: 0;
    border-radius: var(--wa-border-radius-m);
    background: transparent;
    color: inherit;
    font: inherit;
}
/* Native button styles add icon margins; the widget uses only the flex gap. */
.quota-tip-control > wa-icon {
    margin-inline: 0;
}
.quota-tip-control:not(:disabled) {
    cursor: pointer;
    transition: background 0.12s;
}
.quota-tip-control:not(:disabled):hover {
    background: var(--wa-color-neutral-fill-quiet);
}
.quota-tip-control:focus-visible {
    outline: 2px solid var(--wa-color-brand);
    outline-offset: 2px;
}
.quota-tip-provider-icon {
    font-size: var(--wa-font-size-l);
    color: var(--wa-color-neutral-content);
    flex: 0 0 auto;
}
.quota-tip-provider-name {
    font-size: var(--wa-font-size-xs);
    font-weight: var(--wa-font-weight-semibold);
    color: var(--wa-color-neutral-content);
}
.quota-tip-switch {
    font-size: var(--wa-font-size-s);
    color: var(--wa-color-neutral-muted);
}
.quota-tip-control:hover .quota-tip-switch {
    color: var(--wa-color-neutral-content);
}
.quota-tip-period {
    font-weight: var(--wa-font-weight-normal);
    color: var(--wa-color-neutral-muted);
}
.quota-tip-reset {
    font-size: var(--wa-font-size-xs);
    color: var(--wa-color-neutral-muted);
}

.quota-tip-verdict {
    display: flex;
    align-items: center;
    gap: var(--wa-space-s);
}
.quota-tip-verdict-icon {
    flex: 0 0 auto;
    display: grid;
    place-items: center;
    width: 2.25rem;
    height: 2.25rem;
    border-radius: var(--wa-border-radius-l);
    font-size: var(--wa-font-size-l);
    color: #fff;
    background: var(--quota-color);
}
.quota-tip-verdict-text {
    display: flex;
    flex-direction: column;
    min-width: 0;
}
.quota-tip-verdict-text strong {
    font-size: var(--wa-font-size-m);
    line-height: 1.2;
}
.quota-tip-verdict-text span {
    font-size: var(--wa-font-size-xs);
    color: var(--wa-color-neutral-muted);
}

/* Lanes: same visual language as the sidebar footer bars. */
.quota-tip-bars {
    display: grid;
    grid-template-columns: auto 1fr;
    align-items: center;
    gap: var(--wa-space-2xs) var(--wa-space-s);
}
.quota-tip-bar-label {
    font-size: var(--wa-font-size-xs);
    color: var(--wa-color-neutral-muted);
}
.quota-tip-lane {
    position: relative;
    height: 18px;
    border-radius: var(--wa-border-radius-pill);
    background: var(--progress-track);
}
.quota-tip-figure {
    padding-inline: var(--wa-space-xs);
    font-size: var(--wa-font-size-xs);
    font-weight: var(--wa-font-weight-bold);
    color: #fff;
    text-shadow: 0 0 3px rgb(0 0 0 / 0.55), 0 1px 1px rgb(0 0 0 / 0.35);
}
.quota-tip-figure-dark {
    color: oklch(from var(--quota-color) 0.22 calc(c * 0.6) h);
    text-shadow: none;
}
.quota-tip-fill,
.quota-tip-time {
    position: absolute;
    inset: 0 auto 0 0;
    display: flex;
    align-items: center;
    justify-content: flex-end;
    /* Wide enough for "8%" at very low values. */
    min-width: 2.6rem;
    max-width: 100%;
    border-radius: var(--wa-border-radius-pill);
}
.quota-tip-fill {
    background: linear-gradient(90deg, oklch(from var(--quota-color) calc(l + 0.08) c h), var(--quota-color));
    box-shadow: 0 0 0.25rem color-mix(in oklab, var(--quota-color) 30%, transparent);
}
.quota-tip-time {
    background: var(--wa-color-neutral-border-loud);
}
.quota-tip-hatch {
    position: absolute;
    top: 0;
    bottom: 0;
    right: 0;
    border-radius: 0 var(--wa-border-radius-pill) var(--wa-border-radius-pill) 0;
    background: repeating-linear-gradient(135deg, var(--wa-color-danger) 0 3px, transparent 3px 7px);
    pointer-events: none;
}
.quota-tip-tick {
    position: absolute;
    top: -1px;
    bottom: -1px;
    width: 2px;
    border-radius: 1px;
    background: var(--wa-color-danger);
    transform: translateX(-50%);
    pointer-events: none;
}


/* Three-column figures; the pace row and the cost block share the grid so columns line up. */
.quota-tip-stats {
    display: grid;
    grid-auto-flow: column;
    grid-auto-columns: 1fr;
    gap: var(--wa-space-s);
    padding-inline: var(--wa-space-3xs);
}
.quota-tip-stat {
    display: flex;
    flex-direction: column;
    min-width: 0;
}
.quota-tip-stat small {
    font-size: var(--wa-font-size-2xs);
    text-transform: uppercase;
    letter-spacing: 0.05em;
    color: var(--wa-color-neutral-muted);
    white-space: nowrap;
}
.quota-tip-stat strong {
    font-size: var(--wa-font-size-m);
    white-space: nowrap;
}
.quota-tip-hot {
    color: var(--wa-color-danger);
}

.quota-tip-costs {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-xs);
    padding: var(--wa-space-xs) var(--wa-space-s);
    border-radius: var(--wa-border-radius-l);
    /* Translucent tints, not an opaque fill: the glass surface stays visible through the block. */
    background: color-mix(in oklab, var(--wa-color-neutral-fill-loud) 9%, transparent);
    border: 1px solid color-mix(in oklab, var(--wa-color-neutral-fill-loud) 14%, transparent);
}
.quota-tip-costs .quota-tip-stats {
    padding-inline: 0;
}
.quota-tip-note {
    display: flex;
    align-items: flex-start;
    gap: var(--wa-space-2xs);
    font-size: var(--wa-font-size-2xs);
    line-height: 1.4;
    color: var(--wa-color-neutral-muted);
    white-space: normal;
}
/* The icon box is exactly one text line tall, centred on the first line. */
.quota-tip-note wa-icon {
    display: inline-flex;
    align-items: center;
    height: 1lh;
    color: var(--wa-color-warning);
    flex: 0 0 auto;
}

.quota-tip-buttons {
    display: flex;
    flex-wrap: wrap;
    gap: var(--wa-space-xs);
}
.quota-tip-buttons wa-button {
    flex: 1 1 auto;
}
</style>

<!-- Unscoped on purpose: Vue's scoped compiler drops the descendant part of a
     `:global(html.wa-dark) .x` selector, leaving a bare `html.wa-dark` rule. -->
<style>
html.wa-dark .quota-tip-time {
    background: var(--wa-color-neutral-fill-loud);
}
/* The dark-mode time fill is a light grey: its figure turns dark. */
html.wa-dark .quota-tip-figure-time {
    color: var(--wa-color-neutral-on-loud, #10171d);
    text-shadow: none;
}
</style>
