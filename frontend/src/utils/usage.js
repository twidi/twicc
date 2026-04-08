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
 * Lookback durations in milliseconds for recent burn rate computation.
 */
const ONE_HOUR_MS = 60 * 60 * 1000
const ONE_DAY_MS = 24 * 60 * 60 * 1000
const THIRTY_MIN_MS = 30 * 60 * 1000
const TWELVE_HOURS_MS = 12 * 60 * 60 * 1000

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
 * Compute the recent burn rate over the delta between the current snapshot
 * and a historical reference snapshot.
 *
 * Unlike the regular burn rate (which averages from the start of the window),
 * this measures how fast the quota is being consumed *recently* — over roughly
 * the last lookback period (1h for 5h window, 24h for 7d window).
 *
 * Formula: (currentUtil - refUtil) / ((currentTime - refTime) / W × 100)
 *
 * @param {number|null} utilization - Current usage percentage (0–100)
 * @param {string} fetchedAt - ISO datetime of the current snapshot
 * @param {object|null} reference - Reference snapshot: { fetchedAt, utilization }
 * @param {number} windowMs - Total window duration in ms
 * @returns {number|null} Recent burn rate, or null if not computable
 */
function recentBurnRate(utilization, fetchedAt, reference, windowMs) {
    if (utilization == null || !reference || reference.utilization == null || !fetchedAt) return null

    const deltaUtilization = utilization - reference.utilization
    if (deltaUtilization < 0) return null  // a reset happened between snapshots

    const currentMs = new Date(fetchedAt).getTime()
    const refMs = new Date(reference.fetchedAt).getTime()
    const deltaMs = currentMs - refMs
    if (deltaMs <= 0) return null

    const deltaTimePct = (deltaMs / windowMs) * 100
    return burnRate(deltaUtilization, deltaTimePct)
}

/**
 * Compute derived data for a single quota block.
 *
 * @param {number|null} utilization - Usage percentage from API
 * @param {string|null} resetsAt - ISO datetime of reset
 * @param {string} fetchedAt - ISO datetime of fetch
 * @param {number} windowMs - Window duration in ms
 * @param {object|null} refLong - Reference snapshot for long recent rate: { fetchedAt, utilization }
 * @param {object|null} refShort - Reference snapshot for short recent rate: { fetchedAt, utilization }
 * @param {number} lookbackLongMs - Lookback duration for the long recent rate (ms)
 * @param {number} lookbackShortMs - Lookback duration for the short recent rate (ms)
 * @param {object|null} crossRefLong - Cross-period ref for long: { prevRef: {fetchedAt, utilization}, prevEnd: {fetchedAt, utilization} }
 * @param {object|null} crossRefShort - Cross-period ref for short
 * @returns {object} Computed quota info
 */
function computeQuota(utilization, resetsAt, fetchedAt, windowMs, refLong, refShort, lookbackLongMs, lookbackShortMs, crossRefLong = null, crossRefShort = null) {
    const emptyRecent = { rate: null, deltaMs: null, lookbackMs: null, isFallback: false }

    if (utilization == null) {
        return {
            utilization: null,
            resetsAt: null,
            timePct: null,
            burnRate: null,
            recentLong: emptyRecent,
            recentShort: emptyRecent,
            level: USAGE_LEVELS.INACTIVE,
        }
    }

    // utilization is a number but resetsAt may be null (period not started yet)
    if (resetsAt == null) {
        return {
            utilization,
            resetsAt: null,
            timePct: null,
            burnRate: null,
            recentLong: emptyRecent,
            recentShort: emptyRecent,
            level: computeLevel(utilization, null),
        }
    }

    const timePct = temporalPct(fetchedAt, resetsAt, windowMs)
    const rate = burnRate(utilization, timePct)
    const level = computeLevel(utilization, rate)

    // When the window is younger than the lookback interval, try cross-period
    // calculation using data from the previous period. If unavailable, return null.
    const elapsedMs = resetsAt
        ? new Date(fetchedAt).getTime() - (new Date(resetsAt).getTime() - windowMs)
        : null

    function _computeRecent(ref, lookbackMs, crossRef) {
        if (elapsedMs != null && elapsedMs < lookbackMs) {
            // Try cross-period calculation using data from the previous period
            if (crossRef && crossRef.prevRef && crossRef.prevEnd
                && crossRef.prevRef.utilization != null && crossRef.prevEnd.utilization != null) {
                const oldConsumption = crossRef.prevEnd.utilization - crossRef.prevRef.utilization
                if (oldConsumption >= 0) {
                    const totalConsumption = oldConsumption + utilization
                    const prevRefMs = new Date(crossRef.prevRef.fetchedAt).getTime()
                    const deltaMs = new Date(fetchedAt).getTime() - prevRefMs
                    if (deltaMs > 0) {
                        const deltaTimePct = (deltaMs / windowMs) * 100
                        const crossRate = burnRate(totalConsumption, deltaTimePct)
                        if (crossRate != null) {
                            return { rate: crossRate, deltaMs, lookbackMs, isFallback: false }
                        }
                    }
                }
            }
            // No cross-period data available — no meaningful recent rate
            return emptyRecent
        }
        const r = recentBurnRate(utilization, fetchedAt, ref, windowMs)
        const deltaMs = (r != null && ref)
            ? new Date(fetchedAt).getTime() - new Date(ref.fetchedAt).getTime()
            : null
        return { rate: r, deltaMs, lookbackMs, isFallback: false }
    }

    return {
        utilization,
        resetsAt,
        timePct,
        burnRate: rate,
        recentLong: _computeRecent(refLong, lookbackLongMs, crossRefLong),
        recentShort: _computeRecent(refShort, lookbackShortMs, crossRefShort),
        level,
    }
}

