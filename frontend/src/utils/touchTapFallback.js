// Fallback for touch taps whose `click` the browser never produces (seen on
// Firefox for Android: pointerdown/up and touchstart/end fire, no click).
//
// The click stays the normal path. On a touch pointerup over the button where the
// pointerdown happened, a timer is armed. A click on that button cancels it. If the
// timer fires first, the action runs, and a late click on that button is ignored.

/**
 * @param {object} [options]
 * @param {number} [options.delay] ms to wait for a click after a touch pointerup
 * @param {number} [options.ignoreClickFor] ms during which a click is ignored after the fallback ran
 * @param {Function} [options.setTimer] defaults to setTimeout
 * @param {Function} [options.clearTimer] defaults to clearTimeout
 * @param {Function} [options.now] defaults to Date.now
 */
export function createTouchTapFallback({
    delay = 350,
    ignoreClickFor = 1000,
    setTimer = (fn, ms) => setTimeout(fn, ms),
    clearTimer = handle => clearTimeout(handle),
    now = () => Date.now(),
} = {}) {
    let downId = null
    let pending = null // { id, handle }
    let fired = null // { id, at }

    function cancel() {
        downId = null
        if (pending) {
            clearTimer(pending.handle)
            pending = null
        }
    }

    return {
        /** A pointerdown on button `id`. Cancels any pending fallback. */
        down(id, event) {
            cancel()
            if (event?.pointerType === 'touch') downId = id
        },
        /** A pointerup on button `id`: arms the fallback for a touch tap. */
        up(id, event, action) {
            if (event?.pointerType !== 'touch' || downId !== id) return
            cancel()
            const entry = { id, handle: null }
            entry.handle = setTimer(() => {
                if (pending !== entry) return
                pending = null
                fired = { id, at: now() }
                action()
            }, delay)
            pending = entry
        },
        /** A click on button `id`. Returns true when it must be handled. */
        click(id) {
            if (pending?.id === id) cancel()
            if (fired && fired.id === id && now() - fired.at < ignoreClickFor) {
                fired = null
                return false
            }
            return true
        },
        /** A pointercancel, or any reason to drop the pending fallback. */
        cancel,
    }
}
