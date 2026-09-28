// wa-details open/close motion (visual refresh step 4b).
// Design: docs/plans/2026-09-27-details-motion-design.md.
//
// Web Awesome measures the body before Vue renders the lazy content of a card, so the
// card animates toward an empty body and then jumps. TwiCC owns the animation instead:
// this module replaces WaDetails.prototype.handleOpenChange (and nothing else). A card
// opened by the user grows to its rendered content, then its height follows the content
// (pinned inline, animated on each change) until it closes; the fold keeps the content
// visible. Instant opens (duration 0, set by the components on restore) stay instant.

import WaDetails from '@awesome.me/webawesome/dist/components/details/details.js'
import { WaShowEvent } from '@awesome.me/webawesome/dist/events/show.js'
import { WaAfterShowEvent } from '@awesome.me/webawesome/dist/events/after-show.js'
import { WaHideEvent } from '@awesome.me/webawesome/dist/events/hide.js'
import { WaAfterHideEvent } from '@awesome.me/webawesome/dist/events/after-hide.js'

const LOADING_ATTRIBUTE = 'data-motion-loading'
const LOADING_DELAY_MS = 150
const LOADING_EXPIRY_MS = 10000

// ---------------------------------------------------------------------------
// Pure helpers
// ---------------------------------------------------------------------------

/** A CSS <time> as milliseconds: `380ms` → 380, `0.38s` → 380, empty → 0. */
export function parseDurationMs(value) {
    const text = String(value ?? '').trim().toLowerCase()
    const number = parseFloat(text)
    if (!Number.isFinite(number)) return 0
    if (text.endsWith('ms')) return number
    if (text.endsWith('s')) return Math.round(number * 1000 * 1000) / 1000
    return number
}

/** Where a gesture starts: what is on screen when it interrupts a motion, else the settled state. */
export function startState({ kind, interrupting, renderedHeight, renderedOpacity, settledHeight }) {
    if (interrupting) return { height: renderedHeight, opacity: renderedOpacity }
    return kind === 'open' ? { height: 0, opacity: 0 } : { height: settledHeight, opacity: 1 }
}

export function openKeyframes(start, to, reduced) {
    if (reduced) return [{ opacity: start.opacity }, { opacity: 1 }]
    return [{ height: `${start.height}px`, opacity: start.opacity }, { height: `${to}px`, opacity: 1 }]
}

/** `fromOpacity` is below 1 when the follow re-targets an opening still fading in. */
export function followKeyframes(from, to, fromOpacity) {
    return [{ height: `${from}px`, opacity: fromOpacity }, { height: `${to}px`, opacity: 1 }]
}

export function closeKeyframes(start, reduced) {
    const end = reduced ? `${start.height}px` : '0px'
    return [{ height: `${start.height}px`, opacity: start.opacity }, { height: end, opacity: 0 }]
}

/**
 * What a follow frame does with a content height change.
 * @returns {{action: 'none'|'unpin'}|{action: 'set', to: number}|{action: 'animate', from: number, to: number}}
 */
export function followStep({ running, renderedHeight, lastHeight, target, descendantAnimating, unpinned }) {
    if (descendantAnimating) return unpinned ? { action: 'none' } : { action: 'unpin' }
    // Unpinned, the card is auto and already shows `target`.
    if (unpinned) return { action: 'set', to: target }
    // `lastHeight` is always the running animation's target.
    if (Math.abs(target - lastHeight) < 0.5) return { action: 'none' }
    return { action: 'animate', from: running ? renderedHeight : lastHeight, to: target }
}

// ---------------------------------------------------------------------------
// Environment (the browser; node tests swap in fakes)
// ---------------------------------------------------------------------------

let env = globalThis

/** Test hook: use `fakeEnv` for timers, frames, ResizeObserver, styles; drop the shared state. */
export function resetDetailsMotionForTests(fakeEnv = globalThis) {
    env = fakeEnv
    resizeObserver = null
    animating.clear()
}