/**
 * Compute derived data for a period cost block.
 *
 * @param {object|null} raw - Raw period cost data
 * @returns {object} Period cost info
 */
function computePeriodCost(raw) {
    if (!raw) {
        return { spent: null, estimatedPeriod: null, estimatedMonthly: null, capped: false, cutoffAt: null }
    }
    return {
        spent: raw.spent ?? null,
        estimatedPeriod: raw.estimated_period ?? null,
        estimatedMonthly: raw.estimated_monthly ?? null,
        capped: raw.capped ?? false,
        cutoffAt: raw.cutoff_at ?? null,
    }
}

/**
 * Format a single duration in ms as a human-readable string.
 *
 * @param {number} ms - Duration in milliseconds (must be > 0)
 * @param {boolean} roundToHour - If true, round to nearest hour (for 7d window).
 *                                 If false, round to nearest 10 minutes (for 5h window).
 * @returns {string} Formatted duration, e.g. "20h", "50min", "1h"
 */
function formatDuration(ms, roundToHour) {
    if (roundToHour) {
        const hours = Math.round(ms / (60 * 60 * 1000))
        return `${hours}h`
    }

    // Round to nearest 10 minutes
    const totalMinutes = Math.round(ms / (10 * 60 * 1000)) * 10
    if (totalMinutes < 60) return `${totalMinutes}min`
    const hours = Math.floor(totalMinutes / 60)
    const mins = totalMinutes % 60
    return mins > 0 ? `${hours}h${String(mins).padStart(2, '0')}` : `${hours}h`
}

/**
 * Format a recent burn rate time delta for display in tooltips.
 * When maxMs is provided and the formatted delta differs from the formatted max,
 * shows "delta/max" (e.g. "2h/24h", "10min/1h").
 *
 * @param {number|null} deltaMs - Time span in milliseconds
 * @param {boolean} roundToHour - If true, round to nearest hour (for 7d window).
 *                                 If false, round to nearest 10 minutes (for 5h window).
 * @param {number|null} [maxMs] - Maximum lookback duration in milliseconds
 * @returns {string|null} Formatted duration, e.g. "2h/24h", "50min", "1h", or null
 */
