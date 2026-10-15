// frontend/src/utils/timeline/scrollKeeper.js
//
// Keeps the view still when rows are inserted ABOVE it (display design section 9.2, "Scroll"):
//   * at the top, new rows simply appear at the top;
//   * scrolled down, the content on screen must not move.
// The simple virtual scroller has no insertion anchoring of its own, so the page captures the first visible
// row before the list changes and puts it back afterwards. Pure: the scroller is handed in.

/** Within this many pixels of the top the user counts as "at the top". */
export const TOP_EPSILON = 2

/**
 * @typedef {Object} ScrollerApi
 * @property {() => {scrollTop: number}} getScrollState
 * @property {() => ({key: *, index: number, offset: number}|null)} getScrollAnchor
 * @property {(anchor: Object) => void} scrollToAnchor
 * @property {() => void} scrollToTop
 */

/**
 * What to do once the new list is rendered.
 *
 * @param {{atTop: boolean, anchor: (Object|null)}|null} snapshot - Taken before the change.
 * @param {Map<*, number>} indexByKey - Position of every row of the new list.
 * @returns {'none'|'anchor'|'top'}
 */
export function planScrollRestore(snapshot, indexByKey) {
    if (!snapshot || snapshot.atTop || !snapshot.anchor) return 'none'
    const index = indexByKey.get(snapshot.anchor.key)
    // The row the user was reading is gone (another scope, a reload): start from the top.
    if (index === undefined) return 'top'
    // Same position: rows were added below (an older page), nothing moved. Do not touch the scroll then, the
    // user may be scrolling at this very moment.
    return index === snapshot.anchor.index ? 'none' : 'anchor'
}

/**
 * @param {() => (ScrollerApi|null|undefined)} getScroller
 */
export function createScrollKeeper(getScroller) {
    return {
        /** Call BEFORE the list changes (a pre-flush watcher). */
        capture() {
            const scroller = getScroller()
            if (!scroller) return null
            return {
                atTop: scroller.getScrollState().scrollTop <= TOP_EPSILON,
                anchor: scroller.getScrollAnchor(),
            }
        },

        /** Call AFTER the new list is rendered. */
        restore(snapshot, indexByKey) {
            const scroller = getScroller()
            if (!scroller) return
            const action = planScrollRestore(snapshot, indexByKey)
            if (action === 'anchor') scroller.scrollToAnchor(snapshot.anchor)
            else if (action === 'top') scroller.scrollToTop()
        },
    }
}
