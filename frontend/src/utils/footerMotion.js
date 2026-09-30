// Footer blocks motion (visual refresh step 7d): values and keyframes of the footer controller.
// Pure: no Vue, no DOM. The Vue side is composables/useFooterMotion.js.
// Design: docs/plans/2026-09-30-footer-blocks-motion-design.md §3, §4.1.
//
// Keyframes only use height, opacity, overflowY, inset and the discrete position, zIndex,
// maxHeight: never a transform (motion invariants, step 4a). overflowY clips one axis only,
// so a glow or a focus ring may extend sideways.

import { parseDurationMs } from './detailsMotion.js'

// Fallbacks only matter if motion.css is missing: the values of its tokens. Fades use
// ease-in-out (roadmap §6i.2: --motion-ease is front-loaded for fades).
export const FOOTER_MOTION = Object.freeze({
    heightMs: 380,
    fadeMs: 200,
    heightEasing: 'cubic-bezier(.25, .46, .45, .94)',
    fadeEasing: 'ease-in-out',
})

/** A duration token as milliseconds; `fallback` when empty, unparsable, zero or negative. */
export function tokenMs(value, fallback) {
    const text = String(value ?? '').trim()
    if (!text) return fallback
    const ms = parseDurationMs(text)
    return Number.isFinite(ms) && ms > 0 ? ms : fallback
}

/** The timings from the motion tokens, `read(name)` returning a custom property's value. */
export function footerMotionTimings(read) {
    const easing = String(read('--motion-ease-out-height') ?? '').trim()
    return {
        heightMs: tokenMs(read('--motion-dur-3'), FOOTER_MOTION.heightMs),
        fadeMs: tokenMs(read('--motion-dur-2'), FOOTER_MOTION.fadeMs),
        heightEasing: easing || FOOTER_MOTION.heightEasing,
        fadeEasing: FOOTER_MOTION.fadeEasing,
    }
}

export function heightKeyframes(fromPx, toPx) {
    return [
        { height: `${fromPx}px`, overflowY: 'clip' },
        { height: `${toPx}px`, overflowY: 'clip' },
    ]
}

export function fadeKeyframes(from, to) {
    return [{ opacity: from }, { opacity: to }]
}

const insetOf = ({ top, bottom }) => `${top}px 0 ${bottom}px 0`

/** A block's `{ top, bottom }` offsets inside the container, animated from `from` to `to`. */
export function insetKeyframes(from, to) {
    return [{ inset: insetOf(from) }, { inset: insetOf(to) }]
}

/** The restore: the block, back in flow, is held absolute for the run and shrinks to `target`. */
export function restoreKeyframes(from, target) {
    const discrete = { position: 'absolute', zIndex: 2, height: 'auto', maxHeight: 'none' }
    return insetKeyframes(from, target).map((frame) => ({ ...frame, ...discrete }))
}

/** Whether a height change is worth a motion. A 0 height is legal at either end. */
export function shouldAnimate({ enabled, visible, fromPx, toPx }) {
    if (!enabled || !visible) return false
    return Math.abs(toPx - fromPx) >= 1
}

/**
 * A block's `{ top, bottom }` offsets against the container's padding box (what `inset`
 * resolves against): `rect` and `containerRect` are border-box rects, `box` carries the
 * container's clientTop, clientHeight and offsetHeight.
 */
export function paddingBoxOffsets(rect, containerRect, { clientTop, clientHeight, offsetHeight }) {
    const paddingTop = containerRect.top + clientTop
    const paddingBottom = containerRect.bottom - (offsetHeight - clientTop - clientHeight)
    return { top: rect.top - paddingTop, bottom: paddingBottom - rect.bottom }
}
