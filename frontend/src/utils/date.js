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

    // Time formatting (always the same)
    const timeStr = date.toLocaleTimeString(undefined, {
        hour: '2-digit',
        minute: '2-digit',
    })

    if (smart) {
        const hoursDiff = (now - date) / (1000 * 60 * 60)

        // Less than 24 hours ago: time only
        if (hoursDiff < 24 && hoursDiff >= 0) {
            return timeStr
        }

        // Same year: day/month + time
        if (date.getFullYear() === now.getFullYear()) {
            const dateStr = date.toLocaleDateString(undefined, {
                day: '2-digit',
                month: '2-digit',
            })
            return `${dateStr} ${timeStr}`
        }

        // Older: full date with year
        const dateStr = date.toLocaleDateString(undefined, {
            day: '2-digit',
            month: '2-digit',
            year: 'numeric',
        })
        return `${dateStr} ${timeStr}`
    }

    // Full format (default)
    const dateStr = date.toLocaleDateString(undefined, {
        day: '2-digit',
        month: '2-digit',
        year: 'numeric',
    })
    return `${dateStr} ${timeStr}`
}
