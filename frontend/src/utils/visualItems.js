// frontend/src/utils/visualItems.js

/**
 * Compute visual items list from session items based on display mode and expanded groups.
 *
 * @param {Array} items - Array of session items with metadata
 * @param {string} mode - Display mode: 'debug' | 'normal' | 'simplified'
 * @param {Array} expandedGroups - Array of expanded group_head line numbers
 * @returns {Array} Array of visual items: { lineNum, isGroupHead?, isExpanded? }
 */
export function computeVisualItems(items, mode, expandedGroups = []) {
    if (!items || items.length === 0) return []

    const result = []
    const expandedSet = new Set(expandedGroups)  // Convert to Set for O(1) lookup

    for (const item of items) {
        // Skip items without display_level (not yet computed)
        if (item.display_level == null) {
            // In debug mode, show anyway; in other modes, skip
            if (mode === 'debug') {
                result.push({ lineNum: item.line_num })
            }
            continue
        }

        // Debug mode: show everything
        if (mode === 'debug') {
            result.push({ lineNum: item.line_num })
            continue
        }

        // Normal mode: show levels 1 and 2, hide level 3
        if (mode === 'normal') {
            if (item.display_level !== 3) {
                result.push({ lineNum: item.line_num })
            }
            // level 3: skip entirely
            continue
        }

        // Simplified mode: groups are collapsible
        if (item.display_level === 1) {
            // Level 1: always visible
            result.push({ lineNum: item.line_num })
        } else if (item.display_level === 2) {
            // Level 2: check if head or member
            const isHead = item.line_num === item.group_head
            const isExpanded = expandedSet.has(item.group_head)

            if (isHead) {
                // Group head: always has a visual entry (shows toggle)
                result.push({
                    lineNum: item.line_num,
                    isGroupHead: true,
                    isExpanded: isExpanded
                })
            } else if (isExpanded) {
                // Group member, group is expanded: visible
                result.push({ lineNum: item.line_num })
            }
            // else: group member, group collapsed: no visual entry
        }
        // Level 3 in simplified mode: never visible (skip)
    }

    return result
}