function readMotion() {
    const style = env.getComputedStyle(env.document.documentElement)
    const read = (name) => style.getPropertyValue(name).trim()
    return {
        // Fallbacks only matter if motion.css is missing: the values of its tokens.
        dur2: read('--motion-dur-2') ? parseDurationMs(read('--motion-dur-2')) : 200,
        dur3: read('--motion-dur-3') ? parseDurationMs(read('--motion-dur-3')) : 380,
        easing: read('--motion-ease-out-height') || 'cubic-bezier(0.25, 0.46, 0.45, 0.94)',
        reduced: parseFloat(read('--motion-amount')) === 0,
    }
}

const isReducedNow = () => readMotion().reduced

/** Longest wait for an idle main thread before an opening starts anyway. */
export const IDLE_TIMEOUT_MS = 250

function waitForIdle() {
    return new Promise((resolve) => {
        if (env.requestIdleCallback) env.requestIdleCallback(resolve, { timeout: IDLE_TIMEOUT_MS })
        else env.setTimeout(resolve, 0)
    })
}

function isInstant(body, property) {
    return parseDurationMs(env.getComputedStyle(body).getPropertyValue(property)) === 0
}

function renderedOpacityOf(body) {
    const opacity = parseFloat(env.getComputedStyle(body).opacity)
    return Number.isFinite(opacity) ? opacity : 1
}

const contentSlot = (el) => el.shadowRoot?.querySelector('[part~="content"]') ?? null

// The slot is a formatting root (motion.css): its height is exactly what the body needs,
// and it does not depend on the body's own (pinned) height.
const contentHeight = (el) => contentSlot(el)?.offsetHeight ?? 0

const noop = () => {}

// ---------------------------------------------------------------------------
// Per-details state
// ---------------------------------------------------------------------------

const states = new WeakMap()
/** Details with a module animation running (the latest one of their state). */
const animating = new Set()
const slotOwners = new WeakMap()
let resizeObserver = null

function stateOf(el) {
    let state = states.get(el)
    if (!state) {
        state = {
            token: 0, lastKind: null, animation: null, pending: false, followed: false, observed: false,
            reduced: false, lastHeight: 0, rafId: 0, loadingTimer: 0, loadingExpiry: 0, unpinned: false,
        }
        states.set(el, state)
    }
    return state
}

/** Test hook: the state entry of a details, if any. */
export function detailsMotionStateOf(el) {
    return states.get(el)
}

function observer() {
    // Created on first use, so the module imports in node before a test installs a fake.
    if (!resizeObserver) {
        resizeObserver = new env.ResizeObserver((entries) => {
            // Never write a height here (it would resize the scroller item inside the
            // observer: "ResizeObserver loop"); only schedule the frame that does.
            for (const entry of entries) {
                const el = slotOwners.get(entry.target)
                if (el) scheduleFollow(el)
            }
        })
    }
    return resizeObserver
}

function observe(el, state) {
    const slot = contentSlot(el)
    if (!slot) return
    slotOwners.set(slot, el)
    observer().observe(slot)
    state.observed = true
}

function clearLoading(el, state) {
    if (state.loadingTimer) env.clearTimeout(state.loadingTimer)
    if (state.loadingExpiry) env.clearTimeout(state.loadingExpiry)
    state.loadingTimer = 0
    state.loadingExpiry = 0
    el.removeAttribute(LOADING_ATTRIBUTE)
}

// Clip the body vertically only: its height moves, and what overflows at the bottom must
// not paint over the next card. Sideways stays visible, because some content places a
// toolbar just outside its block (Thinking's markdown toolbar, SessionItem.vue).
// `clip` (not `hidden`) creates no scroll container, so a focus inside cannot scroll it.
function clip(body) {
    body.style.overflowX = 'visible'
    body.style.overflowY = 'clip'
}

