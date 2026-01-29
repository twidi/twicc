// frontend/src/utils/visualItems.js

import { DISPLAY_LEVEL, DISPLAY_MODE } from '../constants'

/**
 * Compute visual items list from session items based on display mode and expanded groups.
 *
 * In simplified mode, ALWAYS items (display_level 1) may participate in groups:
 * - group_head != null: item has a connected prefix (part of a group ending at this item)
 * - group_tail != null: item has a connected suffix (starts a group continuing after)
 *
 * COLLAPSIBLE items (display_level 2) can have:
 * - group_head == line_num: this item starts a group (pure COLLAPSIBLE sequence or ending on ALWAYS prefix)
 * - group_head pointing to an ALWAYS: this item is part of a group started by an ALWAYS suffix
 *
 * @param {Array} items - Array of session items with metadata
 * @param {string} mode - Display mode: 'debug' | 'normal' | 'simplified'
 * @param {Array} expandedGroups - Array of expanded group_head line numbers
 * @returns {Array} Array of visual items with properties:
 *   - lineNum: the item's line number
 *   - content: the item's raw content (for reactivity in virtual scroller)
 *   - kind: the item's kind
 *   - groupHead: the item's group_head
 *   - groupTail: the item's group_tail
 *   - isGroupHead?: true if this COLLAPSIBLE starts a group (shows toggle before it)
 *   - isExpanded?: true if the group is expanded
 *   - prefixGroupHead?: group_head for prefix (ALWAYS items)
 *   - prefixExpanded?: true if prefix group is expanded (ALWAYS items)
 *   - suffixGroupHead?: this item's line_num when it starts a group via suffix (ALWAYS items)
 *   - suffixExpanded?: true if suffix group is expanded (ALWAYS items)
 */
export function computeVisualItems(items, mode, expandedGroups = []) {
    if (!items || items.length === 0) return []

    const result = []
    const expandedSet = new Set(expandedGroups) // Convert to Set for O(1) lookup

    // Build a set of line_nums that are ALWAYS items
    // Used to detect when a COLLAPSIBLE's group_head points to an ALWAYS
    const alwaysLineNums = new Set()
    // Count items per group (keyed by group_head)
    const groupSizes = new Map()
    for (const item of items) {
        if (item.display_level === DISPLAY_LEVEL.ALWAYS) {
            alwaysLineNums.add(item.line_num)
        }
        // Count items belonging to each group
        if (item.group_head != null && item.display_level === DISPLAY_LEVEL.COLLAPSIBLE) {
            groupSizes.set(item.group_head, (groupSizes.get(item.group_head) || 0) + 1)
        }
    }

    for (const item of items) {
        // Skip items without display_level (not yet computed)
        if (item.display_level == null) {
            // In debug mode, show anyway; in other modes, skip
            if (mode === DISPLAY_MODE.DEBUG) {
                result.push({
                    lineNum: item.line_num,
                    content: item.content,
                    kind: item.kind,
                    groupHead: item.group_head ?? null,
                    groupTail: item.group_tail ?? null
                })
            }
            continue
        }

        // Debug mode: show everything
        if (mode === DISPLAY_MODE.DEBUG) {
            result.push({
                lineNum: item.line_num,
                content: item.content,
                kind: item.kind,
                groupHead: item.group_head ?? null,
                groupTail: item.group_tail ?? null
            })
            continue
        }

        // Normal mode: show levels 1 and 2, hide level 3
        if (mode === DISPLAY_MODE.NORMAL) {
            if (item.display_level !== DISPLAY_LEVEL.DEBUG_ONLY) {
                result.push({
                    lineNum: item.line_num,
                    content: item.content,
                    kind: item.kind,
                    groupHead: item.group_head ?? null,
                    groupTail: item.group_tail ?? null
                })
            }
            // level 3: skip entirely
            continue
        }

        // Simplified mode: groups are collapsible
        if (item.display_level === DISPLAY_LEVEL.ALWAYS) {
            // Level 1 (ALWAYS): always visible, but may have group participation
            const visualItem = {
                lineNum: item.line_num,
                content: item.content,
                kind: item.kind,
                groupHead: item.group_head ?? null,
                groupTail: item.group_tail ?? null
            }

            // Check if this ALWAYS has a connected prefix (group_head points to previous item)
            if (item.group_head != null) {
                visualItem.prefixGroupHead = item.group_head
                visualItem.prefixExpanded = expandedSet.has(item.group_head)
            }

            // Check if this ALWAYS starts a group with its suffix (group_tail points to following item)
            if (item.group_tail != null) {
                // This ALWAYS's suffix starts a group. The group_head for this group is this item's line_num.
                // Following COLLAPSIBLE items will have group_head pointing here.
                visualItem.suffixGroupHead = item.line_num
                visualItem.suffixExpanded = expandedSet.has(item.line_num)
                visualItem.suffixGroupSize = groupSizes.get(item.line_num) || 0
            }

            result.push(visualItem)
        } else if (item.display_level === DISPLAY_LEVEL.COLLAPSIBLE) {
            // Level 2 (COLLAPSIBLE)
            // Check if this item is its own group_head (starts a group)
            // or if group_head points to an ALWAYS (part of a group started by ALWAYS suffix)
            const isOwnGroupHead = item.line_num === item.group_head
            const groupHeadIsAlways = !isOwnGroupHead && alwaysLineNums.has(item.group_head)
            const isExpanded = expandedSet.has(item.group_head)

            if (isOwnGroupHead) {
                // This COLLAPSIBLE starts a group (may end on ALWAYS prefix or be pure COLLAPSIBLE)
                result.push({
                    lineNum: item.line_num,
                    content: item.content,
                    kind: item.kind,
                    groupHead: item.group_head ?? null,
                    groupTail: item.group_tail ?? null,
                    isGroupHead: true,
                    isExpanded: isExpanded,
                    groupSize: groupSizes.get(item.group_head) || 0
                })
            } else if (groupHeadIsAlways) {
                // This COLLAPSIBLE is part of a group started by an ALWAYS's suffix
                // Only show if that group is expanded
                if (isExpanded) {
                    result.push({
                        lineNum: item.line_num,
                        content: item.content,
                        kind: item.kind,
                        groupHead: item.group_head ?? null,
                        groupTail: item.group_tail ?? null
                    })
                }
                // else: hidden (group collapsed, toggle is in the ALWAYS's suffix)
            } else if (isExpanded) {
                // Regular group member, show if expanded
                result.push({
                    lineNum: item.line_num,
                    content: item.content,
                    kind: item.kind,
                    groupHead: item.group_head ?? null,
                    groupTail: item.group_tail ?? null
                })
            }
            // else: hidden (group collapsed)
        }
        // Level 3 in simplified mode: never visible (skip)
    }

    return result
}
