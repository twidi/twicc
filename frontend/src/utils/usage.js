// frontend/src/utils/usage.js

/**
 * Usage quota computation utilities.
 *
 * Levels for each quota type:
 *   'inactive'  — null or 0% utilization (not used in this window)
 *   'normal'    — usage is progressing but burn rate is sustainable
 *   'warning'   — burning too fast, will hit limit before reset at this pace
 *   'critical'  — at or above 100% utilization, quota exhausted
 */

/**
 * Quota level constants.
 */
export const USAGE_LEVELS = {
    INACTIVE: 'inactive',
    NORMAL: 'normal',
    WARNING: 'warning',
    CRITICAL: 'critical',
}

/**
 * Burn rate thresholds for ring color.
 *   < 0.9  → green (comfortable pace)
 *   < 1.15 → orange (slightly above sustainable)
 *   >= 1.15 → red (will exceed quota well before reset)
 *
 * Always red when utilization >= 100% regardless of burn rate.
 */
const BURN_RATE_GREEN_MAX = 0.9
const BURN_RATE_ORANGE_MAX = 1.15

/**
 * Get the CSS color for a usage ring based on burn rate and utilization.
 *
 * @param {object} quota - A computed quota object from computeUsageData()
 * @returns {string} CSS variable reference for the ring color
 */
export function getUsageRingColor(quota) {
    if (!quota || quota.utilization == null) return 'var(--wa-color-neutral)'
    if (quota.utilization >= 100) return 'var(--wa-color-danger)'
    if (quota.burnRate == null) return 'var(--wa-color-success)'
    if (quota.burnRate < BURN_RATE_GREEN_MAX) return 'var(--wa-color-success)'
    if (quota.burnRate < BURN_RATE_ORANGE_MAX) return 'var(--wa-color-warning)'
    return 'var(--wa-color-danger)'
}

/**
 * Window durations in milliseconds.
 */
const FIVE_HOURS_MS = 5 * 60 * 60 * 1000
const SEVEN_DAYS_MS = 7 * 24 * 60 * 60 * 1000

/**
 * Calculate how far through a time window we are, as a percentage (0–100).
 *
 * @param {string} fetchedAt - ISO datetime string of when data was fetched
 * @param {string} resetsAt - ISO datetime string of when the window resets
 * @param {number} windowMs - Window duration in milliseconds
 * @returns {number} Percentage elapsed (clamped 0–100)
 */
function temporalPct(fetchedAt, resetsAt, windowMs) {
    const fetched = new Date(fetchedAt).getTime()
    const reset = new Date(resetsAt).getTime()
    const start = reset - windowMs
    const elapsed = fetched - start
    if (windowMs <= 0) return 0
    return Math.max(0, Math.min(100, (elapsed / windowMs) * 100))
}

/**
 * Calculate burn rate: ratio of utilization to temporal progress.
 * > 1.0 means on track to exhaust quota before reset.
 *
 * @param {number|null} utilization - Usage percentage (0–100)
 * @param {number|null} timePct - Temporal percentage (0–100)
 * @returns {number|null} Burn rate, or null if not computable
 */
function burnRate(utilization, timePct) {
    if (utilization == null || timePct == null || timePct <= 0) return null
    return utilization / timePct
}

/**
 * Determine the level for a quota based on utilization and burn rate.
 *
 * @param {number|null} utilization - Usage percentage (0–100)
 * @param {number|null} rate - Burn rate (utilization / temporal %)
 * @returns {string} One of USAGE_LEVELS values
 */
function computeLevel(utilization, rate) {
    if (utilization == null || utilization <= 0) return USAGE_LEVELS.INACTIVE
    if (utilization >= 100) return USAGE_LEVELS.CRITICAL
    if (rate != null && rate > 1.0) return USAGE_LEVELS.WARNING
    return USAGE_LEVELS.NORMAL
}

/**
 * Compute derived data for a single quota block.
 *
 * @param {number|null} utilization - Usage percentage from API
 * @param {string|null} resetsAt - ISO datetime of reset
 * @param {string} fetchedAt - ISO datetime of fetch
 * @param {number} windowMs - Window duration in ms
 * @returns {object} Computed quota info
 */
function computeQuota(utilization, resetsAt, fetchedAt, windowMs) {
    if (utilization == null || resetsAt == null) {
        return {
            utilization: null,
            resetsAt: null,
            timePct: null,
            burnRate: null,
            level: USAGE_LEVELS.INACTIVE,
        }
    }

    const timePct = temporalPct(fetchedAt, resetsAt, windowMs)
    const rate = burnRate(utilization, timePct)
    const level = computeLevel(utilization, rate)

    return {
        utilization,
        resetsAt,
        timePct,
        burnRate: rate,
        level,
    }
}

