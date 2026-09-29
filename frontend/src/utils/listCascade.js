// Sidebar list cascade (visual refresh step 5c): which rows enter when a list arrives, and
// which rows enter live. Pure: no Vue, no DOM. The Vue side is composables/useListCascade.js.
// Design: docs/plans/2026-09-29-list-cascade-scheme-reveal-design.md §4 (artifacts list:
// docs/plans/2026-09-29-accent-glow-design.md §17.4).
//
// Only the rows on screen at the start cascade, with a capped index: the rows past the
// cap share the last delay. A live entrance is a row noted live that is new in the list.

export const LIST_CASCADE_STAGGER_MS = 22
export const LIST_CASCADE_DURATION_MS = 320
export const LIST_CASCADE_MAX_INDEX = 20
export const LIST_CASCADE_SETTLE_CAP_MS = 300

const END_MARGIN_MS = 100

/** Entrance index per key for the rows on screen: Map<key, index>. */
export function planListCascade({ keys, visibleStart, visibleEnd, maxIndex = LIST_CASCADE_MAX_INDEX }) {
    const plan = new Map()
    if (!(visibleEnd > visibleStart) || visibleStart >= keys.length) return plan
    const end = Math.min(visibleEnd, keys.length)
    for (let i = Math.max(visibleStart, 0); i < end; i++) {
        plan.set(keys[i], Math.min(i - visibleStart, maxIndex))
    }
    return plan
}

/**
 * Visible range of a list without a virtual scroller: the rows whose rect crosses the
 * view's rect vertically, as { start, end } with `end` exclusive (the range contract of
 * planListCascade and VirtualScroller.getVisibleRange). { start: 0, end: 0 } when none does.
 * `itemRects` are in list order; each rect needs `top` and `bottom`.
 */
export function visibleIndexRange(itemRects, viewRect) {
    let start = -1
    let end = 0
    for (let i = 0; i < itemRects.length; i++) {
        const rect = itemRects[i]
        if (rect.bottom > viewRect.top && rect.top < viewRect.bottom) {
            if (start < 0) start = i
            end = i + 1
        }
    }
    return start < 0 ? { start: 0, end: 0 } : { start, end }
}

/** Keys that enter live: noted live, absent before, present now (list order). */
export function pickLiveEntrances({ previousKeys, keys, liveKeys }) {
    if (!liveKeys.size) return []
    return keys.filter((key) => liveKeys.has(key) && !previousKeys.has(key))
}

/** Milliseconds from the start until the last entrance of `maxIndexUsed` has ended. */
export function listCascadeEndMs(maxIndexUsed) {
    return maxIndexUsed * LIST_CASCADE_STAGGER_MS + LIST_CASCADE_DURATION_MS + END_MARGIN_MS
}
