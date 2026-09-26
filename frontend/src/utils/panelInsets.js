// panelInsets.js — PURE helpers for the floating-panel cards. No DOM, no Vue.
// Design: docs/plans/2026-09-26-floating-panels-design.md (§6.3 region insets, §6.4 iframe
// corners). The layout resolver returns edge-to-edge rects; each card is inset by half the
// panel gap on the edges that face another region, so neighbours end up one gap apart while
// the outer edges stay flush with the layout box. The inset is emitted as CSS calc() over
// --panel-half-gap so it stays in rem and follows the font-size setting with no measurement.

// The resolver works in fractional px: an edge within this distance of the layout boundary
// is on the boundary.
const EDGE_EPSILON = 0.5

// A pooled iframe corner is "flush" with its card's corner when within this distance on both
// axes: the largest card border (4px, awesome theme) plus rounding.
export const FLUSH_TOLERANCE_PX = 6

// Radius given to a flush iframe corner: the card's padding-box radius, resolved by the
// browser from the inherited tokens (theme, breakpoint and font size apply with no JS).
export const CARD_CORNER_RADIUS = 'var(--panel-inner-radius)'

export const NO_INSETS = Object.freeze({ left: false, top: false, right: false, bottom: false })

/** Edges of `rect` ({x,y,w,h}) that face another region rather than the layout boundary. */
export function innerEdges(rect, viewport) {
    return {
        left: rect.x > EDGE_EPSILON,
        top: rect.y > EDGE_EPSILON,
        right: rect.x + rect.w < viewport.w - EDGE_EPSILON,
        bottom: rect.y + rect.h < viewport.h - EDGE_EPSILON,
    }
}

/** Absolute-position style for `rect`, inset by half the panel gap on its inner `edges`. */
export function insetRectStyle(rect, edges) {
    const l = edges.left ? 1 : 0
    const t = edges.top ? 1 : 0
    const r = edges.right ? 1 : 0
    const b = edges.bottom ? 1 : 0
    const half = 'var(--panel-half-gap)'
    return {
        left: `calc(${rect.x}px + ${half} * ${l})`,
        top: `calc(${rect.y}px + ${half} * ${t})`,
        width: `calc(${rect.w}px - ${half} * ${l + r})`,
        height: `calc(${rect.h}px - ${half} * ${t + b})`,
    }
}

/** The part of a frame rect ({x,y,width,height}) left visible by its clip container. */
export function visibleRect(frameRect, clipRect) {
    if (!clipRect) return frameRect
    const x = Math.max(frameRect.x, clipRect.x)
    const y = Math.max(frameRect.y, clipRect.y)
    const right = Math.min(frameRect.x + frameRect.width, clipRect.x + clipRect.width)
    const bottom = Math.min(frameRect.y + frameRect.height, clipRect.y + clipRect.height)
    return { x, y, width: Math.max(0, right - x), height: Math.max(0, bottom - y) }
}

/** Which corners of the visible frame rect sit in the matching corner of its card. */
export function frameFlushCorners(visible, cardRect) {
    const near = (a, b) => Math.abs(a - b) <= FLUSH_TOLERANCE_PX
    const left = near(visible.x, cardRect.x)
    const top = near(visible.y, cardRect.y)
    const right = near(visible.x + visible.width, cardRect.x + cardRect.width)
    const bottom = near(visible.y + visible.height, cardRect.y + cardRect.height)
    return { tl: top && left, tr: top && right, br: bottom && right, bl: bottom && left }
}

/**
 * clip-path for a pooled frame cell: the clip container's insets (parts scrolled out of the
 * owner's scroll container) plus rounded corners where the frame is flush with its card.
 * Null when nothing needs clipping.
 */
export function frameClipPath(frameRect, clipRect, corners) {
    const { x, y, width, height } = frameRect
    let top = 0, right = 0, bottom = 0, left = 0
    if (clipRect) {
        top = Math.max(0, clipRect.y - y)
        left = Math.max(0, clipRect.x - x)
        right = Math.max(0, x + width - (clipRect.x + clipRect.width))
        bottom = Math.max(0, y + height - (clipRect.y + clipRect.height))
    }
    const rounded = !!corners && (corners.tl || corners.tr || corners.br || corners.bl)
    if (!(top || right || bottom || left) && !rounded) return null
    const box = `${top}px ${right}px ${bottom}px ${left}px`
    if (!rounded) return `inset(${box})`
    const r = (on) => (on ? CARD_CORNER_RADIUS : '0')
    return `inset(${box} round ${r(corners.tl)} ${r(corners.tr)} ${r(corners.br)} ${r(corners.bl)})`
}
