// Group reveal (visual refresh retouches): the rows a group of hidden elements adds to the chat list when
// the user opens it. Pure; the Vue side is composables/useGroupReveal.js, the animation is
// styles/group-reveal.css. The block opens from 0 to its height like a curtain, top to bottom: each row
// keeps its final vertical position, its space opens in its turn (one segment each), and its content
// slides in from outside the chat, one row after the other.
/** A removal this long after a group was closed counts as that collapse (more than the exit animation). */
export const GROUP_REVEAL_WINDOW_MS = 400
/** The slide (and fade) of one row. */
export const GROUP_REVEAL_SLIDE_MS = 320
const SEGMENT_MAX_MS = 60
const CURTAIN_MAX_MS = 400

/**
 * The rows of `items` that are new since `previousKeys`, in list order, indexed from 1 (the head's own
 * item is index 0). `count` = rows + the head item.
 */
export function planGroupReveal({ previousKeys, items, getKey }) {
    const rows = items.map(getKey).filter((key) => !previousKeys.has(key)).map((key, i) => ({ key, index: i + 1 }))
    return { rows, count: rows.length + 1 }
}

/** The time one row's space takes to open: a segment of the curtain, which never lasts more than CURTAIN_MAX_MS. */
export function groupRevealSegmentMs(count) {
    return Math.min(SEGMENT_MAX_MS, CURTAIN_MAX_MS / count)
}

/** Milliseconds from the start until the last row has finished sliding in. */
export function groupRevealEndMs(count) {
    return (count - 1) * groupRevealSegmentMs(count) + GROUP_REVEAL_SLIDE_MS
}
