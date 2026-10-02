// Footer blocks motion (visual refresh step 7d): the Vue side of utils/footerMotion.js.
// Design: docs/plans/2026-09-30-footer-blocks-motion-design.md §4.
//
// SessionItemsList provides one controller; each footer block (goal, pending request form,
// hybrid terminal, composer) attaches its wrapper to it. A change of the block's shape
// animates the wrapper's height between two measures (FLIP on height: a pre watcher reads
// the old DOM, a post watcher the new one); a mount or unmount goes through the Transition
// hooks; maximize and restore animate the block's inset inside the list. While footer
// animations run, a ResizeObserver keeps the chat at the distance from the bottom it had
// before the change.
//
// The controller only cancels the Web Animations it created (never a CSS animation or
// transition), and never waits for `finished` to clean up: a handler attached after
// cancel() never fires. Its per-block state lives in a WeakMap keyed on the wrapper, so it
// survives the block's effect scope (Vue stops it before the Transition leave hook runs).

import { getCurrentScope, inject, nextTick, onScopeDispose, provide, ref, watch, computed } from 'vue'
import { isReducedMotion } from '../utils/reducedMotion.js'
import {
    fadeKeyframes,
    footerMotionTimings,
    heightKeyframes,
    insetKeyframes,
    paddingBoxOffsets,
    restoreKeyframes,
    shouldAnimate,
} from '../utils/footerMotion.js'

export const FOOTER_MOTION_KEY = Symbol('footerMotion')

const RESTORING = 'data-footer-restoring'
const LEAVING = 'data-footer-leaving'
const CONTAINER = '.session-items-list'
// A user scroll gesture ends the pin at once (pointerdown only on the scroller itself: a
// scrollbar drag).
const GESTURES = ['wheel', 'touchstart', 'keydown', 'pointerdown']
const FULL = Object.freeze({ top: 0, bottom: 0 })
const NEVER = computed(() => false)

const SETTLE_ROUNDS = 4

/**
 * Waits until the Web Awesome elements under `el` (open shadow roots included) have rendered:
 * a new natural height measured before that is short (a bar holding a wa-button: 33.6px, then
 * 46px). Microtasks only (Lit's `updateComplete`), so no frame is painted meanwhile. A render
 * may create nested elements: repeats while a round finds new ones, at most SETTLE_ROUNDS
 * rounds. A rejected `updateComplete` is swallowed. `env` is the controller's seam; nothing in
 * it is needed here, only the element.
 */
export async function settleRender(el, env) {
    const awaited = new Set()
    for (let round = 0; round < SETTLE_ROUNDS; round++) {
        const pending = []
        const walk = (root) => {
            for (const node of root.querySelectorAll('*')) {
                if (node.localName?.startsWith('wa-') && !awaited.has(node)) {
                    const update = node.updateComplete
                    if (typeof update?.then === 'function') {
                        awaited.add(node)
                        pending.push(Promise.resolve(update).catch(() => {}))
                    }
                }
                if (node.shadowRoot) walk(node.shadowRoot)
            }
        }
        walk(el)
        if (!pending.length) return
        await Promise.all(pending)
    }
}

function realEnv() {
    return {
        getComputedStyle: (element) => window.getComputedStyle(element),
        matchMedia: (query) => window.matchMedia(query),
        document: window.document,
        ResizeObserver: window.ResizeObserver,
        requestAnimationFrame: (callback) => window.requestAnimationFrame(callback),
    }
}

/**
 * The controller. `enabled` and `pinEnabled` are Ref<boolean>; `getScrollEl` returns the chat
 * scroller's root, `isAtBottom` whether it sits in its bottom zone.
 */
