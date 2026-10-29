// In-page element picker, shared by two run contexts:
// - the browser companion (browser-companion/companion.js) runs it INSIDE the
//   user's own page and drives it over postMessage from the Browser pane;
// - the SPA runs it directly against the artifact HTML preview's same-origin
//   iframe (FilePane), calling methods and receiving callbacks synchronously.
// Framework-free and parameterized on the target window/document on purpose:
// it must never touch globals (the companion bundles it into its IIFE, the
// SPA lazy-imports it).
import { domToPng } from 'modern-screenshot'

// Description caps: a CSS-selector style chain of ancestors plus the
// element's opening tag and visible text. Unlike an indexed XPath, this maps
// onto what the page's SOURCE looks like — ids, classes and text are what an
// agent can grep for.
const MAX_CLASSES = 4
const MAX_TEXT = 100
const MAX_TAG = 300

/**
 * @param {object} opts
 * @param {Window} opts.win  Window owning the page to pick in.
 * @param {Document} opts.doc  Its document.
 * @param {(state: object) => void} opts.onState  Fired on every selection
 *        change with { hasSelection, locked, canParent, canFirstChild,
 *        canPrevSibling, canNextSibling, rect } — drives the host card's
 *        navigation buttons; `rect` ({ left, top, width, height } in this
 *        window's viewport, null without selection) lets the host keep its
 *        card off the element.
 */
