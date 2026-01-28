/**
 * Utilities for detecting collapsible content zones within ALWAYS items.
 *
 * An ALWAYS item (user_message or assistant_message) may contain:
 * - Visible content types: text, document, image (always shown)
 * - Collapsible content types: tool_use, tool_result, thinking, etc.
 *
 * Collapsible content can form:
 * - Connected prefix/suffix: part of external groups with other session items
 * - Internal groups: orphan prefix/suffix or middle sections (managed locally)
 */

/**
 * Content types that are considered visible (always shown).
 */
export const VISIBLE_CONTENT_TYPES = ['text', 'document', 'image']

/**
 * Check if a content item is visible (not collapsible).
 *
 * @param {any} item - A content item from message.content array
 * @returns {boolean} - True if the item is visible
 */
export function isVisibleItem(item) {
    return typeof item === 'object' && item !== null && VISIBLE_CONTENT_TYPES.includes(item.type)
}

/**
 * Determine prefix and suffix boundaries in an ALWAYS item's content.
 *
 * These boundaries are only meaningful when the item participates in an external group.
 * - Prefix (connected): elements at start that belong to group ending with this item
 * - Suffix (connected): elements at end that start a group continuing after this item
 *
 * @param {Array} content - The content array from the message
 * @param {number|null} groupHead - The item's group_head metadata (non-null = prefix connected)
 * @param {number|null} groupTail - The item's group_tail metadata (non-null = suffix connected)
 * @returns {{ prefixEndIndex: number|null, suffixStartIndex: number|null }}
 */
export function getPrefixSuffixBoundaries(content, groupHead, groupTail) {
    const hasPrefix = groupHead != null
    const hasSuffix = groupTail != null

    if ((!hasPrefix && !hasSuffix) || !Array.isArray(content) || content.length === 0) {
        return { prefixEndIndex: null, suffixStartIndex: null }
    }

    let prefixEndIndex = null
    let suffixStartIndex = null

    if (hasPrefix) {
        for (let i = 0; i < content.length; i++) {
            if (isVisibleItem(content[i])) {
                prefixEndIndex = i > 0 ? i - 1 : null
                break
            }
        }
    }

    if (hasSuffix) {
        for (let i = content.length - 1; i >= 0; i--) {
            if (isVisibleItem(content[i])) {
                suffixStartIndex = i < content.length - 1 ? i + 1 : null
                break
            }
        }
    }

    return { prefixEndIndex, suffixStartIndex }
}

/**
 * Find all internal collapsible groups within an ALWAYS item's content.
 *
 * Internal groups are consecutive sequences of non-visible elements that are NOT
 * part of an external group (connected prefix/suffix).
 *
 * The function scans from after the connected prefix to before the connected suffix,
 * collecting any non-visible sequences (orphan prefix, middle sections, orphan suffix).
 *
 * @param {Array} content - The content array from the message
 * @param {{ prefixEndIndex: number|null, suffixStartIndex: number|null }} boundaries - From getPrefixSuffixBoundaries
 * @returns {Array<{startIndex: number, endIndex: number}>} - List of internal groups
 */
export function getInternalCollapsibleGroups(content, boundaries) {
    if (!Array.isArray(content) || content.length === 0) {
        return []
    }

    const { prefixEndIndex, suffixStartIndex } = boundaries

    // Scan boundaries: skip connected prefix/suffix
    const scanStart = prefixEndIndex != null ? prefixEndIndex + 1 : 0
    const scanEnd = suffixStartIndex != null ? suffixStartIndex - 1 : content.length - 1

    // Collect consecutive non-visible sequences within scan range
    const groups = []
    let currentGroup = null

    for (let i = scanStart; i <= scanEnd; i++) {
        if (!isVisibleItem(content[i])) {
            if (currentGroup === null) {
                currentGroup = { startIndex: i, endIndex: i }
            } else {
                currentGroup.endIndex = i
            }
        } else {
            if (currentGroup !== null) {
                groups.push(currentGroup)
                currentGroup = null
            }
        }
    }

    if (currentGroup !== null) {
        groups.push(currentGroup)
    }

    return groups
}
