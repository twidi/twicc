<script setup>
// ActivityDashboard.vue - KPI cards for daily/weekly/monthly periods + all-time total,
// with trend badges comparing to the previous period (except total which has no comparison).
// Supports two modes:
//   - 'messages': main metric = user message count, sub-metrics = total cost + cost per message
//   - 'sessions': main metric = session count, sub-metrics = avg messages/session + avg cost/session
// Pure presentation component: receives pre-fetched daily activity data via props.

import { computed } from 'vue'
import AppTooltip from './AppTooltip.vue'
import CostDisplay from './CostDisplay.vue'

const props = defineProps({
    /** Daily activity data: array of { date: "YYYY-MM-DD", user_message_count: N, session_count: N, cost: "X.XX" } */
    dailyActivity: {
        type: Array,
        required: true,
    },
    /** All-time totals from API: { user_message_count: N, session_count: N, cost: "X.XX" } */
    totals: {
        type: Object,
        default: null,
    },
    /** Display mode: 'messages' or 'sessions' */
    mode: {
        type: String,
        default: 'messages',
        validator: v => ['messages', 'sessions'].includes(v),
    },
})

const isSessionsMode = computed(() => props.mode === 'sessions')

/**
 * Aggregate daily activity data for a date range [startDate, endDate).
 * Returns { userMessageCount, sessionCount, cost } totals.
 */
function aggregatePeriod(data, startDate, endDate) {
    let userMessageCount = 0
    let sessionCount = 0
    let cost = 0
    for (const d of data) {
        if (d.date >= startDate && d.date < endDate) {
            userMessageCount += d.user_message_count || 0
            sessionCount += d.session_count || 0
            cost += parseFloat(d.cost) || 0
        }
    }
    return { userMessageCount, sessionCount, cost }
}

/**
 * Format a date as YYYY-MM-DD string in local timezone (for comparison with API data).
 */
function toDateStr(date) {
    const y = date.getFullYear()
    const m = String(date.getMonth() + 1).padStart(2, '0')
    const d = String(date.getDate()).padStart(2, '0')
    return `${y}-${m}-${d}`
}

/**
 * Get a date offset by N days from the given date.
 */
function addDays(date, days) {
    const d = new Date(date)
    d.setDate(d.getDate() + days)
    return d
}

/**
 * Compute a trend badge object from current and previous values.
 * @param {number|null} cur - Current period value
 * @param {number|null} prev - Previous period value
 * @param {boolean} upIsGood - If true, increase = success; if false, increase = danger
 * @returns {object|null} { value, direction, variant } or null if N/A
 */
function computeTrend(cur, prev, upIsGood) {
    if (cur === null || prev === null || prev === 0) return null
    const pctChange = ((cur - prev) / prev) * 100
    const direction = pctChange >= 0 ? 'up' : 'down'
    const isPositive = pctChange >= 0
    return {
        value: Math.abs(pctChange).toFixed(1),
        direction,
        variant: (isPositive === upIsGood) ? 'success' : 'danger',
    }
}

/**
 * Format a decimal number for display (e.g. 12.3, 0.5).
 * Shows one decimal place, or integer if whole number.
 */
function formatAvg(value) {
    if (value === null) return '-'
    const rounded = Math.round(value * 10) / 10
    return rounded % 1 === 0 ? rounded.toString() : rounded.toFixed(1)
}

const periodDefs = [
    {
        key: 'daily',
        label: 'Daily',
        sublabel: 'current day',
        icon: 'calendar-day',
        previousLabel: 'yesterday',
        offsetCurrent: 0,
        offsetPrevious: -1,
        days: 1,
    },
    {
        key: 'weekly',
        label: 'Weekly',
        sublabel: 'rolling 7 days',
        icon: 'calendar-week',
        previousLabel: 'the previous 7 days period',
        offsetCurrent: -6,
        offsetPrevious: -13,
        days: 7,
    },
    {
        key: 'monthly',
        label: 'Monthly',
        sublabel: 'rolling 30 days',
        icon: 'calendar-days',
        previousLabel: 'the previous 30 days period',
        offsetCurrent: -29,
        offsetPrevious: -59,
        days: 30,
    },
]

