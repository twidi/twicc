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
 * @param {string} mode - Display mode: 'conversation' | 'simplified' | 'normal' | 'debug'
 * @param {Array} expandedGroups - Array of expanded group_head line numbers
 * @param {boolean} [isAssistantTurn=false] - Whether we're currently in an assistant turn.
 *   In conversation mode, this hides the trailing assistant_message (incomplete/in-progress).
 * @param {Set} [detailedBlocks=new Set()] - Set of user_message line_nums whose following
 *   non-user block should be rendered in detailed (normal) mode instead of conversation mode.
 *   Only used when mode is 'conversation'. Each entry is the line_num of the last
 *   user_message before a non-user block; all non-user items following that user_message
 *   (up to the next user_message) will be shown using normal-mode filtering (levels 1+2,
 *   hide level 3).
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
 *   - detailToggleFor?: user_message line_num identifying the block this item's toggle controls
 *     (conversation mode only). Always set on the first non-user-message visual item after
 *     the last user_message of a user block.
 */
export function computeVisualItems(items, mode, expandedGroups = [], isAssistantTurn = false, detailedBlocks = new Set()) {
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

    // Conversation mode: show only user_messages + last assistant_message before each user_message
    // + trailing assistant_message (unless we're in assistant_turn, since the optimistic
    // user_message already captures the last pre-turn assistant_message naturally).
    //
    // Items naturally alternate between "user blocks" (consecutive user_messages) and
    // "non-user blocks" (all other items until the next user_message).
    // Each non-user block is identified by the line_num of the last user_message that precedes it.
    // The detail toggle is always on the first visible non-user item of that block.
    //
    // When a block's user_message line_num is in detailedBlocks, all its non-user items
    // are shown with normal-mode filtering (levels 1+2, hide level 3).
    if (mode === DISPLAY_MODE.CONVERSATION) {
        // Phase 1: identify which assistant_messages to keep (one per non-user block).
        // Each non-user block keeps only the last assistant_message before the next user_message.
        // Both real and synthetic items participate: synthetic user_messages (optimistic)
        // close blocks, but only real assistant_messages (line_num >= 0) are tracked.
        const keptAssistantLineNums = new Set()
        let lastAssistantLineNum = null

        for (const item of items) {
            if (item.kind === 'assistant_message' && item.line_num >= 0) {
                lastAssistantLineNum = item.line_num
            } else if (item.kind === 'user_message') {
                if (lastAssistantLineNum !== null) {
                    keptAssistantLineNums.add(lastAssistantLineNum)
                    lastAssistantLineNum = null
                }
            }
        }

        // Trailing assistant_message: show last one only if not in assistant_turn
        if (lastAssistantLineNum !== null && !isAssistantTurn) {
            keptAssistantLineNums.add(lastAssistantLineNum)
        }

        // Phase 2: identify non-user blocks and their user_message identifiers.
        // Maps each non-user item's line_num → the user_message line_num that identifies its block.
        // Also counts visible (level 1+2) non-user items per block to decide whether
        // the detail toggle is useful (only shown when there are 2+ visible items).
        const itemBlockId = new Map() // item line_num → user_message line_num
        const blockVisibleCount = new Map() // user_message line_num → count of visible non-user items
        let lastUserLineNum = null

        for (const item of items) {
            if (item.kind === 'user_message') {
                lastUserLineNum = item.line_num
            } else if (lastUserLineNum !== null) {
                // Non-user item after a user_message: belongs to this block
                itemBlockId.set(item.line_num, lastUserLineNum)
                if (item.display_level == null || item.display_level !== DISPLAY_LEVEL.DEBUG_ONLY) {
                    blockVisibleCount.set(lastUserLineNum, (blockVisibleCount.get(lastUserLineNum) || 0) + 1)
                }
            }
        }

        // Phase 3: build result.
        // Track whether we've placed the detail toggle for each block.
        const togglePlaced = new Set()

        for (const item of items) {
            const blockId = itemBlockId.get(item.line_num)
            const isDetailed = blockId != null && detailedBlocks.has(blockId)

            if (item.kind === 'user_message') {
                // User messages always pass through
                result.push({
                    lineNum: item.line_num,
                    content: item.content,
                    kind: item.kind,
                    groupHead: null,
                    groupTail: null
                })
            } else if (blockId == null) {
                // Non-user item before any user_message (edge case): skip in conversation mode
                continue
            } else if (isDetailed) {
                // Block is in detailed mode: show non-user items with normal-mode filtering
                if (item.display_level == null || item.display_level !== DISPLAY_LEVEL.DEBUG_ONLY) {
                    const visualItem = {
                        lineNum: item.line_num,
                        content: item.content,
                        kind: item.kind,
                        groupHead: item.group_head ?? null,
                        groupTail: item.group_tail ?? null
                    }
                    // First visible non-user item of this block gets the toggle
                    // (only if the block has 2+ visible items, otherwise toggle is useless)
                    if (!togglePlaced.has(blockId) && (blockVisibleCount.get(blockId) || 0) > 1) {
                        visualItem.detailToggleFor = blockId
                        togglePlaced.add(blockId)
                    }
                    result.push(visualItem)
                }
            } else if (item.line_num < 0 || (item.kind === 'assistant_message' && keptAssistantLineNums.has(item.line_num))) {
                // Non-detailed mode: show synthetic items and kept assistant_messages
                const visualItem = {
                    lineNum: item.line_num,
                    content: item.content,
                    kind: item.kind,
                    groupHead: null,
                    groupTail: null
                }
                // First visible non-user item of this block gets the toggle
                // (only if the block has 2+ visible items, otherwise toggle is useless)
                if (!togglePlaced.has(blockId) && (blockVisibleCount.get(blockId) || 0) > 1) {
                    visualItem.detailToggleFor = blockId
                    togglePlaced.add(blockId)
                }
                result.push(visualItem)
            }
        }
        return result
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
