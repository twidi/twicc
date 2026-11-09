import { i18n } from '../i18n/index.js'
import { formatClock } from './timeline/eventTime.js'

/**
 * Format a Unix timestamp using locale-aware formatting
 * @param {number} timestamp - Unix timestamp in seconds
 * @param {Object} options - Formatting options
 * @param {boolean} options.smart - If true, use smart formatting:
 *   - < 24h ago: time only (e.g., "14:32")
 *   - Same year: day/month + time (e.g., "30/01 14:32")
 *   - Older: full date with year (e.g., "30/01/2024 14:32")
 * @returns {string} Formatted date string
 */
export function formatDate(timestamp, { smart = false } = {}) {
    if (!timestamp) return '-'

    const date = new Date(timestamp * 1000)
    const now = new Date()
    const locale = i18n.global.locale.value

    // Time formatting (always the same)
    const timeStr = date.toLocaleTimeString(locale, {
        hour: '2-digit',
        minute: '2-digit',
    })

    if (smart) {
        const hoursDiff = (now - date) / (1000 * 60 * 60)

        // Within 24 hours (past or future): time only
        // We accept future times to handle slight clock desync between backend and frontend
        if (hoursDiff < 24 && hoursDiff >= -24) {
            return timeStr
        }

        // Same year: day/month + time
        if (date.getFullYear() === now.getFullYear()) {
            const dateStr = date.toLocaleDateString(locale, {
                day: '2-digit',
                month: '2-digit',
            })
            return `${dateStr} ${timeStr}`
        }

        // Older: full date with year
        const dateStr = date.toLocaleDateString(locale, {
            day: '2-digit',
            month: '2-digit',
            year: 'numeric',
        })
        return `${dateStr} ${timeStr}`
    }

    // Full format (default)
    const dateStr = date.toLocaleDateString(locale, {
        day: '2-digit',
        month: '2-digit',
        year: 'numeric',
    })
    return `${dateStr} ${timeStr}`
}

/**
 * Format an epoch ms timestamp as a local clock time, hour and minute, in the
 * app locale ("21:01" in French, "9:01 PM" in English).
 *
 * Used for the always-visible per-message timestamp label. Deliberately
 * non-relative so it never needs periodic refreshing (no live "N minutes ago"
 * tickers). Day-boundary disambiguation is left to the hover tooltip
 * (formatFullDateTime). Shares formatClock with the timeline and the
 * background-work texts, so every clock of the app has the same format.
 *
 * @param {number} timestampMs - Epoch milliseconds.
 * @returns {string} Clock time, or "-" when no timestamp.
 */
export function formatClockTime(timestampMs) {
    if (!timestampMs) return '-'
    return formatClock(timestampMs) || '-'
}

/**
 * Local calendar-day key for an epoch ms timestamp: "YYYY-MM-DD".
 *
 * Used to detect day changes between items (in local time, consistent with the
 * HH:MM label) and as a stable identity for day-separator entries.
 *
 * @param {number} timestampMs - Epoch milliseconds.
 * @returns {string|null} e.g. "2026-06-05", or null when no timestamp.
 */
export function formatDayKey(timestampMs) {
    if (!timestampMs || !Number.isFinite(timestampMs)) return null
    const d = new Date(timestampMs)
    const pad = (n) => String(n).padStart(2, '0')
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
}

// Day keys of ISO timestamp strings. The chat derives one per displayed row on
// every recompute (day separators), and its rows are rebuilt each time: parsing
// and formatting thousands of dates was most of that work, for strings that never
// change. Cleared when it grows past the limit (far above one chat's rows), and
// when the local time zone changes (see refreshDayKeysTimeZone).
const dayKeyCache = new Map()
const DAY_KEY_CACHE_LIMIT = 50_000
let dayKeyTimeZone = null

/**
 * Drop the cached day keys if the local time zone changed since they were computed
 * (a laptop that travels with the tab open). Call once per pass over the rows.
 */
export function refreshDayKeysTimeZone() {
    const zone = Intl.DateTimeFormat().resolvedOptions().timeZone
    if (zone !== dayKeyTimeZone) {
        dayKeyCache.clear()
        dayKeyTimeZone = zone
    }
}

/**
 * Local calendar-day key of an ISO timestamp string, as formatDayKey(Date.parse(value)).
 *
 * @param {string|null|undefined} timestamp - ISO 8601 timestamp.
 * @returns {string|null} e.g. "2026-06-05", or null when not a valid timestamp.
 */