const periods = computed(() => {
    const data = props.dailyActivity
    if (!data.length) return []

    const today = new Date()
    today.setHours(0, 0, 0, 0)
    const tomorrow = addDays(today, 1)

    const rollingPeriods = periodDefs.map(({ key, label, sublabel, icon, previousLabel, offsetCurrent, offsetPrevious, days }) => {
        const currentStart = addDays(today, offsetCurrent)
        const previousStart = addDays(today, offsetPrevious)
        const previousEnd = addDays(previousStart, days)

        const cur = aggregatePeriod(data, toDateStr(currentStart), toDateStr(tomorrow))
        const prev = aggregatePeriod(data, toDateStr(previousStart), toDateStr(previousEnd))

        // Main metric
        const mainValue = isSessionsMode.value ? cur.sessionCount : cur.userMessageCount
        const prevMainValue = isSessionsMode.value ? prev.sessionCount : prev.userMessageCount
        const mainTrend = computeTrend(mainValue, prevMainValue, true)

        // Sub metric 1
        let sub1Value, prevSub1Value, sub1Trend
        if (isSessionsMode.value) {
            // avg messages per session
            sub1Value = cur.sessionCount > 0 ? cur.userMessageCount / cur.sessionCount : null
            prevSub1Value = prev.sessionCount > 0 ? prev.userMessageCount / prev.sessionCount : null
            sub1Trend = computeTrend(sub1Value, prevSub1Value, true)
        } else {
            // total cost
            sub1Value = cur.cost
            prevSub1Value = prev.cost
            sub1Trend = computeTrend(sub1Value, prevSub1Value, false)
        }

        // Sub metric 2
        let sub2Value, prevSub2Value, sub2Trend
        if (isSessionsMode.value) {
            // avg cost per session
            sub2Value = cur.sessionCount > 0 ? cur.cost / cur.sessionCount : null
            prevSub2Value = prev.sessionCount > 0 ? prev.cost / prev.sessionCount : null
        } else {
            // cost per message
            sub2Value = cur.userMessageCount > 0 ? cur.cost / cur.userMessageCount : null
            prevSub2Value = prev.userMessageCount > 0 ? prev.cost / prev.userMessageCount : null
        }
        sub2Trend = computeTrend(sub2Value, prevSub2Value, false)

        return {
            key, label, sublabel, icon, previousLabel,
            mainValue, prevMainValue, mainTrend,
            sub1Value, prevSub1Value, sub1Trend,
            sub2Value, prevSub2Value, sub2Trend,
        }
    })

    // All-time total column (no trend, no previous value)
    if (props.totals) {
        const t = props.totals
        const userMessageCount = t.user_message_count || 0
        const sessionCount = t.session_count || 0
        const cost = parseFloat(t.cost) || 0

        const mainValue = isSessionsMode.value ? sessionCount : userMessageCount

        let sub1Value
        if (isSessionsMode.value) {
            sub1Value = sessionCount > 0 ? userMessageCount / sessionCount : null
        } else {
            sub1Value = cost
        }

        let sub2Value
        if (isSessionsMode.value) {
            sub2Value = sessionCount > 0 ? cost / sessionCount : null
        } else {
            sub2Value = userMessageCount > 0 ? cost / userMessageCount : null
        }

        rollingPeriods.push({
            key: 'total',
            label: 'Total',
            sublabel: 'all time',
            icon: 'calendar',
            isTotal: true,
            mainValue,
            sub1Value,
            sub2Value,
        })
    }

    return rollingPeriods
})
</script>

