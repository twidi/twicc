// Home card cascade (visual refresh step 7b): which cards of a batch enter, with which index.
// Pure: no Vue, no DOM. The Vue side is composables/useHomeCardCascade.js.
// Design: docs/plans/2026-09-30-home-motion-design.md §3.
//
// Only the cards on screen cascade, with a capped index: the cards past the cap share the
// last delay. The timings match the .home-card-entering rule (styles/motion.css) and the
// sparkline reveal (components/activity/ActivitySparkline.vue).

import { visibleIndexRange } from './listCascade.js'

export const HOME_CARD_STAGGER_MS = 60
export const HOME_CARD_DURATION_MS = 420
export const HOME_SPARKLINE_REVEAL_MS = 800
export const HOME_CARD_MAX_INDEX = 10

const END_MARGIN_MS = 100

/**
 * Entrance index per card, for `rects` in document order (each needs `top` and `bottom`):
 * an array of the same length, the index for a card on screen, null for the others.
 */
export function planHomeCardCascade(rects, viewRect, maxIndex = HOME_CARD_MAX_INDEX) {
    const { start, end } = visibleIndexRange(rects, viewRect)
    return rects.map((_, i) => (i >= start && i < end ? Math.min(i - start, maxIndex) : null))
}

/** Milliseconds from the start until the entrance of `index` (card and sparkline) has ended. */
export function homeCardEndMs(index) {
    return index * HOME_CARD_STAGGER_MS + Math.max(HOME_CARD_DURATION_MS, HOME_SPARKLINE_REVEAL_MS) + END_MARGIN_MS
}
