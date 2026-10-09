// Host side of the inline artifact scroll relay (see scrollForwarder.js): applies the
// gestures the artifact could not consume to the chat scroller, with the motion a native
// scroll would have — eased steps for notched wheels, momentum after a touch fling.

/** Approximates the line height browsers use for line-based wheel deltas. */
const LINE_PX = 19
/** Time constant of the eased catch-up toward the target of a notched wheel. */
const EASE_TAU_MS = 70
/** Time constant of the momentum decay after a fling. */
const MOMENTUM_TAU_MS = 325
const MIN_FLING_VELOCITY = 0.05 // px/ms
const MAX_FLING_VELOCITY = 6 // px/ms
const STOP_VELOCITY = 0.02 // px/ms
const MAX_FRAME_MS = 64

/**
 * One driver per chat scroller. The scroller's own user-scroll listeners (virtual scroll
 * bookkeeping, auto-follow cancellation) hear the relayed gestures as synthetic events.
 */
export function createScrollDriver(container, {
    requestFrame = callback => globalThis.requestAnimationFrame(callback),
    cancelFrame = id => globalThis.cancelAnimationFrame(id),
    now = () => globalThis.performance.now(),
} = {}) {
    let frame = null
    let mode = null // 'ease' | 'momentum'
    let target = 0
    let velocity = 0
    let last = 0

    const maxTop = () => Math.max(0, container.scrollHeight - container.clientHeight)
    const clamp = value => Math.min(maxTop(), Math.max(0, value))
    function scrollBy(delta) {
        const before = container.scrollTop
        container.scrollBy({ top: delta, behavior: 'instant' })
        return container.scrollTop - before
    }
    const notify = type => container.dispatchEvent(new Event(type))

    function stop() {
        if (frame != null) cancelFrame(frame)
        frame = null
        mode = null
    }
    function start(nextMode) {
        mode = nextMode
        last = now()
        if (frame == null) frame = requestFrame(tick)
    }
    function tick() {
        frame = null
        const time = now()
        const elapsed = Math.min(MAX_FRAME_MS, Math.max(0, time - last))
        last = time
        if (mode === 'ease') {
            const remaining = target - container.scrollTop
            let step = remaining * (1 - Math.exp(-elapsed / EASE_TAU_MS))
            // A sub-pixel step may round away; never stall short of the target.
            if (Math.abs(step) < 1) step = Math.sign(remaining) * Math.min(1, Math.abs(remaining))
            const moved = scrollBy(step)
            if (Math.abs(target - container.scrollTop) < 0.5) { scrollBy(target - container.scrollTop); stop(); return }
            if (step !== 0 && moved === 0) { stop(); return }
        } else if (mode === 'momentum') {
            const wanted = velocity * elapsed
            const moved = scrollBy(wanted)
            velocity *= Math.exp(-elapsed / MOMENTUM_TAU_MS)
            if (Math.abs(velocity) < STOP_VELOCITY || (wanted !== 0 && moved === 0)) { stop(); return }
        } else return
        frame = requestFrame(tick)
    }

    // Real input on the chat itself takes the scroll back at once. Synthetic events are ours.
    const takeBack = event => { if (event.isTrusted) stop() }
    const TAKE_BACK_EVENTS = ['wheel', 'touchstart', 'pointerdown']
    for (const type of TAKE_BACK_EVENTS) container.addEventListener(type, takeBack, { passive: true })

    return {
        wheel(deltaY, deltaMode) {
            const pixels = deltaMode === 1 ? deltaY * LINE_PX
                : deltaMode === 2 ? deltaY * container.clientHeight : deltaY
            notify('wheel')
            if (deltaMode === 0) { stop(); scrollBy(pixels); return }
            // Notched wheels step in large jumps; ease through them, accumulating fast notches.
            target = clamp((mode === 'ease' ? target : container.scrollTop) + pixels)
            start('ease')
        },
        touchStart() { stop() },
        touchMove(deltaY) {
            stop()
            notify('touchmove')
            scrollBy(deltaY)
        },
        touchEnd(releaseVelocity) {
            const speed = Math.min(MAX_FLING_VELOCITY, Math.max(-MAX_FLING_VELOCITY, releaseVelocity))
            if (Math.abs(speed) < MIN_FLING_VELOCITY) return
            velocity = speed
            start('momentum')
        },
        stop,
        dispose() {
            stop()
            for (const type of TAKE_BACK_EVENTS) container.removeEventListener(type, takeBack)
        },
    }
}