export function createFooterMotion({ env, enabled, pinEnabled, getScrollEl, isAtBottom }) {
    const animating = ref(false)
    const blocks = new WeakMap()
    let running = 0
    let capture = null
    let pin = null
    let timings = null

    function stateOf(wrapper) {
        let state = blocks.get(wrapper)
        if (!state) {
            // `generation`: bumped by every pre run, enter and leave of the wrapper; a run that
            // waited for the render (§9) and finds it changed is stale and plays nothing.
            // `pendingFromPx`: the prevPx of a post run waiting for the render (§9.1), owned by
            // `pendingRun`; a pre run landing meanwhile starts from it.
            state = {
                animations: new Set(), count: 0, animating: null, onSettled: null, inset: null, restore: null, generation: 0,
                pendingFromPx: null, pendingRun: null,
            }
            blocks.set(wrapper, state)
        }
        return state
    }

    // Read once: motion.css stays the single source of the values.
    function readTimings() {
        if (!timings) {
            const style = env.getComputedStyle(globalThis.document?.documentElement ?? null)
            timings = footerMotionTimings((name) => style?.getPropertyValue(name))
        }
        return timings
    }

    const isReduced = () => isReducedMotion(env)
    const isVisible = (el) => el.getClientRects().length > 0

    // ── Chat pin ─────────────────────────────────────────────────────────────
    // The gap is read before any DOM change of the batch: the browser clamps scrollTop as
    // soon as the list grows. The first capture of a batch wins; the next frame clears it.
    function capturePin() {
        if (capture || !pinEnabled.value || !isAtBottom()) return
        const scrollEl = getScrollEl()
        if (!scrollEl) return
        capture = { gap: scrollEl.scrollHeight - scrollEl.clientHeight - scrollEl.scrollTop }
        env.requestAnimationFrame(() => {
            capture = null
        })
    }

    function connectPin() {
        const scrollEl = getScrollEl()
        if (!capture || !scrollEl) return
        const { gap } = capture
        // The callback runs before the paint: the chat keeps its gap on every frame.
        const observer = new env.ResizeObserver(() => {
            scrollEl.scrollTop = scrollEl.scrollHeight - scrollEl.clientHeight - gap
        })
        observer.observe(scrollEl)
        const onGesture = (event) => {
            if (event.type === 'pointerdown' && event.target !== scrollEl) return
            disconnectPin()
        }
        for (const type of GESTURES) scrollEl.addEventListener(type, onGesture, { passive: true })
        pin = { observer, scrollEl, onGesture }
    }

    function disconnectPin() {
        if (!pin) return
        pin.observer.disconnect()
        for (const type of GESTURES) pin.scrollEl.removeEventListener(type, pin.onGesture)
        pin = null
    }

    if (getCurrentScope()) onScopeDispose(disconnectPin)

    // ── Animations ───────────────────────────────────────────────────────────
    // Two counters: the global one drives the pin and `animating`, the block's one its own
    // `animating` and `onSettled`. Only the settle handler decrements (once): a cancel then
    // replay keeps the count above 0, so the pin is neither dropped nor re-captured.
    function play(target, keyframes, duration, easing, state) {
        const animation = target.animate(keyframes, { duration, easing })
        state.animations.add(animation)
        state.count += 1
        if (state.animating) state.animating.value = true
        running += 1
        if (running === 1) {
            animating.value = true
            connectPin()
        }
        let settled = false
        const settle = () => {
            if (settled) return
            settled = true
            state.animations.delete(animation)
            drop(state)
        }
        animation.finished.then(settle, settle)
        return animation
    }

    function drop(state) {
        state.count -= 1
        running -= 1
        if (running === 0) {
            animating.value = false
            disconnectPin()
        }
        if (state.count === 0) {
            if (state.animating) state.animating.value = false
            state.onSettled?.()
        }
    }

    // A run that cancelled the block's animations then waits for the render (§9) holds one
    // count meanwhile: the cancelled animations settle during the wait, and without the hold
    // the counts would drop to 0 before the replay (pin dropped then re-captured, onSettled
    // mid-change). Only taken while the block counts (then the global count is above 0 too,
    // so taking it never connects the pin). Returns the release, idempotent.
    function hold(state) {
        if (state.count === 0) return () => {}
        state.count += 1
        running += 1
        let released = false
        return () => {
            if (released) return
            released = true
            drop(state)
        }
    }

    function playHeight(el, fromPx, toPx, state) {
        const { heightMs, heightEasing } = readTimings()
        return play(el, heightKeyframes(fromPx, toPx), heightMs, heightEasing, state)
    }

    function playFade(el, from, to, state) {
        const { fadeMs, fadeEasing } = readTimings()
        return play(el, fadeKeyframes(from, to), fadeMs, fadeEasing, state)
    }

    // An interrupted animation rejects `finished`: the node is never left mounted.
    function whenSettled(animations, done) {
        Promise.allSettled(animations.map((animation) => animation.finished)).then(() => done())
    }

    /** Synchronous and idempotent: cancels this block's footer animations, ends a restore. */
    function cancelAll(wrapper) {
        const state = blocks.get(wrapper)
        if (!state) return
        const animations = [...state.animations]
        state.animations.clear()
        for (const animation of animations) animation.cancel()
        state.inset = null
        finalizeRestore(wrapper, state)
    }

    function finalizeRestore(wrapper, state) {
        if (!state.restore) return
        state.restore = null
        wrapper.removeAttribute(RESTORING)
        wrapper.style.height = ''
    }

    // Always against the list's padding box (what `inset` resolves against), never through
    // offsetParent: in flow it is the footer, maximized the list.
    function offsetsOf(block) {
        const container = block.closest(CONTAINER)
        if (!container) return null
        return paddingBoxOffsets(block.getBoundingClientRect(), container.getBoundingClientRect(), container)
    }

    function maximize(block, from, state) {
        if (!from) return
        const { heightMs, heightEasing } = readTimings()
        state.inset = play(block, insetKeyframes(from, FULL), heightMs, heightEasing, state)
    }

    // The block, back in flow, is held absolute over the list for the run; the wrapper keeps
    // its final height inline, so the chat never squeezes. `target` and H are measured once
    // the render settles (§9); meanwhile `state.restore` holds a token, so a cancelAll landing
    // during the wait (a newer change's post run, in the same flush as its pre bump, or a
    // leave) removes the attribute and makes this run stale.
    async function restore(wrapper, block, from, state) {
        const token = {}
        state.restore = token
        wrapper.setAttribute(RESTORING, '')
        await settleRender(wrapper, env)
        if (state.restore !== token) return
        if (!isVisible(wrapper)) {
            finalizeRestore(wrapper, state)
            return
        }
        const target = offsetsOf(block)
        if (!target) {
            finalizeRestore(wrapper, state)
            return
        }
        const naturalPx = wrapper.getBoundingClientRect().height
        wrapper.style.height = `${naturalPx}px`
        const { heightMs, heightEasing } = readTimings()
        const animation = play(block, restoreKeyframes(from ?? FULL, target), heightMs, heightEasing, state)
        state.restore = animation
        const end = () => {
            if (state.restore === animation) finalizeRestore(wrapper, state)
        }
        animation.finished.then(end, end)
    }

    // ── Shape changes ────────────────────────────────────────────────────────
    function attachBlock({ wrapperRef, blockRef = null, shape, maximized = NEVER, beforeMeasure = null, onSettled = null }) {
        const blockAnimating = ref(false)

        watch(wrapperRef, (wrapper) => {
            if (!wrapper) return
            const state = stateOf(wrapper)
            state.animating = blockAnimating
            state.onSettled = onSettled
            blockAnimating.value = state.count > 0
        }, { immediate: true, flush: 'sync' })

        // Pre flush: the state is new, the DOM still old. A running animation's current
        // height is the right start, so nothing is cancelled here.
        let change = null
        watch([shape, maximized], ([nextShape, nextMaximized], [prevShape, wasMaximized]) => {
            change = null
            const wrapper = wrapperRef.value
            if (!wrapper) return
            const block = blockRef?.value ?? null
            const state = stateOf(wrapper)
            // A post run still waiting for the render: no frame was painted since its pre run,
            // the screen still shows its prevPx (the DOM already has its new height).
            const prevPx = state.pendingFromPx ?? wrapper.getBoundingClientRect().height
            const insetRunning = !!state.inset && state.animations.has(state.inset)
            const growing = !wasMaximized && nextMaximized
            const shrinkingMidMaximize = wasMaximized && !nextMaximized && insetRunning
            const from = block && (growing || shrinkingMidMaximize) ? offsetsOf(block) : null
            capturePin()
            state.generation += 1
            change = { prevPx, from, wasMaximized, shapeChanged: nextShape !== prevShape, generation: state.generation }
        }, { flush: 'pre' })

        // Post flush: the DOM is new. Cancel first, so the measure is the natural height; the
        // measure waits for the Web Awesome elements to render (§9), so this run is async: a
        // newer change landing during the wait makes it stale.
        watch([shape, maximized], async ([, nextMaximized]) => {
            const pending = change
            change = null
            const wrapper = wrapperRef.value
            if (!pending || !wrapper) return
            const block = blockRef?.value ?? null
            const state = stateOf(wrapper)
            cancelAll(wrapper)
            beforeMeasure?.()
            // Nothing can play: no need to wait for the render. The pre run consumed any
            // pending prevPx: drop it.
            if (!enabled.value || !isVisible(wrapper)) {
                state.pendingFromPx = null
                state.pendingRun = null
                return
            }
            state.pendingFromPx = pending.prevPx
            state.pendingRun = pending
            const release = hold(state)
            try {
                await settleRender(wrapper, env)
                if (state.generation !== pending.generation) return
                const nextPx = wrapper.getBoundingClientRect().height
                const reduced = isReduced()
                const visible = isVisible(wrapper)

                // The block is or was maximized: every fade runs on the block (an opacity
                // animation on the wrapper would make it a stacking context under the composer).
                if (block && (pending.wasMaximized || nextMaximized)) {
                    if (!enabled.value || !visible) return
                    if (reduced) playFade(block, 0, 1, state)
                    else if (!pending.wasMaximized) maximize(block, pending.from, state)
                    else if (!nextMaximized && !pending.shapeChanged) await restore(wrapper, block, pending.from, state)
                    // Out of the flow before: its old height means nothing, fade only.
                    else playFade(block, 0, 1, state)
                    return
                }

                if (!shouldAnimate({ enabled: enabled.value, visible, fromPx: pending.prevPx, toPx: nextPx })) return
                if (!reduced) playHeight(wrapper, pending.prevPx, nextPx, state)
                playFade(wrapper, 0, 1, state)
            } finally {
                // Played, bailed out or stale: cleared, unless a newer run owns the value now.
                if (state.pendingRun === pending) {
                    state.pendingFromPx = null
                    state.pendingRun = null
                }
                release()
            }
        }, { flush: 'post' })

        return { animating: blockAnimating }
    }

    // ── Mount and unmount (Transition hooks) ─────────────────────────────────
    // Async (§9): `natural` is measured once the render settles. A leave (or a shape change
    // of the block) during the wait makes this enter stale: it plays nothing and still hands
    // `done` back (a no-op once the Transition cancelled the enter).
    async function footerEnter(el, done) {
        // Nothing can play: done at once, no wait.
        if (!enabled.value || !isVisible(el)) {
            done()
            return
        }
        const state = stateOf(el)
        const token = ++state.generation
        await settleRender(el, env)
        if (state.generation !== token) {
            done()
            return
        }
        const naturalPx = el.getBoundingClientRect().height
        if (!shouldAnimate({ enabled: enabled.value, visible: isVisible(el), fromPx: 0, toPx: naturalPx })) {
            done()
            return
        }
        const animations = []
        if (!isReduced()) animations.push(playHeight(el, 0, naturalPx, state))
        animations.push(playFade(el, 0, 1, state))
        whenSettled(animations, done)
    }

    function footerLeave(el, done) {
        capturePin()
        // A running animation's height is the right start (a leave interrupting an enter).
        const currentPx = el.getBoundingClientRect().height
        cancelAll(el)
        const state = stateOf(el)
        // A pending enter (or shape change) still waiting for the render is now stale.
        state.generation += 1
        // Out of reach while it fades: document-wide lookups skip [data-footer-leaving].
        el.inert = true
        el.setAttribute(LEAVING, '')
        const visible = isVisible(el)
        // A maximized block is absolute: the wrapper holds just the divider (under 1px on
        // fractional pixel ratios), so this fade is never gated on the height delta.
        const maximizedBlock = el.querySelector('.maximized')
        if (maximizedBlock) {
            if (!enabled.value || !visible) {
                done()
                return
            }
            whenSettled([playFade(maximizedBlock, 1, 0, state)], done)
            return
        }
        if (!shouldAnimate({ enabled: enabled.value, visible, fromPx: currentPx, toPx: 0 })) {
            done()
            return
        }
        const animations = []
        if (!isReduced()) animations.push(playHeight(el, currentPx, 0, state))
        animations.push(playFade(el, 1, 0, state))
        whenSettled(animations, done)
    }

    // Banners: the same enter, at mount (they have no leave motion). The promise is returned
    // so a throw is not an unhandled rejection.
    const vFooterEnter = {
        mounted(el) {
            return footerEnter(el, () => {})
        },
    }

    return { animating, attachBlock, capturePin, footerEnter, footerLeave, vFooterEnter }
}

