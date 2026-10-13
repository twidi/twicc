// Touch scrolling of a one-line title that does not fit its box (session header).
//
// The title scrolls under the finger and keeps its position. Once the finger is up and the
// inertia is over, a timer starts; when it fires, the title scrolls back to its start. A new
// touch cancels the timer and the return.
import { isReducedMotion } from './reducedMotion.js'

/** Wait, after the last touch or scroll event, before the title goes back to its start. */
export const RETURN_DELAY_MS = 1000
/** Duration of the scroll back to the start. */
export const RETURN_DURATION_MS = 300

export function easeOutCubic(t) {
    return 1 - (1 - t) ** 3
}

/**
 * Which edges of a scroller hide some content.
 * `tolerance` absorbs the sub-pixel rounding of scrollLeft.
 */
export function hiddenEdges({ scrollLeft, scrollWidth, clientWidth }, tolerance = 1) {
    return {
        start: scrollLeft > tolerance,
        end: scrollLeft + clientWidth < scrollWidth - tolerance,
    }
}

/**
 * The scroll-back controller of one scroller element. The caller forwards the element's
 * `touchstart`, `touchend`/`touchcancel` and `scroll` events to the three handlers.
 */
export function createTitleScrollReturn(el, env = globalThis, { delay = RETURN_DELAY_MS, duration = RETURN_DURATION_MS } = {}) {
    let touching = false
    let timer = null
    let frame = null

    const cancelTimer = () => {
        if (timer !== null) env.clearTimeout(timer)
        timer = null
    }

    const cancelAnimation = () => {
        if (frame !== null) env.cancelAnimationFrame(frame)
        frame = null
    }

    const animateBack = () => {
        const from = el.scrollLeft
        if (isReducedMotion(env)) {
            el.scrollLeft = 0
            return
        }
        let startTime = null
        const step = (now) => {
            startTime ??= now
            const progress = Math.min(1, (now - startTime) / duration)
            el.scrollLeft = from * (1 - easeOutCubic(progress))
            frame = progress < 1 ? env.requestAnimationFrame(step) : null
        }
        frame = env.requestAnimationFrame(step)
    }

    // The timer restarts on every event: it fires only when the finger is up and nothing moves.
    const arm = () => {
        cancelTimer()
        // The scroll events of the return itself must not restart it.
        if (touching || frame !== null || el.scrollLeft <= 0) return
        timer = env.setTimeout(() => {
            timer = null
            animateBack()
        }, delay)
    }

    return {
        onTouchStart() {
            touching = true
            cancelTimer()
            cancelAnimation()
        },
        onTouchEnd() {
            touching = false
            arm()
        },
        onScroll: arm,
        /** Back to the start now, without animation (the title changed). */
        reset() {
            touching = false
            cancelTimer()
            cancelAnimation()
            el.scrollLeft = 0
        },
        destroy() {
            cancelTimer()
            cancelAnimation()
        },
    }
}
