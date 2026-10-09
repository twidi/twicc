/** One event-driven measurement pass per owning view. No background polling. */
export function createInlineGeometryScheduler({ pool,
    requestFrame = callback => globalThis.requestAnimationFrame?.(callback),
    cancelFrame = id => globalThis.cancelAnimationFrame?.(id),
    viewport = () => ({ x: 0, y: 0, width: globalThis.innerWidth || 0, height: globalThis.innerHeight || 0 }),
}) {
    const owners = new Map()
    let pending = null
    let disposed = false
    const rect = element => {
        const box = element?.getBoundingClientRect?.()
        return box ? { x: box.x, y: box.y, width: box.width, height: box.height } : null
    }
    const intersect = (a, b) => {
        const x = Math.max(a.x, b.x), y = Math.max(a.y, b.y)
        return { x, y, width: Math.max(0, Math.min(a.x + a.width, b.x + b.width) - x),
            height: Math.max(0, Math.min(a.y + a.height, b.y + b.height) - y) }
    }
    function patch(id, owner, fields) {
        owner.onGeometry?.(fields)
        pool.patch(id, fields)
    }
    function hide(id, attachment) {
        returnInlineFocus(pool, id, attachment)
        patch(id, owners.get(id), { visible: false })
    }
    function measure() {
        pending = null
        if (disposed) return
        const screen = viewport()
        for (const [id, owner] of owners) {
            const attachment = owner.getAttachment?.()
            if (!owner.isVisible() || owner.isSuppressed?.()) { hide(id, attachment); continue }
            if (owner.isFullscreen?.()) {
                patch(id, owner, { visible: true, zTier: 'fullscreen', clipRect: null, cardRect: null,
                    rect: screen })
                continue
            }
            if (!attachment || attachment.isSuppressed?.()) { hide(id, attachment); continue }
            const box = rect(attachment.placeholderEl)
            if (!box) { hide(id, attachment); continue }
            const clip = rect(attachment.clipEl)
            const clipped = clip ? intersect(screen, clip) : screen
            const visible = intersect(box, clipped)
            if (visible.width <= 0 || visible.height <= 0) { hide(id, attachment); continue }
            const card = attachment.placeholderEl?.closest?.('.panel-card')
            patch(id, owner, { rect: box, clipRect: clipped, cardRect: rect(card), visible: true,
                zTier: attachment.isElevated?.() ? 'overlay' : 'base' })
        }
    }
    function schedule() {
        if (disposed || pending != null || !owners.size) return
        pending = requestFrame(measure) ?? null
    }
    function detach(id) { owners.delete(id) }
    function attach(id, owner) { owners.set(id, owner); schedule(); return () => detach(id) }
    function dispose() {
        disposed = true
        if (pending != null) cancelFrame(pending)
        pending = null
        owners.clear()
    }
    return { attach, detach, schedule, dispose }
}

/** Return focus before a retained iframe becomes hidden. */
export function returnInlineFocus(pool, id, attachment) {
    const iframe = pool.frameEl(id)
    if (iframe && iframe.ownerDocument?.activeElement === iframe) attachment?.focusConversation?.()
}

/** Scroll containers usually have no tabindex, so focus() alone does nothing. */
export function focusInlineConversation(element) {
    if (!element?.isConnected) return
    if (!element.hasAttribute('tabindex')) element.setAttribute('tabindex', '-1')
    element.focus({ preventScroll: true })
}