/**
 * Compute all derived usage data from a raw usage snapshot.
 *
 * @param {object|null} raw - Raw usage data from WebSocket (serialized UsageSnapshot)
 * @returns {object|null} Fully computed usage object, or null if no data
 */
export function computeUsageData(raw) {
    if (!raw) return null

    const fetchedAt = raw.fetched_at

    return {
        fetchedAt,

        fiveHour: computeQuota(
            raw.five_hour_utilization,
            raw.five_hour_resets_at,
            fetchedAt,
            FIVE_HOURS_MS,
        ),

        sevenDay: computeQuota(
            raw.seven_day_utilization,
            raw.seven_day_resets_at,
            fetchedAt,
            SEVEN_DAYS_MS,
        ),

        sevenDayOpus: computeQuota(
            raw.seven_day_opus_utilization,
            raw.seven_day_opus_resets_at,
            fetchedAt,
            SEVEN_DAYS_MS,
        ),

        sevenDaySonnet: computeQuota(
            raw.seven_day_sonnet_utilization,
            raw.seven_day_sonnet_resets_at,
            fetchedAt,
            SEVEN_DAYS_MS,
        ),

        sevenDayOauthApps: computeQuota(
            raw.seven_day_oauth_apps_utilization,
            raw.seven_day_oauth_apps_resets_at,
            fetchedAt,
            SEVEN_DAYS_MS,
        ),

        sevenDayCowork: computeQuota(
            raw.seven_day_cowork_utilization,
            raw.seven_day_cowork_resets_at,
            fetchedAt,
            SEVEN_DAYS_MS,
        ),

        extraUsage: {
            isEnabled: raw.extra_usage_is_enabled ?? false,
            monthlyLimit: raw.extra_usage_monthly_limit,
            usedCredits: raw.extra_usage_used_credits,
            utilization: raw.extra_usage_utilization,
        },
    }
}

/**
 * Format a computed usage object for console display.
 *
 * @param {boolean} hasOauth - Whether OAuth credentials are configured
 * @param {object|null} usage - Computed usage from computeUsageData()
 * @param {boolean} success - Whether the last fetch succeeded
 * @param {string} reason - "sync" (after API fetch) or "connection" (on WS connect)
 */
export function logUsageToConsole(hasOauth, usage, success, reason) {
    if (!hasOauth) {
        console.log('[Usage] OAuth not configured — usage quotas unavailable')
        return
    }

    if (!usage) {
        console.log('[Usage] OAuth configured but no data available yet (reason=%s)', reason)
        return
    }

    const formatQuota = (label, q) => {
        if (q.level === USAGE_LEVELS.INACTIVE) {
            return `  ${label}: inactive`
        }
        const burnStr = q.burnRate != null ? q.burnRate.toFixed(2) : 'n/a'
        const levelIcon = {
            [USAGE_LEVELS.NORMAL]: '\u2705',
            [USAGE_LEVELS.WARNING]: '\u26a0\ufe0f',
            [USAGE_LEVELS.CRITICAL]: '\ud83d\udd34',
        }[q.level] || ''
        return `  ${label}: ${levelIcon} ${q.utilization.toFixed(1)}% used, ${q.timePct.toFixed(1)}% time elapsed, burn=${burnStr} [${q.level}]`
    }

    const reasonLabel = reason === 'connection' ? 'cached' : (success ? 'OK' : 'FAILED')
    const lines = [
        `[Usage] Snapshot at ${usage.fetchedAt} (${reasonLabel})`,
        formatQuota('5-hour', usage.fiveHour),
        formatQuota('7-day', usage.sevenDay),
        formatQuota('7-day Opus', usage.sevenDayOpus),
        formatQuota('7-day Sonnet', usage.sevenDaySonnet),
        formatQuota('7-day OAuth', usage.sevenDayOauthApps),
        formatQuota('7-day Cowork', usage.sevenDayCowork),
    ]

    if (usage.extraUsage.isEnabled) {
        const eu = usage.extraUsage
        lines.push(`  Extra usage: enabled, ${eu.usedCredits ?? 0}/${eu.monthlyLimit ?? '?'} credits used`)
    } else {
        lines.push('  Extra usage: disabled')
    }

    console.log(lines.join('\n'))
}
