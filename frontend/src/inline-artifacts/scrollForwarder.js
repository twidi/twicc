// Iframe side of the inline artifact scroll relay.
//
// An inline artifact iframe lives in FrameHost, outside the chat scroller's DOM subtree
// (a retained iframe must never be re-parented). Native scroll chaining therefore cannot
// reach the chat: a wheel or a touch over the artifact would be swallowed even when the
// artifact has nothing left to scroll. This relay watches the gestures inside the artifact
// document and hands the ones nothing in the artifact can consume to the host, which scrolls
// the chat. The artifact scrolls first; the chat takes over at the boundary, like native.

/** Movement before a touch gesture is classified as a vertical pan or a tap. */
const TOUCH_SLOP_PX = 8
/** Only the most recent movement defines the fling velocity. */
const VELOCITY_WINDOW_MS = 100
/** Screen/client scale outside this range is not a unit difference: ignore it. */
const MIN_SCREEN_SCALE = 0.5
const MAX_SCREEN_SCALE = 4
/** One pixel of tolerance for fractional scroll positions. */
const EDGE_TOLERANCE_PX = 1

const SCROLLABLE_OVERFLOW = new Set(['auto', 'scroll', 'overlay'])
const CONTAINED_OVERSCROLL = new Set(['contain', 'none'])

const isElement = node => node?.nodeType === 1

/**
 * True when a vertical scroll in `direction` (1 = down, -1 = up) started on `start` is not
 * consumed anywhere in the artifact document and the author did not block chaining.
 */
export function canChainVertically(window, document, start, direction) {
    const root = document.documentElement
    const body = document.body
    for (let el = isElement(start) ? start : start?.parentElement; isElement(el); el = el.parentElement) {
        // html and body are evaluated together below: their overflow belongs to the viewport.
        if (el === root || el === body) break
        const style = window.getComputedStyle(el)
        if (!SCROLLABLE_OVERFLOW.has(style.overflowY) || !(el.scrollHeight > el.clientHeight)) continue
        const hasRoom = direction > 0
            ? el.scrollTop + el.clientHeight < el.scrollHeight - EDGE_TOLERANCE_PX
            : el.scrollTop > EDGE_TOLERANCE_PX
        if (hasRoom) return false
        if (CONTAINED_OVERSCROLL.has(style.overscrollBehaviorY)) return false
    }
    const rootStyle = window.getComputedStyle(root)
    const bodyStyle = body ? window.getComputedStyle(body) : null
    // A visible root overflow hands the body's value to the viewport.
    const viewportOverflow = rootStyle.overflowY !== 'visible' ? rootStyle.overflowY : bodyStyle?.overflowY
    if (viewportOverflow !== 'hidden' && viewportOverflow !== 'clip') {
        const scroller = document.scrollingElement || root
        const hasRoom = direction > 0
            ? scroller.scrollTop + scroller.clientHeight < scroller.scrollHeight - EDGE_TOLERANCE_PX
            : scroller.scrollTop > EDGE_TOLERANCE_PX
        if (hasRoom) return false
    }
    return !CONTAINED_OVERSCROLL.has(rootStyle.overscrollBehaviorY)
}

/** False when an ancestor opted out of vertical panning (`touch-action: none` or horizontal only). */
export function allowsVerticalPan(window, start) {
    for (let el = isElement(start) ? start : start?.parentElement; isElement(el); el = el.parentElement) {
        const touchAction = window.getComputedStyle(el).touchAction || 'auto'
        if (touchAction === 'auto' || touchAction === 'manipulation') continue
        if (!/pan-y|pan-up|pan-down/.test(touchAction)) return false
    }
    return true
}

/**
 * Where the finger is on the screen. The iframe moves under the finger while the chat scrolls,
 * so its own clientY would feed that movement back into the next delta and make the scroll
 * oscillate, more as the finger goes faster. Screen coordinates do not depend on the iframe.
 */
const screenY = touch => Number.isFinite(touch.screenY) ? touch.screenY : touch.clientY

/**
 * Screen pixels per client pixel, measured on the slop travel, while the iframe is still:
 * browsers disagree on the unit of screen coordinates.
 */