<template>
    <div v-if="periods.length > 0" class="activity-dashboard">
        <h3 class="dashboard-title">{{ isSessionsMode ? 'Sessions overview' : 'Message turns overview' }}</h3>
        <div class="wa-cluster dashboard-grid">
            <wa-card v-for="period in periods" :key="period.label">
                <div class="wa-flank wa-align-items-start">
                    <div class="wa-stack wa-gap-l">
                        <h3 class="wa-caption-m wa-cluster wa-gap-s">
                            <wa-icon :name="period.icon"></wa-icon>
                            <span class="wa-cluster wa-gap-s wa-stack-wrap">
                                {{ period.label }} <span v-if="period.sublabel" class="wa-caption-2xs period-sublabel">({{ period.sublabel }})</span>
                            </span>
                        </h3>

                        <!-- Main metric -->
                        <div class="wa-stack wa-gap-xs">
                            <div class="wa-cluster wa-gap-m">
                                <div class="wa-stack wa-gap-3xs wa-align-items-center">
                                    <span v-if="period.mainTrend && period.mainTrend.direction !== 'up'" class="wa-caption-xs prev-value">{{ period.prevMainValue }}</span>
                                    <span class="wa-heading-2xl">{{ period.mainValue }}</span>
                                    <span v-if="period.mainTrend && period.mainTrend.direction === 'up'" class="wa-caption-xs prev-value">{{ period.prevMainValue }}</span>
                                </div>
                                <wa-tag
                                    v-if="period.mainTrend"
                                    :variant="period.mainTrend.variant"
                                    appearance="outlined"
                                >
                                    <wa-icon
                                        :name="period.mainTrend.direction === 'up' ? 'arrow-up' : 'arrow-down'"
                                        :label="period.mainTrend.direction === 'up' ? 'Up' : 'Down'"
                                    ></wa-icon>
                                    {{ period.mainTrend.value }}%
                                </wa-tag>
                                <template v-if="!period.isTotal">
                                    <wa-tag v-if="!period.mainTrend" :id="`na-main-${mode}-${period.key}`" variant="neutral" appearance="outlined">N/A</wa-tag>
                                    <AppTooltip v-if="!period.mainTrend" :for="`na-main-${mode}-${period.key}`">No data for {{ period.previousLabel }}</AppTooltip>
                                </template>
                            </div>
                            <span class="wa-caption-s kpi-label">{{ isSessionsMode ? 'sessions created' : 'message turns' }}</span>
                        </div>

                        <wa-divider></wa-divider>

                        <!-- Sub metric 1 -->
                        <div class="wa-stack wa-gap-xs">
                            <div class="wa-cluster wa-gap-m sub-metric">
                                <!-- Cost display for messages mode, plain number for sessions mode -->
                                <div class="wa-stack wa-gap-2xs wa-align-items-center">
                                    <template v-if="isSessionsMode">
                                        <span v-if="period.sub1Trend && period.sub1Trend.direction !== 'up'" class="wa-caption-xs prev-value">{{ formatAvg(period.prevSub1Value) }}</span>
                                        <span class="wa-heading-l">{{ formatAvg(period.sub1Value) }}</span>
                                        <span v-if="period.sub1Trend && period.sub1Trend.direction === 'up'" class="wa-caption-xs prev-value">{{ formatAvg(period.prevSub1Value) }}</span>
                                    </template>
                                    <template v-else>
                                        <CostDisplay v-if="period.sub1Trend && period.sub1Trend.direction !== 'up'" :cost="period.prevSub1Value" :icon="false" class="wa-caption-s prev-value" />
                                        <CostDisplay :cost="period.sub1Value" class="wa-heading-l" />
                                        <CostDisplay v-if="period.sub1Trend && period.sub1Trend.direction === 'up'" :cost="period.prevSub1Value" :icon="false" class="wa-caption-xs prev-value" />
                                    </template>
                                </div>
                                <wa-tag
                                    v-if="period.sub1Trend"
                                    :variant="period.sub1Trend.variant"
                                    appearance="outlined"
                                    size="small"
                                >
                                    <wa-icon
                                        :name="period.sub1Trend.direction === 'up' ? 'arrow-up' : 'arrow-down'"
                                        :label="period.sub1Trend.direction === 'up' ? 'Up' : 'Down'"
                                    ></wa-icon>
                                    {{ period.sub1Trend.value }}%
                                </wa-tag>
                                <template v-if="!period.isTotal">
                                    <wa-tag v-if="!period.sub1Trend" :id="`na-sub1-${mode}-${period.key}`" variant="neutral" appearance="outlined" size="small">N/A</wa-tag>
                                    <AppTooltip v-if="!period.sub1Trend" :for="`na-sub1-${mode}-${period.key}`">No data for {{ period.previousLabel }}</AppTooltip>
                                </template>
                            </div>
                            <span class="wa-caption-xs kpi-label">{{ isSessionsMode ? 'avg. turns per session' : 'total cost' }}</span>
                        </div>

                        <!-- Sub metric 2 -->
                        <div class="wa-stack wa-gap-xs">
                            <div class="wa-cluster wa-gap-m sub-metric">
                                <div class="wa-stack wa-gap-2xs wa-align-items-center">
                                    <CostDisplay v-if="period.sub2Trend && period.sub2Trend.direction !== 'up'" :cost="period.prevSub2Value" :icon="false" class="wa-caption-xs prev-value" />
                                    <CostDisplay :cost="period.sub2Value" class="wa-heading-l" />
                                    <CostDisplay v-if="period.sub2Trend && period.sub2Trend.direction === 'up'" :cost="period.prevSub2Value" :icon="false" class="wa-caption-xs prev-value" />
                                </div>
                                <wa-tag
                                    v-if="period.sub2Trend"
                                    :variant="period.sub2Trend.variant"
                                    appearance="outlined"
                                    size="small"
                                >
                                    <wa-icon
                                        :name="period.sub2Trend.direction === 'up' ? 'arrow-up' : 'arrow-down'"
                                        :label="period.sub2Trend.direction === 'up' ? 'Up' : 'Down'"
                                    ></wa-icon>
                                    {{ period.sub2Trend.value }}%
                                </wa-tag>
                                <template v-if="!period.isTotal">
                                    <wa-tag v-if="!period.sub2Trend" :id="`na-sub2-${mode}-${period.key}`" variant="neutral" appearance="outlined" size="small">N/A</wa-tag>
                                    <AppTooltip v-if="!period.sub2Trend" :for="`na-sub2-${mode}-${period.key}`">No data for {{ period.previousLabel }}</AppTooltip>
                                </template>
                            </div>
                            <span class="wa-caption-xs kpi-label">{{ isSessionsMode ? 'avg. cost per session' : 'avg. cost per turn' }}</span>
                        </div>
                    </div>
                </div>
            </wa-card>
        </div>
    </div>
