// frontend/src/utils/truncate.js
// Shared text truncation utilities

/**
 * Truncate a title string to maxLength characters, adding an ellipsis if needed.
 * @param {string|null|undefined} title - The title to truncate
 * @param {number} maxLength - Maximum length before truncation (default: 50)
 * @param {string} fallback - Fallback text when title is empty/null (default: 'Unknown')
 * @returns {string}
 */
export function truncateTitle(title, maxLength = 50, fallback = 'Unknown') {
    if (!title) return fallback
    return title.length > maxLength ? title.slice(0, maxLength) + '…' : title
}