function unclip(body) {
    body.style.overflowX = ''
    body.style.overflowY = ''
}

function stopFollowing(el, state) {
    if (state.observed) {
        const slot = contentSlot(el)
        if (slot) resizeObserver?.unobserve(slot)
    }
    state.observed = false
    if (state.rafId) env.cancelAnimationFrame(state.rafId)
    state.rafId = 0
    clearLoading(el, state)
    const style = el.body.style
    style.height = ''
    style.opacity = ''
    unclip(el.body)
    state.followed = false
    state.unpinned = false
}

function pin(el, state, height) {
    el.body.style.height = `${height}px`
    state.unpinned = false
}

// ---------------------------------------------------------------------------
// Nested cards
// ---------------------------------------------------------------------------

/** The closest wa-details above `el`, crossing shadow roots. */
function parentDetails(el) {
    let found = el.parentElement?.closest('wa-details') ?? null
    let node = el
    while (!found) {
        const host = node.getRootNode?.()?.host
        if (!host) return null
        found = host.closest?.('wa-details') ?? null
        node = host
    }
    return found
}

function* ancestorDetails(el) {
    for (let ancestor = parentDetails(el); ancestor; ancestor = parentDetails(ancestor)) yield ancestor
}

function followedAncestors(el) {
    const list = []
    for (const ancestor of ancestorDetails(el)) {
        const state = states.get(ancestor)
        if (state?.followed) list.push([ancestor, state])
    }
    return list
}

/** Let a followed ancestor grow and shrink with an inner motion in the same frame. */
function unpinAncestors(el) {
    for (const [ancestor, state] of followedAncestors(el)) {
        state.animation?.cancel()
        ancestor.body.style.height = ''
        ancestor.body.style.opacity = ''
        state.unpinned = true
    }
}

function hasAnimatingDescendant(el) {
    for (const other of animating) {
        if (other === el) continue
        for (const ancestor of ancestorDetails(other)) {
            if (ancestor === el) return true
        }
    }
    return false
}

function startAnimation(el, state, keyframes, options) {
    const animation = el.body.animate(keyframes, options)
    state.animation = animation
    animating.add(el)
    animation.finished.catch(noop).then(() => {
        // A cancelled animation replaced by a newer one must not end the "animating" state.
        if (state.animation === animation) animating.delete(el)
        // The ancestors re-pin in their own frame, even with no size report.
        for (const [ancestor] of followedAncestors(el)) scheduleFollow(ancestor)
    })
    unpinAncestors(el)
    return animation
}

// ---------------------------------------------------------------------------
// Follow
// ---------------------------------------------------------------------------

function scheduleFollow(el) {
    const state = states.get(el)
    if (!state?.observed || state.rafId) return
    const token = state.token
    // Like the opening: wait for an idle main thread before the follow frame, so a heavy
    // content render (a long highlighted file arriving in a Result) is done before the
    // height animation starts. -1 marks "waiting for idle"; stopFollowing resets it.
    state.rafId = -1
    waitForIdle().then(() => {
        if (state.rafId !== -1 || state.token !== token) return
        state.rafId = env.requestAnimationFrame(() => followFrame(el, state, token))
    })
}

