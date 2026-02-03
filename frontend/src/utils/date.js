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
    const locale = navigator.language

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
