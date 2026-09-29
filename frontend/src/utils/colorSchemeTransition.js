// Color-scheme reveal (visual refresh step 5c): the new scheme grows in a circle from the
// control the user changed it with, else the page fades. The settings store applies every
// change through runSchemeTransition; the entry points only leave a one-shot origin.
// Design: docs/plans/2026-09-29-list-cascade-scheme-reveal-design.md §5.
//
// No store or component import (the settings store imports this file).

import { COLOR_SCHEME } from '../constants.js'
import { runViewTransition } from './viewTransition.js'

/** Duration of the circle (the twicc-scheme-circle rule in styles/motion.css). */
export const SCHEME_REVEAL_MS = 650

let nextOrigin = null

/** Radius (px) of a circle centered on (x, y) that covers a width × height viewport,
    plus a 10% margin of the larger side (the root snapshot can exceed the layout viewport
    on mobile). */
export function revealRadius(x, y, width, height) {
    return Math.hypot(Math.max(x, width - x), Math.max(y, height - y)) + 0.1 * Math.max(width, height)
}

/** Center of an element's bounding rect, { x, y }, or null. */
export function originFromElement(el) {
    const rect = el?.getBoundingClientRect?.()
    if (!rect) return null
    return { x: rect.left + rect.width / 2, y: rect.top + rect.height / 2 }
}

/** Center of the viewport, { x, y }. */
export function viewportCenter(env = globalThis) {
    return { x: env.innerWidth / 2, y: env.innerHeight / 2 }
}

/** 'dark' or 'light' for a stored scheme ('system' follows the OS). */
export function effectiveSchemeFor(mode, env = globalThis) {
    if (mode === COLOR_SCHEME.SYSTEM) {
        return env.matchMedia?.('(prefers-color-scheme: dark)')?.matches ? COLOR_SCHEME.DARK : COLOR_SCHEME.LIGHT
    }
    return mode
}

/** One-shot origin for the next scheme change (consumed by takeNextSchemeOrigin). It
    expires at the end of the task: the store watcher that takes it runs before. */
export function setNextSchemeOrigin(origin, env = globalThis) {
    nextOrigin = origin
    env.setTimeout(() => {
        if (nextOrigin === origin) nextOrigin = null
    }, 0)
}

export function takeNextSchemeOrigin() {
    const origin = nextOrigin
    nextOrigin = null
    return origin
}

/** Apply a scheme change: circle from `origin`, else a fade; nothing animates when
    `animate` is false. */
export function runSchemeTransition(apply, { origin = null, animate = true, env = globalThis } = {}) {
    if (!animate) {
        apply()
        return
    }
    const reduced = env.matchMedia?.('(prefers-reduced-motion: reduce)')?.matches
    if (origin && !reduced) {
        const radius = revealRadius(origin.x, origin.y, env.innerWidth, env.innerHeight)
        runViewTransition(apply, {
            kind: 'circle',
            properties: {
                '--twicc-scheme-x': `${origin.x}px`,
                '--twicc-scheme-y': `${origin.y}px`,
                '--twicc-scheme-r': `${radius}px`,
            },
            env,
        })
        return
    }
    runViewTransition(apply, { kind: 'fade', env })
}
