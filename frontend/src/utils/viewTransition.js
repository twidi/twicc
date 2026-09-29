// The single owner of document view transitions (visual refresh step 5c): the color-scheme
// reveal, the tab crossfade and the overlay slide all go through runViewTransition, so no
// user leaves its class or timing on <html> for another. The only file that names
// startViewTransition.
// Design: docs/plans/2026-09-29-list-cascade-scheme-reveal-design.md §5.1 and §12.3.
//
// During a transition the page takes no pointer input (the pseudo-elements cover it), so
// three watchdogs bound it: a late old-state capture, a slow update, an animation overrun.
// A skipped transition still runs its update: the change lands, without animation.

import { nextTick } from 'vue'

const CLASS_PREFIX = 'twicc-vt-'
const UPDATE_CAP_MS = 3000
const FALLBACK_END_MS = 1000

let currentRun = 0
let depth = 0
let appliedProperties = []
// Settles when the latest transition's update callback is done (or failed); null when none
// is in flight.
let updatePending = null

/** True when the browser has same-document view transitions. */
export function supportsViewTransitions(env = globalThis) {
    return typeof env.document?.startViewTransition === 'function'
}

/** True while a transition's update callback runs. */
export function isViewTransitionUpdating() {
    return depth > 0
}

/**
 * Run `fn` once the in-flight transition's update is done, or at once when none is. For a
 * change made just before a transition starts (the overlay's tab bar switches on the click,
 * its transition starts in the same task): a CSS transition started then runs behind the
 * frozen old image and is over when the page shows again.
 */
export function afterViewTransitionUpdate(fn) {
    if (updatePending) updatePending.then(fn)
    else fn()
}

function clearMarks(root) {
    for (const name of [...root.classList]) {
        if (name.startsWith(CLASS_PREFIX)) root.classList.remove(name)
    }
    for (const name of appliedProperties) root.style.removeProperty(name)
    appliedProperties = []
}

/** End time (ms) of the longest view-transition pseudo animation, or FALLBACK_END_MS. */
function pseudoAnimationsEnd(root) {
    let end = null
    for (const animation of root.getAnimations?.({ subtree: true }) ?? []) {
        const effect = animation.effect
        if (typeof effect?.pseudoElement !== 'string' || !effect.pseudoElement.startsWith('::view-transition')) continue
        const endTime = Number(effect.getComputedTiming?.().endTime)
        if (Number.isFinite(endTime)) end = Math.max(end ?? 0, endTime)
    }
    return end ?? FALLBACK_END_MS
}

/**
 * Run `update` (a DOM change) in a document view transition of `kind`: `twicc-vt-<kind>` and
 * `properties` sit on <html> while it runs. `settle`: `update` may be async (a navigation);
 * the callback waits for it, then for Vue and one macrotask. Without support, or inside
 * another transition's update, `update` runs directly.
 */
export function runViewTransition(update, {
    kind, properties = {}, settle = false,
    startTimeoutMs = 150, updateTimeoutMs = 400, overrunMs = 150, env = globalThis,
} = {}) {
    const doc = env.document
    if (typeof doc?.startViewTransition !== 'function' || depth > 0) {
        update()
        return
    }

    const root = doc.documentElement
    const run = ++currentRun
    clearMarks(root)
    root.classList.add(`${CLASS_PREFIX}${kind}`)
    for (const [name, value] of Object.entries(properties)) root.style.setProperty(name, value)
    appliedProperties = Object.keys(properties)

    let transition = null
    let ran = false
    let callbackStarted = false
    let readySettled = false
    let finishedSettled = false
    let skipped = false
    const timers = new Set()

    function later(fn, ms) {
        const id = env.setTimeout(() => {
            timers.delete(id)
            fn()
        }, ms)
        timers.add(id)
        return () => {
            env.clearTimeout(id)
            timers.delete(id)
        }
    }

    function skip() {
        if (skipped || !transition) return
        skipped = true
        try {
            transition.skipTransition()
        } catch {
            // Already finished.
        }
    }

    function cleanup() {
        for (const id of timers) env.clearTimeout(id)
        timers.clear()
        if (run === currentRun) clearMarks(root)
    }

    let cancelStart = null
    let cancelUpdateWatch = null

    function begin() {
        depth++
        callbackStarted = true
        ran = true
        cancelStart?.()
        if (!readySettled && !skipped) cancelUpdateWatch = later(skip, updateTimeoutMs)
    }

    async function settleUpdate() {
        let cancelCap = null
        const capped = new Promise((resolve) => { cancelCap = later(resolve, UPDATE_CAP_MS) })
        try {
            await Promise.race([Promise.resolve(update()), capped])
        } finally {
            cancelCap()
        }
        await nextTick()
        await new Promise((resolve) => env.setTimeout(resolve, 0))
        await nextTick()
    }

    const callback = settle
        ? async () => {
            begin()
            try {
                await settleUpdate()
            } finally {
                depth--
            }
        }
        : () => {
            begin()
            try {
                update()
            } finally {
                depth--
            }
        }

    try {
        transition = doc.startViewTransition(callback)
    } catch {
        if (!ran) update()
        cleanup()
        return
    }

    if (!callbackStarted) {
        cancelStart = later(() => {
            if (!callbackStarted) skip()
        }, startTimeoutMs)
    }

    const onReadySettled = () => {
        readySettled = true
        cancelUpdateWatch?.()
    }
    transition.ready.then(() => {
        onReadySettled()
        if (finishedSettled) return
        later(() => {
            if (!finishedSettled) skip()
        }, pseudoAnimationsEnd(root) + overrunMs)
    }, onReadySettled)
    transition.updateCallbackDone.catch(() => {})
    const updateDone = transition.updateCallbackDone.then(() => {}, () => {})
    updatePending = updateDone
    updateDone.then(() => {
        if (updatePending === updateDone) updatePending = null
    })
    const onFinished = () => {
        finishedSettled = true
        cleanup()
    }
    transition.finished.then(onFinished, onFinished)
}