export function formatRecentDelta(deltaMs, roundToHour, maxMs) {
    if (deltaMs == null || deltaMs <= 0) return null

    const deltaStr = formatDuration(deltaMs, roundToHour)

    if (maxMs != null && maxMs > 0) {
        const maxStr = formatDuration(maxMs, roundToHour)
        if (deltaStr !== maxStr) {
            return `${deltaStr}/${maxStr}`
        }
    }

    return deltaStr
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
    const periodCosts = raw.period_costs || {}

    // Reference snapshots for recent burn rate (from backend)
    const refs = raw.references || {}

    // 5h window: long = 1h, short = 30min
    const _fhRef = (raw_ref) => raw_ref
        ? { fetchedAt: raw_ref.fetched_at, utilization: raw_ref.five_hour_utilization }
        : null
    const fhRefLong = _fhRef(refs.one_hour)
    const fhRefShort = _fhRef(refs.thirty_min)

    // 7d windows: long = 24h, short = 12h
    const _sdRef = (raw_ref, field) => raw_ref
        ? { fetchedAt: raw_ref.fetched_at, utilization: raw_ref[field] }
        : null

    // Cross-period references (previous period data for early-window burn rates)
    const _fhCross = (cross) => cross ? {
        prevRef: { fetchedAt: cross.prev_ref.fetched_at, utilization: cross.prev_ref.five_hour_utilization },
        prevEnd: { fetchedAt: cross.prev_end.fetched_at, utilization: cross.prev_end.five_hour_utilization },
    } : null
    const _sdCross = (cross, field) => cross ? {
        prevRef: { fetchedAt: cross.prev_ref.fetched_at, utilization: cross.prev_ref[field] },
        prevEnd: { fetchedAt: cross.prev_end.fetched_at, utilization: cross.prev_end[field] },
    } : null

    const crossFhLong = _fhCross(refs.cross_fh_long)
    const crossFhShort = _fhCross(refs.cross_fh_short)

    return {
        fetchedAt,

        fiveHour: computeQuota(
            raw.five_hour_utilization,
            raw.five_hour_resets_at,
            fetchedAt,
            FIVE_HOURS_MS,
            fhRefLong,
            fhRefShort,
            ONE_HOUR_MS,
            THIRTY_MIN_MS,
            crossFhLong,
            crossFhShort,
        ),

        sevenDay: computeQuota(
            raw.seven_day_utilization,
            raw.seven_day_resets_at,
            fetchedAt,
            SEVEN_DAYS_MS,
            _sdRef(refs.one_day, 'seven_day_utilization'),
            _sdRef(refs.twelve_hour, 'seven_day_utilization'),
            ONE_DAY_MS,
            TWELVE_HOURS_MS,
            _sdCross(refs.cross_sd_long, 'seven_day_utilization'),
            _sdCross(refs.cross_sd_short, 'seven_day_utilization'),
        ),

        sevenDayOpus: computeQuota(
            raw.seven_day_opus_utilization,
            raw.seven_day_opus_resets_at,
            fetchedAt,
            SEVEN_DAYS_MS,
            _sdRef(refs.one_day, 'seven_day_opus_utilization'),
            _sdRef(refs.twelve_hour, 'seven_day_opus_utilization'),
            ONE_DAY_MS,
            TWELVE_HOURS_MS,
            _sdCross(refs.cross_sd_long, 'seven_day_opus_utilization'),
            _sdCross(refs.cross_sd_short, 'seven_day_opus_utilization'),
        ),

        sevenDaySonnet: computeQuota(
            raw.seven_day_sonnet_utilization,
            raw.seven_day_sonnet_resets_at,
            fetchedAt,
            SEVEN_DAYS_MS,
            _sdRef(refs.one_day, 'seven_day_sonnet_utilization'),
            _sdRef(refs.twelve_hour, 'seven_day_sonnet_utilization'),
            ONE_DAY_MS,
            TWELVE_HOURS_MS,
            _sdCross(refs.cross_sd_long, 'seven_day_sonnet_utilization'),
            _sdCross(refs.cross_sd_short, 'seven_day_sonnet_utilization'),
        ),

        sevenDayOauthApps: computeQuota(
            raw.seven_day_oauth_apps_utilization,
            raw.seven_day_oauth_apps_resets_at,
            fetchedAt,
            SEVEN_DAYS_MS,
            _sdRef(refs.one_day, 'seven_day_oauth_apps_utilization'),
            _sdRef(refs.twelve_hour, 'seven_day_oauth_apps_utilization'),
            ONE_DAY_MS,
            TWELVE_HOURS_MS,
            _sdCross(refs.cross_sd_long, 'seven_day_oauth_apps_utilization'),
            _sdCross(refs.cross_sd_short, 'seven_day_oauth_apps_utilization'),
        ),

        sevenDayCowork: computeQuota(
            raw.seven_day_cowork_utilization,
            raw.seven_day_cowork_resets_at,
            fetchedAt,
            SEVEN_DAYS_MS,
            _sdRef(refs.one_day, 'seven_day_cowork_utilization'),
            _sdRef(refs.twelve_hour, 'seven_day_cowork_utilization'),
            ONE_DAY_MS,
            TWELVE_HOURS_MS,
            _sdCross(refs.cross_sd_long, 'seven_day_cowork_utilization'),
            _sdCross(refs.cross_sd_short, 'seven_day_cowork_utilization'),
        ),

        extraUsage: {
            isEnabled: raw.extra_usage_is_enabled ?? false,
            monthlyLimit: raw.extra_usage_monthly_limit,
            usedCredits: raw.extra_usage_used_credits,
            utilization: raw.extra_usage_utilization,
        },

        // Period cost estimates
        fiveHourCost: computePeriodCost(periodCosts.five_hour),
        sevenDayCost: computePeriodCost(periodCosts.seven_day),
    }
}