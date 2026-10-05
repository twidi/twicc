/** Touch previews for rail session buttons. Native pointer scrolling stays enabled. */
export function createRailSessionLongPress({ document, show, hide }) {
    let pending = null
    let timer = null
    let suppressedClick = null
    let disposed = false

    function clearPending() {
        clearTimeout(timer)
        timer = null
        pending = null
    }

    function cancel() {
        clearPending()
        hide()
    }

    function start(id, event) {
        if (disposed || !event.isPrimary || event.button !== 0) return
        clearPending()
        // A new deliberate press cannot be the previous hold's synthetic click.
        suppressedClick = null
        if (event.pointerType !== 'touch') return
        pending = { id, source: event.currentTarget, pointerId: event.pointerId,
            x: event.clientX, y: event.clientY }
        timer = setTimeout(() => {
            const press = pending
            clearPending()
            if (!press?.source.isConnected) return
            suppressedClick = press.id
            show(press.id)
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

    return { start, consumeClick, cancel, dispose }
}
