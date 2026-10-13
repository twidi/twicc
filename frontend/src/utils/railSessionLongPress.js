const AUTO_HIDE_DELAY_MS = 3000

/** Touch previews for rail entries. Native pointer scrolling stays enabled. */
export function createRailSessionLongPress({ document, show, hide, isOpen }) {
    let pending = null
    let timer = null
    let suppressedClick = null
    let disposed = false
    let autoHideTimer = null
    let autoHideId = null
    let held = false

    function clearAutoHide(id) {
        if (id !== undefined && id !== autoHideId) return
        clearTimeout(autoHideTimer)
        autoHideTimer = null
        autoHideId = null
        held = false
    }

    function armAutoHide(id) {
        clearTimeout(autoHideTimer)
        autoHideId = id
        autoHideTimer = setTimeout(() => {
            clearAutoHide()
            hide(id)
        }, AUTO_HIDE_DELAY_MS)
    }

    // A touch inside the open preview (scrolling a long title) postpones the automatic dismissal:
    // it waits while the finger is down, then restarts after the last release or scroll event.
    function holdAutoHide(id) {
        if (id !== autoHideId) return
        held = true
        clearTimeout(autoHideTimer)
        autoHideTimer = null
    }

    function releaseAutoHide(id) {
        if (id !== autoHideId) return
        held = false
        armAutoHide(id)
    }

    function extendAutoHide(id) {
        if (id !== autoHideId || held) return
        armAutoHide(id)
    }

    function clearPending() {
        clearTimeout(timer)
        timer = null
        pending = null
    }

    function cancel() {
        clearPending()
        clearAutoHide()
        hide()
    }

    function start(id, event) {
        if (disposed || !event.isPrimary || event.button !== 0) return
        clearPending()
        // A new deliberate press cannot be the previous hold's synthetic click.
        suppressedClick = null
        if (event.pointerType !== 'touch') {
            clearAutoHide()
            return
        }
        pending = { id, source: event.currentTarget, pointerId: event.pointerId,
            x: event.clientX, y: event.clientY }
        timer = setTimeout(() => {
            const press = pending
            clearPending()
            if (!press?.source.isConnected) return
            suppressedClick = press.id
            clearAutoHide()
            if (isOpen(press.id)) {
                hide(press.id)
                return
            }
            show(press.id)
            armAutoHide(press.id)
        }, 450)
    }

    function move(event) {
        if (!pending || event.pointerId !== pending.pointerId) return
        if (Math.abs(event.clientX - pending.x) > 10 || Math.abs(event.clientY - pending.y) > 10) clearPending()
    }

    function finish(event) {
        if (pending && event.pointerId === pending.pointerId) clearPending()
    }

    function anotherPointer(event) {
        if (pending && event.pointerId !== pending.pointerId) clearPending()
    }

    function consumeClick(id, event) {
        if (event.detail === 0 || suppressedClick !== id) return false
        suppressedClick = null
        event.preventDefault()
        event.stopPropagation()
        return true
    }

    const listeners = [
        ['pointermove', move], ['pointerup', finish], ['pointercancel', finish],
        ['pointerdown', anotherPointer], ['scroll', clearPending],
    ]
    for (const [type, listener] of listeners) document.addEventListener(type, listener, { capture: true, passive: true })

    function dispose() {
        cancel()
        disposed = true
        suppressedClick = null
        for (const [type, listener] of listeners) document.removeEventListener(type, listener, { capture: true })
    }

    return { start, consumeClick, cancel, dispose, clearAutoHide, holdAutoHide, releaseAutoHide, extendAutoHide }
}
