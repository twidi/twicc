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
 * Format the compact burn-rate chip shown at the end of a quota bar.
 *
 * The chip is always a burn multiplier — never a percentage:
 *   - exhausted (utilization >= 100%) → "100%+"
 *   - a computed burn rate > 0.05     → "×0.7" / "×2.3" (one decimal below ×10)
 *
 * Returns null (no chip at all) when there is nothing meaningful to show: no
 * utilization, or a burn rate that rounds to ×0.0 (<= 0.05, i.e. under 5%) or
 * isn't computable yet.
 *
 * @param {object|null} quota - A computed quota object from computeUsageData()
 * @returns {{text: string}|null}
 */
export function formatBurnChip(quota) {
    if (!quota || quota.utilization == null) return null
    if (quota.utilization >= 100) return { text: '100%+' }
    if (quota.burnRate != null && quota.burnRate > 0.05) {
        const b = quota.burnRate
        return { text: '×' + b.toFixed(b < 10 ? 1 : 0) }
    }
    return null
}

/**
 * Render an extra-usage credit figure as a bare money amount.
 *
 * ``value`` is in minor units and ``decimalPlaces`` is the exponent the
 * provider reported alongside the currency, so 4419 / 2 gives "44.19".
 * Trailing zeros are dropped: 8000 / 2 gives "80". Returns null when either
 * side is missing — the snapshot then carries no money shape and the caller
 * falls back to bare credit counts.
 *
 * Python mirror: ``format_extra_usage_amount`` in ``src/twicc/usage.py``.
 *
 * @param {number|null|undefined} value - Figure in minor units
 * @param {number|null|undefined} decimalPlaces - Currency exponent
 * @returns {string|null}
 */
