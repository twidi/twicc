// Scroll-edge shadows. A zone that can still scroll shows a shadow at the edge where more is
// hidden, so the user sees it is not at the end. This module only measures: it writes
// --scroll-edge-top / --scroll-edge-bottom (1 or 0) on the scrolling element, and
// styles/scroll-shadow.css draws the shadow from them. Two entry points:
//  - installScrollEdges(): the scrolling body of the Web Awesome dialogs and drawers, found when the
//    component is shown, once for all of them (not the glass surfaces: a mask would break their glass);
//  - the `v-scroll-shadow` directive: a scrolling zone of our own markup (a popover's content, a list).
// The measure runs on scroll, on resize of the zone or of its content, and when the content changes.
export const EDGE_TOLERANCE = 2

/** Which edges of a scroll container still hide content. */
export function edgeState({ scrollTop, scrollHeight, clientHeight }) {
    return {
        top: scrollTop > EDGE_TOLERANCE,
        bottom: scrollHeight - clientHeight - scrollTop > EDGE_TOLERANCE,
    }
}

/** What the shadow layers need to know of the scroller's box: the shadows are in-flow sticky
 *  pseudo-elements, pulled back over the gap between children, spread over the side padding and pinned past the
 *  top and bottom padding (a sticky box stays inside the content box, the shadow must reach the edge). */
export function boxMetrics({ rowGap, paddingLeft, paddingRight, paddingTop, paddingBottom }) {
    return {
        gap: !rowGap || rowGap === 'normal' ? '0px' : rowGap,
        left: paddingLeft || '0px',
        right: paddingRight || '0px',
        top: paddingTop || '0px',
        bottom: paddingBottom || '0px',
    }
}

export function updateBox(el) {
    const { gap, left, right, top, bottom } = boxMetrics(getComputedStyle(el))
    el.style.setProperty('--scroll-edge-gap', gap)
    el.style.setProperty('--scroll-edge-pad-left', left)
    el.style.setProperty('--scroll-edge-pad-right', right)
    el.style.setProperty('--scroll-edge-pad-top', top)
    el.style.setProperty('--scroll-edge-pad-bottom', bottom)
}

/** The scrolling part each Web Awesome component owns. */
export const SCROLLING_PARTS = {
    'wa-dialog': 'body',
    'wa-drawer': 'body',
}

export function updateEdges(el) {
    const { top, bottom } = edgeState(el)
    el.style.setProperty('--scroll-edge-top', top ? '1' : '0')
    el.style.setProperty('--scroll-edge-bottom', bottom ? '1' : '0')
}

const watched = new WeakSet()

// A slot has no box of its own: what it shows is what its content boxes measure.
function contentBoxes(el) {
    const boxes = []
    for (const child of el.children) {
        if (child.localName === 'slot') boxes.push(...child.assignedElements({ flatten: true }))
        else boxes.push(child)
    }
    return boxes
}

/** Keep the edge flags of `el` up to date. `host` is the element whose light-DOM changes mean new
 *  content (the Web Awesome component, for a part inside its shadow root); it defaults to `el`. */
export function watchScrollEdges(el, host = el) {
    if (watched.has(el)) {
        updateBox(el)
        updateEdges(el)
        return
    }
    watched.add(el)
    const resize = new ResizeObserver(() => {
        updateBox(el)
        updateEdges(el)
    })
    const observeContent = () => {
        resize.disconnect()
        resize.observe(el)
        contentBoxes(el).forEach((box) => resize.observe(box))
        contentBoxes(host).forEach((box) => resize.observe(box))
    }
    el.addEventListener('scroll', () => updateEdges(el), { passive: true })
    new MutationObserver(() => {
        observeContent()
        updateEdges(el)
    }).observe(host, { childList: true, subtree: true })
    observeContent()
    updateBox(el)
    updateEdges(el)
}

/** Watch the scrolling part of every Web Awesome component of SCROLLING_PARTS when it is shown. */
export function installScrollEdges() {
    document.addEventListener('wa-after-show', (event) => {
        const host = event.target
        const part = SCROLLING_PARTS[host.localName]
        const el = part && host.shadowRoot?.querySelector(`[part~="${part}"]`)
        if (el) watchScrollEdges(el, host)
    })
}

/** `v-scroll-shadow`: the zone shows a shadow at the edges where more content is hidden. */
export const vScrollShadow = {
    mounted(el) {
        el.setAttribute('data-scroll-shadow', '')
        watchScrollEdges(el)
    },
}
