import { getReorderScrollParents, getScrollStep } from './listReorder.js'
import { getGroupItems, isSnippetGroup } from './snippetGroups.js'

/** Lists use insertion indices before the source item is removed. */
export function getGroupedDropTarget(lists, y, isGroup = false, outside = false) {
    const root = lists.find((list) => !list.groupId)
    if (!root) return null
    if (outside) return { groupId: null, index: root.rows.length }
    const list = !isGroup && lists.find((list) => list.groupId && y >= list.top && y <= list.bottom) || root
    const index = list.rows.findIndex((row) => y < row.top + row.height / 2)
    return { groupId: list.groupId || null, index: index < 0 ? list.rows.length : index }
}

/** Move only through the grip, with one gesture for mouse, touch, and pen. */
export function startGroupedReorder(event, handle, getEntries, onDrop, onActive = () => {}, onHoverGroup = () => {}) {
    if (event.button !== 0 || event.isPrimary === false) return () => {}
    const row = handle.closest('[data-group-row]')
    const editor = row?.closest('[data-group-editor]')
    if (!editor) return () => {}
    const entries = typeof getEntries === 'function' ? getEntries() : getEntries
    const from = { groupId: row.parentElement.dataset.groupId || null, index: Number(row.dataset.index) }
    const item = getGroupItems(entries, from.groupId)?.[from.index]
    if (!item) return () => {}
    const initial = JSON.stringify(entries)
    const doc = handle.ownerDocument
    const view = doc.defaultView
    const parents = getReorderScrollParents(editor, view)
    const originalStyle = row.style.cssText
    const originalRect = row.getBoundingClientRect()
    const offset = event.clientY - originalRect.top
    let x = event.clientX
    let y = event.clientY
    let active = false
    let ended = false
    let frame
    let ghost
    let marker
    let target = null

    function unchanged() {
        return editor.isConnected && editor.getClientRects().length > 0 && (typeof getEntries !== 'function' || getEntries() === entries) && initial === JSON.stringify(entries)
    }

    function preview() {
        ghost.style.top = `${y - offset}px`
        ghost.style.left = `${x - (event.clientX - originalRect.left)}px`
        const rect = editor.getBoundingClientRect()
        let hoveredGroup = null
        if (!isSnippetGroup(item) && x >= rect.left - 32 && x <= rect.right + 32) {
            const header = Array.from(editor.querySelectorAll('[data-group-header]')).find((element) => {
                const bounds = element.getBoundingClientRect()
                return element.dataset.groupHeader && y >= bounds.top && y <= bounds.bottom
            })
            hoveredGroup = header?.dataset.groupHeader || null
            if (hoveredGroup) onHoverGroup(hoveredGroup)
        }
        const elements = Array.from(editor.querySelectorAll('[data-group-list]'))
            .filter((element) => element.style?.display !== 'none' || element.dataset.groupId === hoveredGroup)
        const lists = elements.map((element) => {
            const rect = element.getBoundingClientRect()
            const header = element.dataset.groupId && element.parentElement.querySelector('[data-group-header]')
            // Vue opens a hovered group on the next render. Its header already
            // accepts a drop at the start before that render completes.
            const collapsed = element.style?.display === 'none'
            const headerRect = header ? header.getBoundingClientRect() : null
            return {
                groupId: element.dataset.groupId || null,
                top: headerRect?.top ?? rect.top,
                bottom: collapsed ? headerRect.bottom : rect.bottom,
                rows: (collapsed ? [] : Array.from(element.children)).filter((child) => child.hasAttribute('data-group-row'))
                    .map((child) => child.getBoundingClientRect()),
            }
        })
        const outside = editor.querySelector('[data-group-outside]')?.getBoundingClientRect()
        // A vertical gesture can enter a group even when its grip is indented.
        target = x >= rect.left - 32 && x <= rect.right + 32
            && y >= rect.top - 32 && y <= rect.bottom + 32
            ? getGroupedDropTarget(lists, y, isSnippetGroup(item), outside && y >= outside.top && y <= outside.bottom)
            : null
        marker.style.display = target ? 'block' : 'none'
        if (!target) return
        const listIndex = lists.findIndex((list) => list.groupId === target.groupId)
        const list = lists[listIndex]
        const element = elements[listIndex]
        // A collapsed list has no visible content geometry until Vue renders it.
        if (element.style?.display === 'none') { marker.style.display = 'none'; return }
        const listRect = element.getBoundingClientRect()
        const top = list.rows[target.index]?.top ?? list.rows.at(-1)?.bottom ?? listRect.top + 8
        marker.style.top = `${top}px`
        marker.style.left = `${listRect.left}px`
        marker.style.width = `${listRect.width}px`
    }

    function makePreview() {
        ghost = row.cloneNode(true)
        for (const element of [ghost, ...ghost.querySelectorAll('[id]')]) element.removeAttribute('id')
        ghost.inert = true
        ghost.setAttribute('aria-hidden', 'true')
        ghost.setAttribute('popover', 'manual')
        ghost.style.cssText = `position:fixed;margin:0;inset:auto;box-sizing:border-box;width:${originalRect.width}px;max-height:240px;overflow:hidden;pointer-events:none;opacity:.9;background:var(--wa-color-surface-default);box-shadow:0 4px 16px #0004;`
        doc.body.append(ghost)
        ghost.showPopover()
        marker = doc.createElement('div')
        marker.setAttribute('popover', 'manual')
        marker.setAttribute('aria-hidden', 'true')
        marker.style.cssText = 'position:fixed;margin:0;inset:auto;padding:0;height:3px;border:0;background:var(--wa-color-brand-fill-loud);pointer-events:none;'
        doc.body.append(marker)
        marker.showPopover()
        row.style.opacity = '.3'
    }

    function tick() {
        if (ended) return
        if (!unchanged()) { finish(false); return }
        if (active) {
            for (const parent of parents) {
                const rect = parent.getBoundingClientRect()
                const before = parent.scrollTop
                parent.scrollTop += getScrollStep(y, Math.max(0, rect.top), Math.min(view.innerHeight, rect.bottom))
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
            makePreview()
            onActive(true)
        }
        if (active) { pointer.preventDefault(); preview() }
    }

    function up(pointer) {
        if (pointer.pointerId !== event.pointerId) return
        x = pointer.clientX
        y = pointer.clientY
        if (active && unchanged()) preview()
        finish(true)
    }

    function cancel(pointer) {
        if (pointer.pointerId === undefined || pointer.pointerId === event.pointerId) finish(false)
    }

    function keydown(key) {
        if (key.key !== 'Escape') return
        key.preventDefault()
        key.stopPropagation()
        finish(false)
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
        ghost?.remove()
        marker?.remove()
        row.style.cssText = originalStyle
        if (handle.hasPointerCapture(event.pointerId)) handle.releasePointerCapture(event.pointerId)
        onActive(false)
        if (commit && active && valid && target) onDrop(from, target)
    }

    event.preventDefault()
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
