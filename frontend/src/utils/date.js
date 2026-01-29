/**
 * Format a Unix timestamp to dd-mm-yyyy HH:MM
 * Uses dashes to avoid ambiguity between dd/mm and mm/dd formats
 * @param {number} timestamp - Unix timestamp in seconds
 * @returns {string} Formatted date string
 */
export function formatDate(timestamp) {
    if (!timestamp) return '-'

    const date = new Date(timestamp * 1000)

    const day = String(date.getDate()).padStart(2, '0')
    const month = String(date.getMonth() + 1).padStart(2, '0')
    const year = date.getFullYear()
    const hours = String(date.getHours()).padStart(2, '0')
    const minutes = String(date.getMinutes()).padStart(2, '0')

    return `${day}-${month}-${year} ${hours}:${minutes}`
}