function followFrame(el, state, token) {
    state.rafId = 0
    if (state.token !== token) return
    const slot = contentSlot(el)
    const body = el.body
    if (!el.isConnected || !slot) {
        stopFollowing(el, state)
        return
    }
    // Hidden tab or display: none ancestor: no further size change would be reported.
    if (slot.offsetHeight === 0 && body.offsetParent === null) {
        stopFollowing(el, state)
        return
    }
    if (el.hasAttribute(LOADING_ATTRIBUTE) && slot.assignedElements().length) clearLoading(el, state)
    if (state.reduced || !state.followed) return

    const target = slot.offsetHeight
    // Read before any cancel: afterwards they would give the pinned values.
    const renderedHeight = body.offsetHeight
    const fromOpacity = renderedOpacityOf(body)
    const step = followStep({
        running: state.animation?.playState === 'running',
        renderedHeight,
        lastHeight: state.lastHeight,
        target,
        descendantAnimating: hasAnimatingDescendant(el),
        unpinned: state.unpinned,
    })

    if (step.action === 'unpin') {
        state.animation?.cancel()
        body.style.height = ''
        body.style.opacity = ''
        state.unpinned = true
    } else if (step.action === 'set' || (step.action === 'animate' && isReducedNow())) {
        pin(el, state, step.to)
        state.lastHeight = step.to
    } else if (step.action === 'animate') {
        const motion = readMotion()
        state.animation?.cancel()
        pin(el, state, step.to)
        startAnimation(el, state, followKeyframes(step.from, step.to, fromOpacity), {
            duration: motion.dur2,
            easing: motion.easing,
        })
        state.lastHeight = step.to
    }
}

function armLoadingLine(el, state, token) {
    state.loadingTimer = env.setTimeout(() => {
        state.loadingTimer = 0
        if (state.token !== token || !el.open) return
        const slot = contentSlot(el)
        if (!slot || slot.assignedElements().length) return
        el.setAttribute(LOADING_ATTRIBUTE, '')
        // Content may legitimately be empty: never show the line forever.
        state.loadingExpiry = env.setTimeout(() => {
            state.loadingExpiry = 0
            if (state.token === token) el.removeAttribute(LOADING_ATTRIBUTE)
        }, LOADING_EXPIRY_MS)
    }, LOADING_DELAY_MS)
}

// ---------------------------------------------------------------------------
// The replacement handleOpenChange
// ---------------------------------------------------------------------------

/**
 * Same contract as Web Awesome's (events, prevention, closeOthersWithSameName,
 * isAnimating, inner <details> open during the whole open and fold). Every await is
 * followed by a token check: a superseded gesture touches nothing.
 */
export async function handleOpenChange() {
    const el = this
    const body = el.body
    const state = stateOf(el)
    const kind = el.open ? 'open' : 'close'

    const token = ++state.token
    const interrupting = state.animation?.playState === 'running' || state.pending
    const wasFollowed = state.followed
    const wasOpening = el.isAnimating && state.lastKind === 'open'
    state.lastKind = kind
    // Before changing anything: what is on screen now.
    const renderedHeight = body.offsetHeight
    const renderedOpacity = renderedOpacityOf(body)
    state.animation?.cancel()
    state.pending = false
    stopFollowing(el, state)
    // clip, not hidden: no scroll container, so a focus inside cannot scroll the body.
    clip(body)

    if (kind === 'open') await openGesture(el, state, token, interrupting, renderedHeight, renderedOpacity)
    else await closeGesture(el, state, token, interrupting, renderedHeight, renderedOpacity, wasFollowed, wasOpening)
}