export function dayKeyOfTimestamp(timestamp) {
    if (typeof timestamp !== 'string') {
        const ms = Date.parse(timestamp)
        return Number.isFinite(ms) ? formatDayKey(ms) : null
    }
    let key = dayKeyCache.get(timestamp)
    if (key === undefined) {
        const ms = Date.parse(timestamp)
        key = Number.isFinite(ms) ? formatDayKey(ms) : null
        if (dayKeyCache.size >= DAY_KEY_CACHE_LIMIT) dayKeyCache.clear()
        dayKeyCache.set(timestamp, key)
    }
    return key
}

/**
 * Human-readable, locale-aware label for a day separator, e.g.
 * "Friday, June 5, 2026" / "vendredi 5 juin 2026" (local time).
 *
 * @param {number} timestampMs - Epoch milliseconds.
 * @returns {string} Localized full date, or "-" when no timestamp.
 */
export function formatDaySeparatorLabel(timestampMs) {
    if (!timestampMs || !Number.isFinite(timestampMs)) return '-'
    return new Date(timestampMs).toLocaleDateString(i18n.global.locale.value, {
        weekday: 'long',
        year: 'numeric',
        month: 'long',
        day: 'numeric',
    })
}

/**
 * Format an epoch ms timestamp as a full, classic local date-time string:
 * "YYYY-MM-DD HH:MM:SS" (with seconds). Uses the browser's local timezone.
 *
 * Used for the hover tooltip on per-message timestamps, where the always-visible
 * label may be relative or smart-truncated but the tooltip must show the exact
 * moment down to the second.
 *
 * @param {number} timestampMs - Epoch milliseconds.
 * @returns {string} e.g. "2026-06-05 21:01:41", or "-" when no timestamp.
 */
export function formatFullDateTime(timestampMs) {
    if (!timestampMs) return '-'
    return new Intl.DateTimeFormat(i18n.global.locale.value, {
        year: 'numeric', month: '2-digit', day: '2-digit',
        hour: '2-digit', minute: '2-digit', second: '2-digit',
    }).format(new Date(timestampMs))
}

/**
 * Format a duration in seconds to a human-readable string.
 * - < 60s: "42s"
 * - < 1h: "2m 30s"
 * - >= 1h: "1h 15m 30s"
 * @param {number} seconds - Duration in seconds
 * @returns {string} Formatted duration string
 */
export function formatDuration(seconds) {
    if (seconds < 0) seconds = 0
    seconds = Math.floor(seconds)

    const hours = Math.floor(seconds / 3600)
    const minutes = Math.floor((seconds % 3600) / 60)
    const secs = seconds % 60

    if (hours > 0) {
        return `${hours}h ${minutes}m ${secs}s`
    }
    if (minutes > 0) {
        return `${minutes}m ${secs}s`
    }
    return `${secs}s`
}

/**
 * Format an epoch ms timestamp as a relative time string.
 *
 * - < 1 minute : "just now"
 * - < 1 hour   : "Nm ago"
 * - < 24 hours : "Nh ago"
 * - < 7 days   : "Nd ago"
 * - older      : falls back to formatDate(timestamp_seconds, { smart: true })
 *
 * @param {number} timestampMs - Epoch milliseconds (use Date.parse(iso) for ISO strings).
 * @returns {string}
 */
export function formatRelative(timestampMs) {
    if (!timestampMs) return '-'
    const deltaSec = Math.max(0, Math.floor((Date.now() - timestampMs) / 1000))
    if (deltaSec < 60) return i18n.global.t('common.time.justNow')
    if (deltaSec < 3600) return new Intl.RelativeTimeFormat(i18n.global.locale.value, { style: 'narrow' }).format(-Math.floor(deltaSec / 60), 'minute')
    if (deltaSec < 86400) return new Intl.RelativeTimeFormat(i18n.global.locale.value, { style: 'narrow' }).format(-Math.floor(deltaSec / 3600), 'hour')
    if (deltaSec < 7 * 86400) return new Intl.RelativeTimeFormat(i18n.global.locale.value, { style: 'narrow' }).format(-Math.floor(deltaSec / 86400), 'day')
    return formatDate(Math.floor(timestampMs / 1000), { smart: true })
}