export function screenScale(startTouch, touch) {
    const client = touch.clientY - startTouch.clientY
    if (Math.abs(client) < TOUCH_SLOP_PX / 2) return 1
    const scale = (screenY(touch) - screenY(startTouch)) / client
    return scale >= MIN_SCREEN_SCALE && scale <= MAX_SCREEN_SCALE ? scale : 1
}

/** Pixels per millisecond over the last moments of the gesture, positive = scroll down. */
export function releaseVelocity(samples, endTime) {
    const recent = samples.filter(sample => endTime - sample.time <= VELOCITY_WINDOW_MS)
    if (recent.length < 2) return 0
    const span = recent[recent.length - 1].time - recent[0].time
    if (!(span > 0)) return 0
    // The first sample's distance happened before the window started.
    return recent.slice(1).reduce((sum, sample) => sum + sample.delta, 0) / span
}

export function createInlineScrollForwarder({ document, window, getHost, inlineGeneration }) {
    if (inlineGeneration == null) return { dispose() {} }
    const send = payload => {
        getHost().then(host => host.forwardInlineScroll({ inlineGeneration, ...payload })).catch(() => {})
    }

    function wheel(event) {
        if (event.defaultPrevented || event.ctrlKey || event.shiftKey || !event.deltaY
            || Math.abs(event.deltaX) > Math.abs(event.deltaY)) return
        if (!canChainVertically(window, document, event.target, Math.sign(event.deltaY))) return
        // Nothing in the artifact scrolls; keep the gesture from chaining anywhere else.
        if (event.cancelable) event.preventDefault()
        send({ kind: 'wheel', deltaY: event.deltaY, deltaMode: event.deltaMode })
    }

    // The relay decision is taken once, when the finger leaves the tap slop, and holds for the
    // whole gesture, like the browser's own scroll latching.
    let gesture = null
    function finish(velocity) {
        if (gesture?.state === 'forward') send({ kind: 'touchend', velocity })
        gesture = null
    }
    function touchstart(event) {
        if (event.touches.length !== 1) {
            finish(0)
            gesture = { state: 'native' }
            return
        }
        const touch = event.touches[0]
        gesture = { state: 'pending', start: { clientY: touch.clientY, screenY: touch.screenY },
            x: touch.clientX, y: touch.clientY, target: event.target, samples: [] }
        // A finger landing on the artifact stops a fling still running on the chat.
        send({ kind: 'touchstart' })
    }
    function touchmove(event) {
        if (!gesture || gesture.state === 'native') return
        const touch = event.touches[0]
        if (!touch) return
        if (gesture.state === 'pending') {
            const dx = touch.clientX - gesture.x, dy = touch.clientY - gesture.y
            if (Math.hypot(dx, dy) < TOUCH_SLOP_PX) return
            // Finger moving up scrolls the content down.
            const relay = Math.abs(dy) > Math.abs(dx) && !event.defaultPrevented
                && allowsVerticalPan(window, gesture.target)
                && canChainVertically(window, document, gesture.target, dy < 0 ? 1 : -1)
            if (!relay) { gesture.state = 'native'; return }
            gesture.state = 'forward'
            gesture.scale = screenScale(gesture.start, touch)
            gesture.y = screenY(touch) // the slop travel is not replayed
        }
        const delta = (gesture.y - screenY(touch)) / gesture.scale
        gesture.y = screenY(touch)
        gesture.samples.push({ time: event.timeStamp, delta })
        if (event.cancelable) event.preventDefault()
        if (delta) send({ kind: 'touchmove', deltaY: delta })
    }
    function touchend(event) {
        finish(event.type === 'touchend' ? releaseVelocity(gesture?.samples || [], event.timeStamp) : 0)
    }

    window.addEventListener('wheel', wheel, { passive: false })
    window.addEventListener('touchstart', touchstart, { passive: true })
    window.addEventListener('touchmove', touchmove, { passive: false })
    window.addEventListener('touchend', touchend, { passive: true })
    window.addEventListener('touchcancel', touchend, { passive: true })
    return {
        dispose() {
            window.removeEventListener('wheel', wheel)
            window.removeEventListener('touchstart', touchstart)
            window.removeEventListener('touchmove', touchmove)
            window.removeEventListener('touchend', touchend)
            window.removeEventListener('touchcancel', touchend)
        },
    }
}