</template>

<style scoped>
.activity-dashboard {
    padding-inline: var(--wa-space-m);
    display: flex;
    flex-direction: column;
    align-items: stretch;
    max-width: 85rem;
    margin-inline: auto;
}

.dashboard-title {
    margin-block: var(--wa-space-l);
    color: var(--wa-color-text-quiet);
    font-size: var(--wa-font-size-l);
    text-align: center;
}

.dashboard-grid {
    justify-content: center;
    align-items: stretch;
}

h3 > span {
    row-gap: var(--wa-space-3xs);
}

wa-card {
    min-width: 16rem;
}

.kpi-label {
    color: var(--wa-color-text-quiet);
}

.period-sublabel {
    color: var(--wa-color-text-quiet);
}

.prev-value {
    color: var(--wa-color-text-quiet);
}

wa-tag {
    font-size: var(--wa-font-size-m);
}
.sub-metric wa-tag {
    font-size: var(--wa-font-size-s);
}

@container project-detail (width < 640px) {
    wa-card {
        --spacing: var(--wa-space-m);
        min-width: 13rem;
    }
}
@container project-detail (width < 550px) {
    .dashboard-grid {
        justify-content: center;
    }
    wa-card {
        max-width: 100%;
        min-width: 16rem;
    }
}

</style>
