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
 * @property {Array<{key: *, top: number, height: number}>} [positions] - Every row, in order, as the scroller lays it out.
 */

/**
 * The row to anchor on: the first row at or below the scroller's anchor whose key passes `isAnchorKey`, at the same
 * scroll position (its offset is then negative: the row starts below the top of the view). A row that never moves on
 * an insertion (the room at the top of the list, a day separator whose new rows land below it) would make the restore
 * see "same index" and let the rows on screen shift down.
 *
 * @param {{key: *, index: number, offset: number}|null} anchor - `getScrollAnchor()`.
 * @param {Array<{key: *, top: number}>|undefined} positions
 * @param {(key: *) => boolean} isAnchorKey
 * @returns {{key: *, index: number, offset: number}|null} The anchor itself when it passes, or when no row below does.
 */
export function chooseAnchor(anchor, positions, isAnchorKey) {
    if (!anchor || !positions?.length || isAnchorKey(anchor.key)) return anchor
    const anchorTop = positions[anchor.index]?.top
    if (anchorTop === undefined) return anchor
    for (let index = anchor.index + 1; index < positions.length; index += 1) {
        const row = positions[index]
        if (isAnchorKey(row.key)) return { key: row.key, index, offset: anchor.offset - (row.top - anchorTop) }
    }
    return anchor
}

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
 * @param {Object} [options]
 * @param {(key: *) => boolean} [options.isAnchorKey] - Rows the view may be anchored on (default: every row).
 */
export function createScrollKeeper(getScroller, { isAnchorKey = () => true } = {}) {
    return {
        /** Call BEFORE the list changes (a pre-flush watcher): the scroller still lays out the old rows. */
        capture() {
            const scroller = getScroller()
            if (!scroller) return null
            return {
                atTop: scroller.getScrollState().scrollTop <= TOP_EPSILON,
                anchor: chooseAnchor(scroller.getScrollAnchor(), scroller.positions, isAnchorKey),
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