export function createElementPicker({ win, doc, onState }) {
    // A full-viewport overlay swallows every pointer interaction — the page
    // must not react at all while the mode is on — and the element under the
    // pointer gets a dashed outline. Two states: hovering (red outline
    // follows the pointer) and locked (a click/tap turned it green; hover is
    // ignored so the user can travel to the host's card without losing the
    // selection — navigation and clear both keep/lift the lock). The
    // outline box is a separate pointer-events:none node, so hit-testing
    // skips it; elementsFromPoint sees the page through the overlay by
    // filtering our own two nodes out.
    let overlay = null
    let outline = null
    let lastPoint = null
    let current = null
    let locked = false

    function isOwnNode(node) {
        return node === overlay || node === outline
    }

    // One DOM step from `el`, skipping our own overlay/outline nodes (both
    // are children of <body>, so a sibling walk there would land on them).
    function stepFrom(el, direction) {
        if (direction === 'parent') return el.parentElement
        let node =
            direction === 'first-child'
                ? el.firstElementChild
                : direction === 'prev-sibling'
                  ? el.previousElementSibling
                  : direction === 'next-sibling'
                    ? el.nextElementSibling
                    : null
        while (node && isOwnNode(node)) {
            node = direction === 'prev-sibling' ? node.previousElementSibling : node.nextElementSibling
        }
        return node
    }

    // Where the host's navigation buttons can go from the current element —
    // reported on every element change so the buttons enable/disable live.
    function emitState() {
        const el = current
        const box = el ? el.getBoundingClientRect() : null
        onState({
            hasSelection: !!el,
            locked,
            rect: box ? { left: box.left, top: box.top, width: box.width, height: box.height } : null,
            canParent: !!(el && stepFrom(el, 'parent')),
            canFirstChild: !!(el && stepFrom(el, 'first-child')),
            canPrevSibling: !!(el && stepFrom(el, 'prev-sibling')),
            canNextSibling: !!(el && stepFrom(el, 'next-sibling')),
        })
    }

    function drawOutline() {
        if (!outline) return
        if (current && !current.isConnected) {
            // The page re-rendered under us (SPA) — the selection is gone.
            setCurrent(null, false)
            return
        }
        if (!current) {
            outline.style.display = 'none'
            return
        }
        const rect = current.getBoundingClientRect()
        outline.style.outlineColor = locked ? '#30a46c' : '#e5484d'
        outline.style.display = 'block'
        outline.style.left = `${rect.left}px`
        outline.style.top = `${rect.top}px`
        outline.style.width = `${rect.width}px`
        outline.style.height = `${rect.height}px`
    }

    function setCurrent(el, isLocked) {
        const changed = el !== current || isLocked !== locked
        current = el
        locked = isLocked
        drawOutline()
        if (changed) emitState()
    }

    function nav(direction) {
        if (!overlay || !current) return
        const next = stepFrom(current, direction)
        if (!next) return
        // An explicit choice locks the selection (green), whatever it started
        // from, and drops the pointer anchor (the scrollIntoView below would
        // otherwise immediately re-aim at the stale pointer position).
        lastPoint = null
        setCurrent(next, true)
        next.scrollIntoView({ block: 'nearest', inline: 'nearest' })
    }

    function clear() {
        lastPoint = null
        setCurrent(null, false)
    }

    function highlightAt(x, y) {
        lastPoint = { x, y }
        const el = doc.elementsFromPoint(x, y).find((node) => !isOwnNode(node))
        setCurrent(el || null, false)
    }

    // One chain segment: tag#id.classes, with :nth-of-type only when the
    // segment alone would be ambiguous among same-tag siblings.
    function segmentFor(node) {
        const signature = (el) => `${el.id}|${[...el.classList].slice(0, MAX_CLASSES).join('.')}`
        let segment = node.localName
        if (node.id) segment += `#${node.id}`
        const classes = [...node.classList].slice(0, MAX_CLASSES)
        if (classes.length) segment += `.${classes.join('.')}`
        const sameTag = node.parentElement
            ? [...node.parentElement.children].filter(
                  (sib) => !isOwnNode(sib) && sib.localName === node.localName
              )
            : []
        if (sameTag.length > 1 && sameTag.filter((sib) => signature(sib) === signature(node)).length > 1) {
            segment += `:nth-of-type(${sameTag.indexOf(node) + 1})`
        }
        return segment
    }

    function chainFor(el) {
        const parts = []
        for (let node = el; node && node !== doc.documentElement; node = node.parentElement) {
            parts.unshift(segmentFor(node))
        }
        return parts.length ? parts.join(' > ') : el.localName
    }

    // The element's own opening tag — it carries every attribute (id,
    // classes, data-*, aria-*) in the exact shape the source declares them.
    // Vue's scoped-style data-v-* markers are compile-time noise and dropped.
    function openingTagFor(el) {
        const clone = el.cloneNode(false)
        for (const name of clone.getAttributeNames()) {
            if (name.startsWith('data-v-')) clone.removeAttribute(name)
        }
        const html = clone.outerHTML
        const end = html.indexOf('>')
        const tag = end === -1 ? html : html.slice(0, end + 1)
        return tag.length > MAX_TAG ? `${tag.slice(0, MAX_TAG)}…>` : tag
    }

    function textFor(el) {
        const text = (el.textContent || '').replace(/\s+/g, ' ').trim()
        return text.length > MAX_TEXT ? `${text.slice(0, MAX_TEXT)}…` : text
    }

    function describe() {
        if (!current) return null
        return { chain: chainFor(current), openingTag: openingTagFor(current), text: textFor(current) }
    }

    // Render the current element to a PNG data URL with modern-screenshot (a
    // live-DOM-to-image renderer, foreignObject-based). Fidelity is
    // best-effort by nature — webfont glyphs and cross-origin images may not
    // survive the round-trip.
    async function capture() {
        if (!current) throw new Error('no element selected')
        return await domToPng(current)
    }

    function onPointerMove(event) {
        // Hover only drives the outline while nothing is locked.
        if (locked) return
        highlightAt(event.clientX, event.clientY)
    }

    function onPointerDown(event) {
        // A click/tap locks the element under the point (touch has no hover —
        // the tap IS the pointing gesture). preventDefault also keeps it from
        // focusing/activating anything underneath.
        event.preventDefault()
        const el = doc.elementsFromPoint(event.clientX, event.clientY).find((node) => !isOwnNode(node))
        if (!el) return
        lastPoint = null
        setCurrent(el, true)
    }

    function blockEvent(event) {
        event.preventDefault()
    }

    function onScroll() {
        // The page still scrolls under the overlay (wheel/touch chaining). A
        // locked selection sticks to its element (redraw its rect); a hover
        // one re-aims at the last pointer position so the outline tracks the
        // element now under it.
        if (!locked && lastPoint) highlightAt(lastPoint.x, lastPoint.y)
        else drawOutline()
    }

    function enable() {
        if (overlay) return
        overlay = doc.createElement('div')
        overlay.style.cssText = 'position:fixed;inset:0;z-index:2147483646;cursor:crosshair;background:transparent;'
        overlay.addEventListener('pointermove', onPointerMove)
        overlay.addEventListener('pointerdown', onPointerDown)
        overlay.addEventListener('click', blockEvent)
        overlay.addEventListener('contextmenu', blockEvent)
        outline = doc.createElement('div')
        outline.style.cssText =
            'position:fixed;z-index:2147483647;pointer-events:none;display:none;' +
            'outline:2px dashed #e5484d;outline-offset:-2px;'
        win.addEventListener('scroll', onScroll, true)
        const root = doc.body || doc.documentElement
        root.appendChild(overlay)
        root.appendChild(outline)
    }

    function disable() {
        if (!overlay) return
        win.removeEventListener('scroll', onScroll, true)
        overlay.remove()
        outline.remove()
        overlay = null
        outline = null
        lastPoint = null
        current = null
        locked = false
    }

    // Teardown that must never throw — the document may already be dead
    // (iframe reloaded out from under the SPA caller).
    function destroy() {
        try {
            disable()
        } catch {
            overlay = null
            outline = null
            lastPoint = null
            current = null
            locked = false
        }
    }

    return { enable, disable, nav, clear, describe, capture, destroy }
}
