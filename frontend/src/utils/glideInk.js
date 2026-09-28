// Gliding indicators (visual refresh step 4c).
// Design: docs/plans/2026-09-28-gliding-indicators-design.md.
//
// One shared shape, the ink, marks the current item of a list, a tab bar or a segmented
// control. This module measures the active item and writes its box into four custom
// properties (--glide-x/y/w/h) on a target; a CSS transition (motion.css) moves the ink.
// It decides for each placement whether the ink glides (another item of the same list,
// ink already shown), snaps (first placement, a list rebuilt, the same item moved) or
// hides (nothing to mark). Same code path in every browser.

import { parseDurationMs } from './detailsMotion.js'

const PROPERTIES = ['--glide-x', '--glide-y', '--glide-w', '--glide-h']
const READY = 'data-glide-ready'
const INSTANT = 'data-glide-instant'
const ZERO_BOX = { x: 0, y: 0, w: 0, h: 0 }

// ---------------------------------------------------------------------------
// Pure helpers
// ---------------------------------------------------------------------------

// Box of `activeRect` inside the container, in content coordinates and layout pixels.
// The rects are visual (getBoundingClientRect, after transforms); the scroll and client
// offsets are layout values. The container's visual/layout ratio removes an ancestor's
// scale (a dialog's opening animation) before the layout offsets are added.
// offsetWidth/offsetHeight are rounded integers while the rect is fractional: a difference
// of 1px or less is rounding, not a scale, and gives a ratio of exactly 1 (a real opening
// scale of 0.8 differs by tens of px).
export function scaleRatio(visual, layout) {
    return layout > 0 && visual > 0 && Math.abs(visual - layout) > 1 ? visual / layout : 1
}

export function relativeBox(activeRect, containerRect,
                            { scrollLeft = 0, scrollTop = 0, clientLeft = 0, clientTop = 0,
                              offsetWidth = 0, offsetHeight = 0 } = {}) {
    // Web Awesome's opening scales are uniform: one ratio, taken on the container's longer
    // layout axis (where a real scale is furthest above the 1px rounding threshold).
    const s = offsetWidth >= offsetHeight
        ? scaleRatio(containerRect.width, offsetWidth)
        : scaleRatio(containerRect.height, offsetHeight)
    const sx = s
    const sy = s
    return {
        x: (activeRect.left - containerRect.left) / sx - clientLeft + scrollLeft,
        y: (activeRect.top - containerRect.top) / sy - clientTop + scrollTop,
        w: activeRect.width / sx,
        h: activeRect.height / sy,
    }
}

// What the next placement does.
//   state: { ready, active, resetKey }   (last placement)
//   next:  { visible, active, resetKey } (now)
export function placementMode(state, next) {
    if (!next.visible || !next.active) return 'hide'
    if (state.ready && state.active && state.active !== next.active
        && Object.is(state.resetKey, next.resetKey)) return 'glide'
    return 'snap'
}

// true when all four values differ by less than 0.01px (a or b may be null → false unless
// both null). The tolerance absorbs the float noise of the scale division, so a measure
// taken during an opening animation does not snap over a running glide.
export function sameBox(a, b) {
    if (!a || !b) return !a && !b
    return Math.abs(a.x - b.x) < 0.01 && Math.abs(a.y - b.y) < 0.01
        && Math.abs(a.w - b.w) < 0.01 && Math.abs(a.h - b.h) < 0.01
}

const sameRect = (a, b) => !!a && !!b && Math.abs(a.left - b.left) < 0.01 && Math.abs(a.top - b.top) < 0.01
    && Math.abs(a.width - b.width) < 0.01 && Math.abs(a.height - b.height) < 0.01

// ---------------------------------------------------------------------------
// Controller
// ---------------------------------------------------------------------------

// The settle loop's safety cap: both a frame count and a duration, so a short opening on a
// high refresh rate display is not cut early.
const SETTLE_MAX_FRAMES = 60
const SETTLE_MAX_MS = 500

