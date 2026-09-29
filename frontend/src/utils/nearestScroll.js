// The minimal scroll that brings a row into a zone of its scroll container (visual refresh
// step 6a-bis §10, docs/plans/2026-09-29-sidebar-row-glide-design.md): the one computation
// the virtual scroller (align 'nearest') and the artifacts list share. Pure numbers. And the
// smooth reveal (§10.7): when a reveal scrolls smoothly, and the smooth scroll itself.

/** How long a smooth reveal may take before it counts as ended (a missing scrollend). */
export const SMOOTH_SCROLL_TIMEOUT_MS = 600

/**
 * The scrollTop that brings the row [top, top + height] inside the zone: the viewport minus
 * `marginTop` at the top and `marginBottom` at the bottom (a negative margin widens it).
 *
 * In order: a row inside the zone keeps the current scroll (returned as is, so the caller
 * can skip the write); a row taller than the zone brings its bottom to the bottom edge but
 * never scrolls past its own top; a row above comes to the top edge; a row below comes to
 * the bottom edge. The target is then clamped to [0, scrollMax].
 *
 * @param {Object} geometry - All in px, in one coordinate space (the scroll content)
 * @param {number} geometry.top - The row's top
 * @param {number} geometry.height - The row's height
 * @param {number} geometry.scrollTop - The current scroll
 * @param {number} geometry.viewport - The visible height (the container's clientHeight)
 * @param {number} geometry.scrollMax - The real end of the scroll range
 * @param {number} [geometry.marginTop=0] - The top band
 * @param {number} [geometry.marginBottom=0] - The bottom band
 * @returns {number} The target scrollTop
 */
export function nearestScrollTop({ top, height, scrollTop, viewport, scrollMax, marginTop = 0, marginBottom = 0 }) {
    const bottom = top + height
    const viewTop = scrollTop + marginTop
    const viewBottom = scrollTop + viewport - marginBottom
    if (top >= viewTop && bottom <= viewBottom) return scrollTop

    let target
    if (height > viewport - marginTop - marginBottom) {
        target = Math.min(bottom - viewport + marginBottom, top)
    } else if (top < viewTop) {
        target = top - marginTop
    } else {
        target = bottom - viewport + marginBottom
    }
    return Math.max(0, Math.min(target, Math.max(0, scrollMax)))
}

/**
 * How a reveal scrolls: smooth for a short move (at most one viewport) when allowed and
 * motion is not reduced; instant otherwise, a zero move included.
 *
 * @param {Object} reveal
 * @param {number} reveal.distance - |target - current|, px
 * @param {number} reveal.viewport - The longest smooth move, px
 * @param {boolean} reveal.reduced - The user prefers reduced motion
 * @param {boolean} reveal.allowed - The list allows a smooth reveal now
 * @returns {'smooth' | 'auto'}
 */
export function revealBehavior({ distance, viewport, reduced, allowed }) {
    return allowed && !reduced && distance > 0 && distance <= viewport ? 'smooth' : 'auto'
}

/**
 * Start a smooth scroll of `container` to `target` and race its end: the container's
 * `scrollend` once it sits at the target (within 1px; an earlier one, away from it, is
 * ignored), or SMOOTH_SCROLL_TIMEOUT_MS (a missing event, a no-op scroll). The listener
 * and the timer are cleaned up on every path.
 *
 * @param {HTMLElement} container - The scroll container
 * @param {number} target - The scrollTop to reach
 * @param {Object} [env=globalThis] - setTimeout / clearTimeout
 * @returns {{ done: Promise<void>, cancel: () => void }} `done` resolves at the end or on
 *   `cancel()`, which also cleans up
 */
export function startSmoothScroll(container, target, env = globalThis) {
    let resolveDone
    const done = new Promise((resolve) => { resolveDone = resolve })
    let finished = false
    let timer = null

    function finish() {
        if (finished) return
        finished = true
        container.removeEventListener('scrollend', onScrollEnd)
        env.clearTimeout(timer)
        resolveDone()
    }

    function onScrollEnd() {
        if (Math.abs(container.scrollTop - target) <= 1) finish()
    }

    container.addEventListener('scrollend', onScrollEnd)
    timer = env.setTimeout(finish, SMOOTH_SCROLL_TIMEOUT_MS)
    container.scrollTo({ top: target, behavior: 'smooth' })
    return { done, cancel: finish }
}
