/** Move visible entries while retaining every hidden entry at its original index. */
export function moveVisibleItems(items, indices, from, to) {
    if (!Number.isInteger(from) || !Number.isInteger(to) || from === to
        || from < 0 || to < 0 || from >= indices.length || to >= indices.length) return false
    const visible = indices.map((index) => items[index])
    const [item] = visible.splice(from, 1)
    visible.splice(to, 0, item)
    indices.forEach((index, i) => { items[index] = visible[i] })
    return true
}

export function getDropIndex(rects, from, centerY) {
    let target = from
    for (let i = from + 1; i < rects.length; i++) {
        if (centerY >= rects[i].top + rects[i].height / 2) target = i
    }
    for (let i = from - 1; i >= 0; i--) {
        if (centerY <= rects[i].top + rects[i].height / 2) target = i
    }
    return target
}

export function getScrollStep(y, top, bottom) {
    if (bottom <= top) return 0
    const edge = Math.min(48, (bottom - top) / 3)
    if (y < top + edge) return -Math.ceil(12 * Math.min(1, (top + edge - y) / edge))
    if (y > bottom - edge) return Math.ceil(12 * Math.min(1, (y - bottom + edge) / edge))
    return 0
}

function scrollParents(element, view) {
    const parents = []
    // Follow slots into wa-dialog's shadow DOM: its body owns the scrolling.
    for (let node = element; node; node = node.assignedSlot || node.parentElement || node.getRootNode()?.host) {
        if (node.nodeType !== 1) continue
        if (/(auto|scroll)/.test(view.getComputedStyle(node).overflowY)) parents.push(node)
    }
    return parents
}

/** Pointer Events provide the same handle gesture for mouse, pen, and touch. */
export function startListReorder(event, handle, onDrop, onActive = () => {}) {
    if (event.button !== 0 || event.isPrimary === false) return () => {}
    const row = handle.closest('[data-reorder-row]')
    const list = row?.parentElement
    if (!list?.hasAttribute('data-reorder-list')) return () => {}
    const rows = Array.from(list.children).filter((child) => child.hasAttribute('data-reorder-row'))
    const from = rows.indexOf(row)
    if (from < 0 || rows.length < 2) return () => {}

    event.preventDefault()
    const doc = handle.ownerDocument
    const view = doc.defaultView
    const rects = rows.map((item) => item.getBoundingClientRect())
    const styles = rows.map((item) => item.style.cssText)
    const texts = rows.map((item) => item.textContent)
    const startListTop = list.getBoundingClientRect().top
    const grabOffset = event.clientY - rects[from].top
    const parents = scrollParents(list, view)
    let y = event.clientY
    let x = event.clientX
    let target = from
    let active = false
    let ended = false
    let frame = null

    function unchanged() {
        const current = Array.from(list.children).filter((child) => child.hasAttribute('data-reorder-row'))
        return list.isConnected && list.getClientRects().length > 0
            && current.length === rows.length
            && current.every((item, i) => item === rows[i] && item.textContent === texts[i])
    }

    function preview() {
        const delta = list.getBoundingClientRect().top - startListTop
        const adjusted = rects.map((rect) => ({ top: rect.top + delta, height: rect.height }))
        target = getDropIndex(adjusted, from, y - grabOffset + rects[from].height / 2)
        const order = rows.map((_, i) => i)
        order.splice(from, 1)
        order.splice(target, 0, from)
        let top = rects[0].top + delta
        const gap = rects.length > 1 ? rects[1].top - rects[0].bottom : 0
        // Transforms contribute to scrollHeight. Keep the dragged row inside the
        // original list so auto-scroll cannot create an endless blank tail.
        const last = adjusted.at(-1)
        const draggedTop = Math.max(adjusted[0].top, Math.min(y - grabOffset, last.top + last.height - rects[from].height))
        for (const index of order) {
            const offset = index === from ? draggedTop - adjusted[index].top : top - adjusted[index].top
            rows[index].style.transform = `translateY(${offset}px)`
            top += rects[index].height + gap
        }
    }

    function tick() {
        if (ended) return
        if (!unchanged()) { finish(false); return }
        if (active) {
            // Scroll the closest ancestor that can still move in the requested direction.
            for (const parent of parents) {
                const rect = parent.getBoundingClientRect()
                const step = getScrollStep(y, Math.max(0, rect.top), Math.min(view.innerHeight, rect.bottom))
                const before = parent.scrollTop
                parent.scrollTop += step
                if (parent.scrollTop !== before) break
            }
            preview()
        }
        frame = view.requestAnimationFrame(tick)
    }

    function move(pointer) {
        if (pointer.pointerId !== event.pointerId) return
        x = pointer.clientX
        y = pointer.clientY
        if (!active && Math.hypot(x - event.clientX, y - event.clientY) >= 5) {
            active = true
            onActive(true)
            rows.forEach((item) => { item.style.transition = 'none' })
            row.style.position = 'relative'
            row.style.zIndex = '1'
            row.style.background = 'var(--wa-color-surface-default)'
            row.style.boxShadow = '0 2px 8px rgb(0 0 0 / 20%)'
        }
        if (active) { pointer.preventDefault(); preview() }
    }

    function up(pointer) {
        if (pointer.pointerId !== event.pointerId) return
        y = pointer.clientY
        if (active && unchanged()) preview()
        finish(true)
    }

    function cancel(pointer) {
        if (pointer.pointerId === undefined || pointer.pointerId === event.pointerId) finish(false)
    }

    function keydown(key) {
        if (key.key === 'Escape') {
            key.preventDefault()
            key.stopPropagation()
            finish(false)
        }
    }

    function finish(commit) {
        if (ended) return
        ended = true
        const valid = unchanged()
        view.cancelAnimationFrame(frame)
        doc.removeEventListener('pointermove', move, true)
        doc.removeEventListener('pointerup', up, true)
        doc.removeEventListener('pointercancel', cancel, true)
        doc.removeEventListener('keydown', keydown, true)
        view.removeEventListener('blur', cancel)
        handle.removeEventListener('lostpointercapture', cancel)
        rows.forEach((item, i) => { item.style.cssText = styles[i] })
        if (handle.hasPointerCapture(event.pointerId)) handle.releasePointerCapture(event.pointerId)
        onActive(false)
        if (commit && active && valid && from !== target) onDrop(from, target)
    }

    doc.addEventListener('pointermove', move, { capture: true, passive: false })
    doc.addEventListener('pointerup', up, true)
    doc.addEventListener('pointercancel', cancel, true)
    doc.addEventListener('keydown', keydown, true)
    view.addEventListener('blur', cancel)
    handle.addEventListener('lostpointercapture', cancel)
    handle.setPointerCapture(event.pointerId)
    frame = view.requestAnimationFrame(tick)
    return () => finish(false)
}