export function createGlideInk({
    container,            // Element
    target = container,   // Element receiving --glide-* and data-glide-*
    flushTarget = target, // Element whose computed style carries the ink's transition
    flushPseudo = null,   // '::after' for the tab bar ink, else null
    getActive,            // () => Element | null
    getItems = () => [],  // () => Element[] — extra elements watched for resize
    getResetKey = () => null,
    // globalThis, never a plain object holding the bare window functions: those throw
    // "Illegal invocation" when called with another `this`.
    env = globalThis,
}) {
    const state = {
        ready: false, active: null, resetKey: null, box: null,
        collapseTimer: null, settleFrame: null, settleRect: null,
    }
    // Settle loop: frame count and the first frame's timestamp.
    let settleCount = 0
    let settleStart = 0
    const observed = new Set()

    function writeBox(box) {
        const values = [box.x, box.y, box.w, box.h]
        PROPERTIES.forEach((name, i) => target.style.setProperty(name, `${values[i]}px`))
    }

    // The ink is scaled with an ancestor when the container's visual size on its longer
    // axis differs from its layout size by more than the offset rounding.
    function isScaled(rect) {
        const [visual, layout] = container.offsetWidth >= container.offsetHeight
            ? [rect.width, container.offsetWidth]
            : [rect.height, container.offsetHeight]
        return layout > 0 && Math.abs(visual - layout) > 0.5
    }

    function syncObserved(active) {
        const wanted = new Set([container, active, ...getItems()].filter(Boolean))
        for (const element of observed) {
            if (!wanted.has(element)) {
                observer.unobserve(element)
                observed.delete(element)
            }
        }
        for (const element of wanted) {
            if (!observed.has(element)) {
                observer.observe(element)
                observed.add(element)
            }
        }
    }

    function cancelSettle() {
        if (state.settleFrame !== null) env.cancelAnimationFrame(state.settleFrame)
        state.settleFrame = null
        state.settleRect = null
    }

    function startSettle(rect) {
        if (state.settleFrame !== null) return
        settleCount = 0
        state.settleRect = rect
        state.settleFrame = env.requestAnimationFrame(settleStep)
    }

    // Each frame only reads: a write per frame would restart a running glide every frame
    // (Firefox rounds positions to 1/60px).
    function settleStep(timestamp) {
        state.settleFrame = null
        settleCount += 1
        if (settleCount === 1) settleStart = timestamp
        const rect = container.getBoundingClientRect()
        const still = sameRect(rect, state.settleRect)
        const stop = (still && !isScaled(rect))
            // Not before the 3rd frame: Firefox can still show the start scale on the first.
            || (still && settleCount >= 3)
            || (settleCount >= SETTLE_MAX_FRAMES && timestamp - settleStart >= SETTLE_MAX_MS)
        if (stop) {
            state.settleRect = null
            update({ settle: true })
            return
        }
        state.settleRect = rect
        state.settleFrame = env.requestAnimationFrame(settleStep)
    }

    function update({ settle = false } = {}) {
        const containerRect = container.getBoundingClientRect()
        const visible = container.isConnected && containerRect.width > 0 && containerRect.height > 0
        let active = getActive() ?? null
        if (active) {
            const activeRect = active.getBoundingClientRect()
            if (!(activeRect.width > 0 && activeRect.height > 0)) active = null
        }
        const resetKey = getResetKey()
        if (state.collapseTimer !== null) {
            env.clearTimeout(state.collapseTimer)
            state.collapseTimer = null
        }
        // Every mode, hide included: a controller created hidden places its ink when the
        // container gets a size.
        syncObserved(active)

        let mode = placementMode(state, { visible, active, resetKey })

        if (mode === 'hide') {
            target.removeAttribute(READY)
            state.ready = false
            state.active = null
            state.box = null
            cancelSettle()
            // Collapse the invisible ink to a zero box, so it never keeps the list's scroll
            // range: at once when hidden, else after the fade.
            if (!visible) writeBox(ZERO_BOX)
            else {
                const delay = parseDurationMs(env.getComputedStyle(target).getPropertyValue('--motion-dur-1'))
                state.collapseTimer = env.setTimeout(() => {
                    state.collapseTimer = null
                    writeBox(ZERO_BOX)
                }, delay)
            }
            return
        }

        const box = relativeBox(active.getBoundingClientRect(), containerRect, {
            scrollLeft: container.scrollLeft,
            scrollTop: container.scrollTop,
            clientLeft: container.clientLeft,
            clientTop: container.clientTop,
            offsetWidth: container.offsetWidth,
            offsetHeight: container.offsetHeight,
        })

        // A settle correction of the same element is under half a pixel: written like a
        // glide, so a glide the user started during the opening is retargeted, not cut.
        if (settle && mode === 'snap' && state.active === active) mode = 'glide'

        if (!sameBox(box, state.box)) {
            if (mode === 'snap') {
                target.setAttribute(INSTANT, '')
                writeBox(box)
                target.setAttribute(READY, '')
                // Commit the new values with `transition: none`, so no transition starts;
                // removing the attribute afterwards changes no value.
                env.getComputedStyle(flushTarget, flushPseudo).getPropertyValue('translate')
                target.removeAttribute(INSTANT)
            } else {
                writeBox(box)
            }
        }

        state.ready = true
        state.active = active
        state.resetKey = resetKey
        state.box = box

        if (!settle && isScaled(containerRect)) startSettle(containerRect)
    }

    function destroy() {
        if (state.collapseTimer !== null) env.clearTimeout(state.collapseTimer)
        state.collapseTimer = null
        cancelSettle()
        observer.disconnect()
        observed.clear()
        target.removeAttribute(READY)
        target.removeAttribute(INSTANT)
        for (const name of PROPERTIES) target.style.removeProperty(name)
    }

    // A nested target never inherits its ancestor's box.
    writeBox(ZERO_BOX)
    // The callback runs before paint: a resize never shows a stale ink.
    const observer = new env.ResizeObserver(() => update())
    update()
    return { update, destroy }
}