export function formatExtraUsageAmount(value, decimalPlaces) {
    if (value == null || decimalPlaces == null || decimalPlaces < 0) return null
    const amount = value / 10 ** decimalPlaces
    return amount.toLocaleString(navigator.language, { maximumFractionDigits: decimalPlaces })
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
 * @param {string|null} resetsAt - ISO datetime of the window reset (for cutoff positioning)
 * @param {number|null} windowMs - Window duration in ms (for cutoff positioning)
 * @returns {object} Period cost info
 */
function computePeriodCost(raw, resetsAt = null, windowMs = null) {
    if (!raw) {
        return { spent: null, estimatedPeriod: null, estimatedMonthly: null, capped: false, cutoffAt: null, cutoffPct: null }
    }
    const cutoffAt = raw.cutoff_at ?? null
    // Position of the projected cutoff on the window timeline (0–100%), so the UI
    // can mark where the quota will run out on the time lane. Only meaningful when
    // the window has started (resetsAt known) and a cutoff was projected.
    const cutoffPct = (cutoffAt && resetsAt && windowMs)
        ? temporalPct(cutoffAt, resetsAt, windowMs)
        : null
    return {
        spent: raw.spent ?? null,
        estimatedPeriod: raw.estimated_period ?? null,
        estimatedMonthly: raw.estimated_monthly ?? null,
        capped: raw.capped ?? false,
        cutoffAt,
        cutoffPct,
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
 *
 * @param {number|null} deltaMs - Time span in milliseconds
 * @param {boolean} roundToHour - If true, round to nearest hour (for 7d window).
 *                                 If false, round to nearest 10 minutes (for 5h window).
 * @returns {string|null} Formatted duration, e.g. "2h", "50min", "1h", or null
 */
export function formatRecentDelta(deltaMs, roundToHour) {
    if (deltaMs == null || deltaMs <= 0) return null
    return formatDuration(deltaMs, roundToHour)
}

/**
 * Short reset/cutoff time: time only under 24h, weekday + time under 7 days, else weekday + date.
 *
 * @param {string|number|Date|null} resetsAt
 * @returns {string}
 */
export function formatResetTime(resetsAt) {
    if (!resetsAt) return '?'
    const reset = resetsAt instanceof Date ? resetsAt : new Date(resetsAt)
    const now = new Date()
    const locale = navigator.language
    const diffMs = reset - now
    const diffHours = diffMs / (1000 * 60 * 60)
    // < 24h: time only
    if (diffHours < 24) {
        return reset.toLocaleTimeString(locale, { hour: '2-digit', minute: '2-digit' })
    }
    // < 7 days: weekday + time
    if (diffHours < 7 * 24) {
        return reset.toLocaleDateString(locale, { weekday: 'long', hour: '2-digit', minute: '2-digit' })
    }
    // >= 7 days: weekday + day/month
    return reset.toLocaleDateString(locale, { weekday: 'long', day: 'numeric', month: 'numeric' })
}

/**
 * A reset/cutoff moment split for use inside an English sentence: the clock follows the
 * browser locale, the day and month names stay English (a localized "samedi" in the middle
 * of an English phrase reads badly). Same windows as formatResetTime: under 24h only a time,
 * under 7 days a weekday + time, beyond a weekday + date (no time).
 *
 * @param {string|number|Date|null} when
 * @returns {{time: string|null, day: string|null}}
 */
export function formatQuotaMoment(when) {
    if (!when) return { time: null, day: null }
    const date = when instanceof Date ? when : new Date(when)
    const diffHours = (date - new Date()) / (1000 * 60 * 60)
    const time = date.toLocaleTimeString(navigator.language, { hour: '2-digit', minute: '2-digit' })
    const weekday = date.toLocaleDateString('en', { weekday: 'long' })
    if (diffHours < 24) return { time, day: null }
    if (diffHours < 7 * 24) return { time, day: weekday }
    return { time: null, day: `${weekday} ${date.getDate()} ${date.toLocaleDateString('en', { month: 'long' })}` }
}

/**
 * Full reset date with weekday, day, month and time (tooltip precision).
 *
 * @param {string|number|Date|null} resetsAt
 * @returns {string}
 */
export function formatResetTimePrecise(resetsAt) {
    if (!resetsAt) return ''
    const reset = resetsAt instanceof Date ? resetsAt : new Date(resetsAt)
    const locale = navigator.language
    return reset.toLocaleDateString(locale, { weekday: 'long', day: 'numeric', month: 'long', hour: '2-digit', minute: '2-digit' })
}

/**
 * Detect whether extra usage was consumed in the last ~hour.
 *
 * Returns true when the current snapshot shows more consumption than the
 * 1h-ago reference: ``utilization`` rising for Anthropic-style providers,
 * ``remaining_credits`` falling for Codex-style providers. Used by the
 * sidebar "only when needed" gate to surface the panel during mid-period
 * activity (e.g. fast mode burning extra credits before the 5h/7d quotas
 * saturate). Conservative when the reference is missing: returns false
 * rather than risk a false-positive.
 *
 * @param {object} raw - The current snapshot (serialized UsageSnapshot).
 * @param {object|null|undefined} ref - The 1h-ago reference snapshot.
 * @returns {boolean}
 */
function computeExtraUsageRecentlyActive(raw, ref) {
    if (!ref) return false

    // Anthropic-style: utilization (or used_credits) goes up with consumption.
    // ``?? 0`` covers the case where the reference is null and the current is
    // positive — that's still an increase, the user asked us to flag it.
    const curUtil = raw.extra_usage_utilization
    if (curUtil != null && curUtil > (ref.extra_usage_utilization ?? 0)) return true

    // Codex-style: balance goes down with consumption. Require both sides
    // present — if the reference balance is null we can't tell if anything
    // was consumed, so we don't claim activity.
    const curRem = raw.extra_usage_remaining_credits
    const refRem = ref.extra_usage_remaining_credits
    if (curRem != null && refRem != null && curRem < refRem) return true

    return false
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

        // Extra usage. Two display modes coexist on the same object,
        // selected by which fields the backend populates:
        // - Anthropic-style (Claude Code): monthlyLimit + usedCredits +
        //   utilization (percent ring). remainingCredits is null.
        // - Remaining-only (Codex): only remainingCredits is set
        //   (absolute counter), the three Anthropic-style fields are null.
        // currency + decimalPlaces turn monthlyLimit / usedCredits into
        // money (they are minor units); both null means bare credit counts.
        // recentlyActive flags consumption in the last ~hour and is used
        // by the sidebar gate to keep the panel visible even when the
        // 5h / 7d quotas aren't saturated (e.g. fast mode burning credits
        // mid-period).
        extraUsage: {
            isEnabled: raw.extra_usage_is_enabled ?? false,
            monthlyLimit: raw.extra_usage_monthly_limit,
            usedCredits: raw.extra_usage_used_credits,
            utilization: raw.extra_usage_utilization,
            remainingCredits: raw.extra_usage_remaining_credits,
            currency: raw.extra_usage_currency ?? null,
            decimalPlaces: raw.extra_usage_decimal_places ?? null,
            recentlyActive: computeExtraUsageRecentlyActive(raw, refs.extra_usage_one_hour),
        },

        // Period cost estimates
        fiveHourCost: computePeriodCost(periodCosts.five_hour, raw.five_hour_resets_at, FIVE_HOURS_MS),
        sevenDayCost: computePeriodCost(periodCosts.seven_day, raw.seven_day_resets_at, SEVEN_DAYS_MS),
    }
}