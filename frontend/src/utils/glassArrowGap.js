// Glass popovers: cut the body's border under the arrow (visual refresh step 3).
// Design: docs/plans/2026-09-27-glass-design.md, §5.2 "The arrow joint".
//
// The glass body draws its border with an overlay above its content, and the arrow is
// translucent, so the border would show through the arrow's base as a line closing it.
// Web Awesome places the arrow in JavaScript and announces every placement with a
// `wa-reposition` event (bubbles, composed). One document listener measures where the
// arrow's base meets the body and publishes it on the popover host; glass.css masks the
// border (and its light edge) over that span, on that side only.

const SIDE_FOR_PLACEMENT = { top: 'bottom', bottom: 'top', left: 'right', right: 'left' }

/**
 * Where the arrow's base meets the body, in the body's own (unscaled) pixels.
 *
 * @param {string|null} placement Web Awesome's `data-current-placement` (e.g. `bottom-start`).
 * @param {{left: number, top: number, width: number, height: number}} bodyRect Body box on screen.
 * @param {{left: number, top: number, right: number, bottom: number, width: number, height: number}} arrowRect
 *   Arrow box on screen (the rotated square's bounding box: its span along the edge is the base).
 * @param {number} [scale=1] Screen size / layout size of the body (below 1 during the show animation).
 * @returns {{side: 'top'|'bottom'|'left'|'right', start: number, end: number, center: number}|null}
 *   `center` is the middle of the base: the popover grows from it (overlay motion, step 5b).
 */
export function computeArrowGap(placement, bodyRect, arrowRect, scale = 1) {
    const side = SIDE_FOR_PLACEMENT[(placement || '').split('-')[0]]
    if (!side || !bodyRect || !arrowRect || !scale) return null
    if (!bodyRect.width || !bodyRect.height || !arrowRect.width || !arrowRect.height) return null

    const horizontal = side === 'top' || side === 'bottom'
    const length = (horizontal ? bodyRect.width : bodyRect.height) / scale
    const from = horizontal ? arrowRect.left - bodyRect.left : arrowRect.top - bodyRect.top
    const to = horizontal ? arrowRect.right - bodyRect.left : arrowRect.bottom - bodyRect.top
    const clamp = (value) => Math.min(Math.max(value / scale, 0), length)
    const round = (value) => Math.round(value * 100) / 100
    const start = clamp(from)
    const end = clamp(to)
    return { side, start: round(start), end: round(end), center: round((start + end) / 2) }
}

function onReposition(event) {
    const popup = event.composedPath()[0]
    const host = popup?.getRootNode?.()?.host
    if (!host || host.tagName !== 'WA-POPOVER') return
    const body = host.shadowRoot?.querySelector('[part~="body"]')
    const arrow = popup.shadowRoot?.querySelector('[part~="arrow"]')
    if (!body || !arrow) return

    const bodyRect = body.getBoundingClientRect()
    const scale = body.offsetWidth ? bodyRect.width / body.offsetWidth : 1
    const gap = computeArrowGap(popup.getAttribute('data-current-placement'), bodyRect, arrow.getBoundingClientRect(), scale)
    if (!gap) {
        delete host.dataset.glassArrow
        return
    }
    host.dataset.glassArrow = gap.side
    host.style.setProperty('--glass-arrow-gap-start', `${gap.start}px`)
    host.style.setProperty('--glass-arrow-gap-end', `${gap.end}px`)
    // The popover's transform origin (utils/waMotionStyles.js, gated on data-glass-arrow).
    host.style.setProperty('--popover-arrow-center', `${gap.center}px`)
}

let installed = false

/** Install the document listener once (SPA and share viewer entry points). */
export function installGlassArrowGap() {
    if (installed || typeof document === 'undefined') return
    installed = true
    document.addEventListener('wa-reposition', onReposition)
}