/**
 * The session-switch gate: `arm()` closes it, and it opens again after nextTick then an
 * animation frame, once the new session's blocks have mounted and reshaped. A later arm()
 * makes an earlier release stale.
 */
export function createSwitchGate({ env = realEnv() } = {}) {
    const open = ref(true)
    let generation = 0
    function arm() {
        const current = ++generation
        open.value = false
        nextTick(() => {
            env.requestAnimationFrame(() => {
                if (current === generation) open.value = true
            })
        })
    }
    return { open, arm }
}

/** Called once in SessionItemsList's setup: provides the controller to the footer blocks. */
export function provideFooterMotion(options) {
    const motion = createFooterMotion({ env: realEnv(), ...options })
    provide(FOOTER_MOTION_KEY, motion)
    const { capturePin, footerEnter, footerLeave, vFooterEnter } = motion
    return { capturePin, footerEnter, footerLeave, vFooterEnter }
}

/**
 * Called in a footer block's setup. `wrapperRef` is the element whose height animates (the
 * block's single root), `blockRef` the maximizable element (null for the composer), `shape` a
 * computed string that changes exactly when the height must animate, `maximized` a
 * computed boolean. Returns `{ animating }`: true while this block has a footer animation.
 */
export function useFooterBlockMotion({
    wrapperRef,
    blockRef = null,
    shape,
    maximized,
    beforeMeasure,
    onSettled,
    motion = inject(FOOTER_MOTION_KEY, null),
}) {
    if (!motion) return { animating: ref(false) }
    return motion.attachBlock({ wrapperRef, blockRef, shape, maximized, beforeMeasure, onSettled })
}
