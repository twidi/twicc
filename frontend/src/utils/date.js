import { i18n } from '../i18n/index.js'

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
 * Format an epoch ms timestamp as a fixed 24-hour clock "HH:MM" in local time.
 *
 * Used for the always-visible per-message timestamp label. Deliberately
 * setting-independent and non-relative so it never needs periodic refreshing
 * (no live "N minutes ago" tickers). Day-boundary disambiguation is left to the
 * hover tooltip (formatFullDateTime).
 *
 * @param {number} timestampMs - Epoch milliseconds.
 * @returns {string} e.g. "21:01", or "-" when no timestamp.
 */
export function formatClockTime(timestampMs) {
    if (!timestampMs) return '-'
    const d = new Date(timestampMs)
    const pad = (n) => String(n).padStart(2, '0')
    return `${pad(d.getHours())}:${pad(d.getMinutes())}`
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
    const d = new Date(timestampMs)
    const pad = (n) => String(n).padStart(2, '0')
    return (
        `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ` +
        `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`
    )
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
    if (deltaSec < 60) return 'just now'
    if (deltaSec < 3600) return `${Math.floor(deltaSec / 60)}m ago`
    if (deltaSec < 86400) return `${Math.floor(deltaSec / 3600)}h ago`
    if (deltaSec < 7 * 86400) return `${Math.floor(deltaSec / 86400)}d ago`
    return formatDate(Math.floor(timestampMs / 1000), { smart: true })
}
