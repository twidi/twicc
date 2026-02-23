<script setup>
// ActivityDashboard.vue - KPI cards showing messages, cost and cost-per-message
// for daily/weekly/monthly periods, with trend badges comparing to the previous period.
// Pure presentation component: receives pre-fetched daily activity data via props.

import { computed } from 'vue'
import AppTooltip from './AppTooltip.vue'
import CostDisplay from './CostDisplay.vue'

const props = defineProps({
    /** Daily activity data: array of { date: "YYYY-MM-DD", user_message_count: N, cost: "X.XX" } */
    dailyActivity: {
        type: Array,
        required: true,
    },
})

/**
 * Aggregate daily activity data for a date range [startDate, endDate).
 * Returns { userMessageCount, cost } totals.
 */
function aggregatePeriod(data, startDate, endDate) {
    let userMessageCount = 0
    let cost = 0
    for (const d of data) {
        if (d.date >= startDate && d.date < endDate) {
            userMessageCount += d.user_message_count || 0
            cost += parseFloat(d.cost) || 0
        }
    }
    return { userMessageCount, cost }
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

const periods = computed(() => {
    const data = props.dailyActivity
    if (!data.length) return []

    const today = new Date()
    today.setHours(0, 0, 0, 0)
    const tomorrow = addDays(today, 1)

    const defs = [
        {
            key: 'daily',
            label: 'Daily',
            icon: 'calendar-day',
            previousLabel: 'yesterday',
            current: [today, tomorrow],
            previous: [addDays(today, -1), today],
        },
        {
            key: 'weekly',
            label: 'Weekly',
            sublabel: 'rolling 7 days',
            icon: 'calendar-week',
            previousLabel: 'the previous 7 days period',
            current: [addDays(today, -6), tomorrow],
            previous: [addDays(today, -13), addDays(today, -6)],
        },
        {
            key: 'monthly',
            label: 'Monthly',
            sublabel: 'rolling 30 days',
            icon: 'calendar',
            previousLabel: 'the previous 30 days period',
            current: [addDays(today, -29), tomorrow],
            previous: [addDays(today, -59), addDays(today, -29)],
        },
    ]

    return defs.map(({ key, label, sublabel, icon, previousLabel, current, previous }) => {
        const cur = aggregatePeriod(data, toDateStr(current[0]), toDateStr(current[1]))
        const prev = aggregatePeriod(data, toDateStr(previous[0]), toDateStr(previous[1]))

        const curCpm = cur.userMessageCount > 0 ? cur.cost / cur.userMessageCount : null
        const prevCpm = prev.userMessageCount > 0 ? prev.cost / prev.userMessageCount : null

        return {
            key,
            label,
            sublabel,
            icon,
            previousLabel,
            messages: cur.userMessageCount,
            prevMessages: prev.userMessageCount,
            messagesTrend: computeTrend(cur.userMessageCount, prev.userMessageCount, true),
            cost: cur.cost,
            prevCost: prev.cost,
            costTrend: computeTrend(cur.cost, prev.cost, false),
            cpm: curCpm,
            prevCpm,
            cpmTrend: computeTrend(curCpm, prevCpm, false),
        }
    })
})
</script>

<template>
    <div v-if="periods.length > 0" class="activity-dashboard">
        <h3 class="dashboard-title">Activity overview</h3>
        <div class="wa-grid dashboard-grid">
            <wa-card v-for="period in periods" :key="period.label">
                <div class="wa-flank wa-align-items-start">
                    <wa-avatar shape="rounded">
                        <wa-icon slot="icon" :name="period.icon"></wa-icon>
                    </wa-avatar>
                    <div class="wa-stack wa-gap-m">
                        <h3 class="wa-caption-m">{{ period.label }} <span v-if="period.sublabel" class="wa-caption-2xs period-sublabel">({{ period.sublabel }})</span></h3>

                        <!-- Main metric: message count -->
                        <div class="wa-stack wa-gap-2xs">
                            <div class="wa-cluster wa-gap-xs">
                                <span v-if="period.messagesTrend" class="wa-caption-2xs prev-value">{{ period.prevMessages }} →</span>
                                <span class="wa-heading-xl">{{ period.messages }}</span>
                                <wa-tag
                                    v-if="period.messagesTrend"
                                    :variant="period.messagesTrend.variant"
                                    appearance="outlined"
                                >
                                    <wa-icon
                                        :name="period.messagesTrend.direction === 'up' ? 'arrow-up' : 'arrow-down'"
                                        :label="period.messagesTrend.direction === 'up' ? 'Up' : 'Down'"
                                    ></wa-icon>
                                    {{ period.messagesTrend.value }}%
                                </wa-tag>
                                <wa-tag v-else :id="`na-messages-${period.key}`" variant="neutral" appearance="outlined">N/A</wa-tag>
                                <AppTooltip v-if="!period.messagesTrend" :for="`na-messages-${period.key}`">No data for {{ period.previousLabel }}</AppTooltip>
                            </div>
                            <span class="wa-caption-s kpi-label">user messages</span>
                        </div>

                        <wa-divider></wa-divider>

                        <!-- Sub metrics: cost + cost per user message -->
                        <div class="wa-stack wa-gap-2xs">
                            <div class="wa-cluster wa-gap-xs sub-metric">
                                <CostDisplay v-if="period.costTrend" :cost="period.prevCost" class="wa-caption-2xs prev-value" />
                                <span v-if="period.costTrend" class="wa-caption-2xs prev-value">→</span>
                                <CostDisplay :cost="period.cost" class="wa-heading-s" />
                                <wa-tag
                                    v-if="period.costTrend"
                                    :variant="period.costTrend.variant"
                                    appearance="outlined"
                                    size="small"
                                >
                                    <wa-icon
                                        :name="period.costTrend.direction === 'up' ? 'arrow-up' : 'arrow-down'"
                                        :label="period.costTrend.direction === 'up' ? 'Up' : 'Down'"
                                    ></wa-icon>
                                    {{ period.costTrend.value }}%
                                </wa-tag>
                                <wa-tag v-else :id="`na-cost-${period.key}`" variant="neutral" appearance="outlined" size="small">N/A</wa-tag>
                                <AppTooltip v-if="!period.costTrend" :for="`na-cost-${period.key}`">No data for {{ period.previousLabel }}</AppTooltip>
                            </div>
                            <span class="wa-caption-xs kpi-label">total cost</span>
                        </div>

                        <div class="wa-stack wa-gap-2xs">
                            <div class="wa-cluster wa-gap-xs sub-metric">
                                <CostDisplay v-if="period.cpmTrend" :cost="period.prevCpm" class="wa-caption-2xs prev-value" />
                                <span v-if="period.cpmTrend" class="wa-caption-2xs prev-value">→</span>
                                <CostDisplay :cost="period.cpm" class="wa-heading-s" />
                                <wa-tag
                                    v-if="period.cpmTrend"
                                    :variant="period.cpmTrend.variant"
                                    appearance="outlined"
                                    size="small"
                                >
                                    <wa-icon
                                        :name="period.cpmTrend.direction === 'up' ? 'arrow-up' : 'arrow-down'"
                                        :label="period.cpmTrend.direction === 'up' ? 'Up' : 'Down'"
                                    ></wa-icon>
                                    {{ period.cpmTrend.value }}%
                                </wa-tag>
                                <wa-tag v-else :id="`na-cpm-${period.key}`" variant="neutral" appearance="outlined" size="small">N/A</wa-tag>
                                <AppTooltip v-if="!period.cpmTrend" :for="`na-cpm-${period.key}`">No data for {{ period.previousLabel }}</AppTooltip>
                            </div>
                            <span class="wa-caption-xs kpi-label">cost per user message</span>
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
}

.dashboard-title {
    margin-block: var(--wa-space-l);
    color: var(--wa-color-text-quiet);
    font-size: var(--wa-font-size-l);
    text-align: center;
}

.dashboard-grid {
    --min-column-size: 22ch;
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
    font-size: var(--wa-font-size-xs);
}
.sub-metric wa-tag {
    font-size: var(--wa-font-size-2xs);
}

</style>
