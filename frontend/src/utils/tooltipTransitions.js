const installed = new WeakSet()

/**
 * Web Awesome finishes every hide animation by hiding the popup, even after a
 * reopen. Run one native lifecycle at a time and then process the latest open
 * state. Keep its events, animations, positioning and dismissal registration.
 * Public show/hide requests wait for that final state, including when a later
 * request cancels their intermediate transition.
 */
export function serializeTooltipTransitions(el) {
    if (installed.has(el)) return
    const native = el.handleOpenChange
    if (typeof native !== 'function') {
        throw new Error('serializeTooltipTransitions: wa-tooltip.handleOpenChange is unavailable')
    }
    if (!el.eventController?.signal) {
        throw new Error('serializeTooltipTransitions: wa-tooltip.eventController is unavailable')
    }
    let running = null
    let applied = el.open
    // A tooltip mounted with open already true still needs native registration.
    let appliedSignal = null
    el.handleOpenChange = function () {
        if (!running) {
            // Reserve the runner before invoking native event handlers.
            running = Promise.resolve().then(async () => {
                try {
                    while (el.isConnected && (el.open !== applied || (el.open && el.eventController.signal !== appliedSignal))) {
                        const requested = el.open
                        // Native disconnect aborts this controller and clears dismissal
                        // registration. A new connection must run show again.
                        const signal = el.eventController.signal
                        await native.call(el)
                        applied = requested
                        appliedSignal = signal.aborted ? null : signal
                    }
                } finally {
                    running = null
                }
            })
        }
        return running
    }
    el.show = async function () {
        if (el.open) return
        el.open = true
        return el.handleOpenChange()
    }
    el.hide = async function () {
        if (!el.open) return
        el.open = false
        return el.handleOpenChange()
    }
    installed.add(el)
}
