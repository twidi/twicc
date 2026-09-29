// Sidebar list rows (visual refresh step 6a-bis): the DOM helpers the session list and the
// artifacts list pass to useGlideInk, so the open row's lit fill glides from row to row, and
// the helpers that reveal the open row only near an edge.
// Design: docs/plans/2026-09-29-sidebar-row-glide-design.md §4.2, §10.

import { nearestScrollTop } from './nearestScroll.js'

const ZERO_OFFSET = Object.freeze({ x: 0, y: 0 })

// The open row's `base` part, not its host: the part carries a margin-bottom
// (styles/sidebar-rows.css), so the host is taller than the lit box. null until Lit has
// rendered the button's shadow tree (one microtask after it connects).
export function activeRowBase(root) {
    return root?.querySelector('.sidebar-row--active')?.shadowRoot?.querySelector('[part~="base"]') ?? null
}

// The entrance translate of the open row (its closest .list-entering ancestor, the 5c
// cascade and live entrance), in layout px. The controller subtracts it, so a measure taken
// during the entrance lands on the row's final box. `translate` computes to `none`, one
// length (x) or two (x y).
export function entranceOffset(root, env = globalThis) {
    const entering = root?.querySelector('.sidebar-row--active')?.closest('.list-entering')
    if (!entering) return ZERO_OFFSET
    const value = env.getComputedStyle(entering).translate
    if (!value || value === 'none') return ZERO_OFFSET
    const [x = 0, y = 0] = value.trim().split(/\s+/).map((part) => parseFloat(part) || 0)
    return { x, y }
}

const NO_BANDS = Object.freeze({ top: 0, bottom: 0 })

// The reveal bands of a list (styles/sidebar-rows.css, the list's own rules), in px: how
// close to each edge the open row may sit before the list scrolls to reveal it. The bottom
// band adds the part the floating "New session" button covers (session list only). The
// properties are registered as <length>, so the computed style holds them in px.
export function revealBands(el, env = globalThis) {
    if (!el) return NO_BANDS
    const style = env.getComputedStyle(el)
    const read = (name) => parseFloat(style.getPropertyValue(name)) || 0
    return {
        top: read('--sidebar-row-reveal-top'),
        bottom: read('--sidebar-row-reveal-bottom') + read('--sidebar-row-reveal-cover'),
    }
}

// The virtual scroller's margins for the bands: its positions start at 0 without the
// list's top padding, so a row's real top in the scroll content is its position plus the
// padding. Shifting both margins by it puts the zone edges on real pixels (a negative
// marginTop is valid).
export function revealMargins(bands, pad) {
    return { marginTop: bands.top - pad, marginBottom: bands.bottom + pad }
}

// The scrollTop that reveals an artifacts list entry inside the bands, or null when the
// list is already there (within 1px). The entry's offsetTop is a content coordinate of the
// positioned list, padding included.
export function entryReveal({ offsetTop, offsetHeight, scrollTop, clientHeight, scrollHeight }, bands) {
    const target = nearestScrollTop({
        top: offsetTop,
        height: offsetHeight,
        scrollTop,
        viewport: clientHeight,
        scrollMax: scrollHeight - clientHeight,
        marginTop: bands.top,
        marginBottom: bands.bottom,
    })
    return Math.abs(target - scrollTop) <= 1 ? null : target
}