async function openGesture(el, state, token, interrupting, renderedHeight, renderedOpacity) {
    const body = el.body
    el.details.open = true
    const show = new WaShowEvent()
    el.dispatchEvent(show)
    if (show.defaultPrevented) {
        el.open = false
        el.details.open = false
        unclip(body)
        el.isAnimating = false
        return
    }
    el.closeOthersWithSameName()

    if (isInstant(body, '--show-duration')) {
        body.style.height = 'auto'
        unclip(body)
        el.isAnimating = false
        el.dispatchEvent(new WaAfterShowEvent())
        return
    }

    el.isAnimating = true
    const motion = readMotion()
    state.reduced = motion.reduced
    const start = startState({ kind: 'open', interrupting, renderedHeight, renderedOpacity })
    // Hold the start state, so a frame painted before the measure shows it, not the content.
    body.style.height = `${start.height}px`
    body.style.opacity = String(start.opacity)
    state.pending = true

    // Wait until the main thread is idle, then for the next frame: the lazy content (Vue's
    // render, the Lit renders it triggers, a code viewer's highlighting) is done by then. A
    // height animation runs on the main thread, so starting it while that work is still
    // going freezes its first frames (measured on a phone: jumps of 100+ px).
    await waitForIdle()
    if (state.token !== token) return
    await new Promise((resolve) => env.requestAnimationFrame(resolve))
    if (state.token !== token) return
    state.pending = false

    const height = contentHeight(el)
    body.style.opacity = ''
    if (motion.reduced) body.style.height = 'auto'
    else pin(el, state, height)
    startAnimation(el, state, openKeyframes(start, height, motion.reduced), {
        duration: motion.reduced ? motion.dur2 : motion.dur3,
        easing: motion.easing,
    })
    state.lastHeight = height
    state.followed = !motion.reduced
    // Now: content arriving during the opening is followed from the first frame.
    observe(el, state)
    armLoadingLine(el, state, token)

    // A follow may re-target the opening: wait for the replacement too. A cancelled,
    // non-running animation (a nested card's unpin) counts as settled.
    for (;;) {
        const animation = state.animation
        await animation.finished.catch(noop)
        if (state.token !== token) return
        if (state.animation !== animation && state.animation?.playState === 'running') continue
        break
    }
    el.isAnimating = false
    el.dispatchEvent(new WaAfterShowEvent())
}

async function closeGesture(el, state, token, interrupting, renderedHeight, renderedOpacity, wasFollowed, wasOpening) {
    const body = el.body
    const hide = new WaHideEvent()
    el.dispatchEvent(hide)
    if (hide.defaultPrevented) {
        el.details.open = true
        el.open = true
        // Restore the open state the common start broke.
        if (wasFollowed || wasOpening) {
            state.followed = !state.reduced
            observe(el, state)
            if (state.reduced) body.style.height = 'auto'
            else {
                const height = contentHeight(el)
                pin(el, state, height)
                state.lastHeight = height
            }
            el.isAnimating = false
            if (wasOpening) el.dispatchEvent(new WaAfterShowEvent())
        } else {
            unclip(body)
            el.isAnimating = false
        }
        return
    }

    if (isInstant(body, '--hide-duration')) {
        body.style.height = 'auto'
        unclip(body)
        el.isAnimating = false
        el.details.open = false
        el.dispatchEvent(new WaAfterHideEvent())
        return
    }

    el.isAnimating = true
    // Read live: a card opened instantly has no stored value.
    const motion = readMotion()
    const start = startState({ kind: 'close', interrupting, renderedHeight, renderedOpacity, settledHeight: renderedHeight })
    body.style.height = 'auto'
    const animation = startAnimation(el, state, closeKeyframes(start, motion.reduced), {
        duration: motion.reduced ? motion.dur2 : motion.dur3,
        easing: motion.easing,
    })
    await animation.finished.catch(noop)
    // A reopen during the fold returns here: the card is open again.
    if (state.token !== token) return

    body.style.height = 'auto'
    unclip(body)
    el.isAnimating = false
    el.details.open = false
    el.dispatchEvent(new WaAfterHideEvent())
}

// ---------------------------------------------------------------------------
// Installation
// ---------------------------------------------------------------------------

/** Replace `target.prototype.handleOpenChange` (idempotent). Called by the SPA and share viewer entries. */
export function installDetailsMotion(target = WaDetails) {
    const proto = target?.prototype
    if (proto?.handleOpenChange === handleOpenChange) return
    if (typeof proto?.handleOpenChange !== 'function') {
        throw new Error(
            'installDetailsMotion: WaDetails.prototype.handleOpenChange is not a function; '
            + 'Web Awesome changed wa-details (re-read docs/plans/2026-09-27-details-motion-design.md §1.1)',
        )
    }
    proto.handleOpenChange = handleOpenChange
}
