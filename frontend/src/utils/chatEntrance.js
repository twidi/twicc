// Live chat entrances (visual refresh step 5a): which rows of a new visual-item list
// "arrive live" and enter. Pure: no Vue, no DOM. The Vue side is
// composables/useChatEntrance.js.
// Design: docs/plans/2026-09-28-chat-entrances-skeleton-design.md §4.
//
// Mounting is not arrival (the virtual scroller re-creates rows that come back into its
// render range): a row enters only when its key is new in the list AND its line was
// marked live since the previous call, or when it is a synthetic row. View changes,
// first loads, lazy fills and synthetic-row swaps never enter.

import { PROCESS_STATE, SYNTHETIC_ITEM } from '../constants.js'

export const CHAT_ENTRANCE_STAGGER_MS = 70
export const CHAT_ENTRANCE_MAX_BATCH = 6
/** Real item kinds a streamed block can turn into (see _retireStreamingBlocks). Claude also
 *  files tool_use-only and tool_result lines as content_items (backend compute), hence the
 *  hasToolBlock check of rule 4. */
export const STREAMED_KINDS = new Set(['assistant_message', 'content_items', 'reasoning'])

const STREAMING_BLOCK_KIND = SYNTHETIC_ITEM.STREAMING_BLOCK.kind
const OPTIMISTIC = SYNTHETIC_ITEM.OPTIMISTIC_USER_MESSAGE.lineNum
const STARTING = SYNTHETIC_ITEM.STARTING_ASSISTANT_MESSAGE.lineNum
const WORKING = SYNTHETIC_ITEM.WORKING_ASSISTANT_MESSAGE.lineNum
const FAILED_BASE = SYNTHETIC_ITEM.FAILED_USER_MESSAGE.baseLineNum

const DAYSEP_KEY = /^daysep-(-?\d+)$/

/** Number carried by a row key: the key itself, N for 'daysep-N', else NaN. */
export function keyLineNum(key) {
    if (typeof key === 'number') return key
    if (typeof key === 'string') {
        const match = DAYSEP_KEY.exec(key)
        if (match) return Number(match[1])
    }
    return NaN
}

const isDaySeparatorKey = (key) => typeof key === 'string' && DAYSEP_KEY.test(key)
const isFailedKey = (key) => keyLineNum(key) <= FAILED_BASE

/** True when a process state adds or removes rows (the starting or working message). */
export function isInTurnState(state) {
    return state === PROCESS_STATE.ASSISTANT_TURN || state === PROCESS_STATE.STARTING
}

/**
 * Whether a suppressing action (§5.2) must mark a view change.
 * @param {{ listBefore, listAfter, inTurnBefore?: boolean, inTurnAfter?: boolean }} p
 * @returns {boolean} listBefore !== listAfter, or inTurnBefore !== inTurnAfter when both
 *                    are given
 */
export function shouldNoteViewChange({ listBefore, listAfter, inTurnBefore, inTurnAfter }) {
    if (listBefore !== listAfter) return true
    if (typeof inTurnBefore === 'boolean' && typeof inTurnAfter === 'boolean') return inTurnBefore !== inTurnAfter
    return false
}

function variantOf(item) {
    if (item?.kind === 'user_message') return 'user'
    if (item?.isBlockStart && item?.isBlockEnd) return 'card'
    return 'slice'
}

/**
 * Decide which rows of a new visual-item list enter.
 * @param {object} p
 * @param {Set<string|number>} p.previousKeys  keys of the previous list
 * @param {Array} p.items                      the new list (visual items, in order)
 * @param {(item) => string|number} p.getKey
 * @param {Set<number>} p.liveLineNums         lines marked live since the previous call
 * @param {Set<number>} p.retiredRealLineNums  real lines that replaced a streamed block
 *                                             since the previous call
 * @param {Set<string|number>} p.previousStreamingKeys  keys of the previous list's rows with
 *                                             syntheticKind 'streaming-block'
 * @param {boolean} p.revealed                 false = record the baseline only
 * @param {boolean} p.viewChanged              true = a view change caused this update
 * @param {(item) => boolean} p.hasToolBlock   true when the item's parsed content holds a
 *                                             tool_use or tool_result block
 * @returns {{ entering: Array<{ key, index: number, variant: 'user'|'card'|'slice' }>,
 *             keys: Set<string|number>, streamingKeys: Set<string|number> }}
 */
export function planChatEntrances({
    previousKeys,
    items,
    getKey,
    liveLineNums,
    retiredRealLineNums,
    previousStreamingKeys,
    revealed,
    viewChanged,
    hasToolBlock,
}) {
    // Rule 1: the new keys, the streamed-block keys, what was added and removed.
    const keys = new Set()
    const streamingKeys = new Set()
    const byKey = new Map()
    const added = []
    for (const item of items) {
        const key = getKey(item)
        keys.add(key)
        byKey.set(key, item)
        if (item.syntheticKind === STREAMING_BLOCK_KIND) streamingKeys.add(key)
        if (!previousKeys.has(key)) added.push(key)
    }
    const result = { entering: [], keys, streamingKeys }

    // Rule 2.
    if (!revealed || viewChanged) return result

    const removed = new Set()
    for (const key of previousKeys) if (!keys.has(key)) removed.add(key)

    // Rule 3: candidates (a separator is a candidate with its live line).
    let candidates = added.filter((key) => {
        const line = keyLineNum(key)
        if (Number.isNaN(line)) return false
        if (line < 0) return true
        return line > 0 && liveLineNums.has(line) && !retiredRealLineNums.has(line)
    })

    // Rule 4: swaps of a synthetic row for its successor.
    const dropped = new Set()
    const drop = (predicate) => {
        candidates = candidates.filter((key) => {
            if (!predicate(key, byKey.get(key))) return true
            dropped.add(key)
            return false
        })
    }
    const isRealUserMessage = (key, item) => typeof key === 'number' && key > 0 && item?.kind === 'user_message'

    if (removed.has(OPTIMISTIC)) {
        drop((key, item) => isRealUserMessage(key, item) || isFailedKey(key))
    }
    if ([...removed].some(isFailedKey)) {
        drop((key, item) => key === OPTIMISTIC || isRealUserMessage(key, item))
    }
    if (removed.has(STARTING)) {
        drop((key) => key === WORKING)
    }
    if (removed.has(WORKING) || removed.has(STARTING)) {
        drop((key, item) => item?.syntheticKind === STREAMING_BLOCK_KIND)
    }
    if ([...previousStreamingKeys].some((key) => keys.has(key))) {
        drop((key, item) => typeof key === 'number' && key > 0
            && STREAMED_KINDS.has(item?.kind) && !hasToolBlock(item))
    }
    candidates = candidates.filter((key) => !(isDaySeparatorKey(key) && dropped.has(keyLineNum(key))))

    // Rule 5.
    if (candidates.length > CHAT_ENTRANCE_MAX_BATCH) return result

    // Rule 6: in list order (added is), a separator takes the variant of its row.
    result.entering = candidates.map((key, index) => {
        const item = isDaySeparatorKey(key) ? byKey.get(keyLineNum(key)) : byKey.get(key)
        return { key, index, variant: variantOf(item) }
    })
    return result
}
