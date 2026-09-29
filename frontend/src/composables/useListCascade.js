// Session-list cascade (visual refresh step 5c): the Vue side of utils/listCascade.js.
// Design: docs/plans/2026-09-29-list-cascade-scheme-reveal-design.md §4.
//
// A list "arrives" the first time the unfiltered list holds a row after the mount or a
// scope change: from then until the start, every row is hidden (list-arriving). Two frames
// later (the scroller has measured its rows), the rows on screen cascade; when the list is
// scrolling to a held target outside that range, the start waits for that scroll, capped.
// Afterwards, a row noted live that is new in the list enters alone.
//
// One pre-flush watcher, so a row carries its class at its first render (a post-flush class
// would show the row one frame, then blink). An entry ends on a timer, not on animationend:
// a row unmounted mid-animation must not keep it.

import { onScopeDispose, reactive, ref, shallowRef, watch } from 'vue'
import {
    LIST_CASCADE_SETTLE_CAP_MS,
    listCascadeEndMs,
    pickLiveEntrances,
    planListCascade,
} from '../utils/listCascade.js'

const FRAME_FALLBACK_MS = 16

export function useListCascade({ items, getKey, sourceSize, scopeKey, getVisibleRange, env = globalThis }) {
    const phase = ref('idle')             // 'idle' | 'pending' | 'playing'
    const plan = shallowRef(new Map())    // key → index, while playing
    const live = reactive(new Map())      // key → index, live entrances
    const liveTimers = new Map()          // key → cancel()
    const cancels = new Set()             // every timer and frame still scheduled
    const noted = new Set()
    let previousKeys = new Set()
    let arrived = false
    let lastScope
    let generation = 0
    let held = null                       // { key, settled: Promise<void> }
    let cancelCap = null
    let capReached = null

    function later(fn, ms) {
        let cancel
        const id = env.setTimeout(() => {
            cancels.delete(cancel)
            fn()
        }, ms)
        cancel = () => {
            env.clearTimeout(id)
            cancels.delete(cancel)
        }
        cancels.add(cancel)
        return cancel
    }

    function frame(fn) {
        if (typeof env.requestAnimationFrame !== 'function') return later(fn, FRAME_FALLBACK_MS)
        let cancel
        const id = env.requestAnimationFrame(() => {
            cancels.delete(cancel)
            fn()
        })
        cancel = () => {
            env.cancelAnimationFrame?.(id)
            cancels.delete(cancel)
        }
        cancels.add(cancel)
        return cancel
    }

    function cancelAll() {
        for (const cancel of [...cancels]) cancel()
        liveTimers.clear()
        cancelCap = null
    }

    function reset() {
        generation++
        cancelAll()
        plan.value = new Map()
        live.clear()
        held = null
        capReached = null
        arrived = false
        phase.value = 'idle'
    }

    function currentKeys() {
        return (items.value ?? []).map(getKey)
    }

    function arrive() {
        arrived = true
        phase.value = 'pending'
        const gen = ++generation
        held = null
        capReached = new Promise((resolve) => {
            cancelCap = later(resolve, LIST_CASCADE_SETTLE_CAP_MS)
        })
        frame(() => {
            if (gen !== generation) return
            frame(() => begin(gen))
        })
    }

    function begin(gen) {
        if (gen !== generation) return
        const range = getVisibleRange()
        const target = held
        if (target && range) {
            const index = currentKeys().indexOf(target.key)
            if (index >= 0 && (index < range.start || index >= range.end)) {
                Promise.race([target.settled, capReached]).then(() => {
                    if (gen !== generation) return
                    frame(() => {
                        if (gen !== generation) return
                        start(getVisibleRange())
                    })
                })
                return
            }
        }
        start(range)
    }

    function start(range) {
        cancelCap?.()
        cancelCap = null
        held = null
        const next = range
            ? planListCascade({ keys: currentKeys(), visibleStart: range.start, visibleEnd: range.end })
            : new Map()
        if (!next.size) {
            plan.value = new Map()
            phase.value = 'idle'
            return
        }
        plan.value = next
        phase.value = 'playing'
        later(() => {
            plan.value = new Map()
            phase.value = 'idle'
        }, listCascadeEndMs(Math.max(...next.values())))
    }

    function enterLive(keys) {
        for (const key of keys) {
            liveTimers.get(key)?.()
            live.set(key, 0)
            liveTimers.set(key, later(() => {
                liveTimers.delete(key)
                live.delete(key)
            }, listCascadeEndMs(0)))
        }
    }

    function run() {
        const scope = scopeKey()
        if (scope !== lastScope) {
            reset()
            lastScope = scope
        }
        const keys = currentKeys()
        if (!arrived && sourceSize() > 0) arrive()
        else if (phase.value !== 'pending') enterLive(pickLiveEntrances({ previousKeys, keys, liveKeys: noted }))
        previousKeys = new Set(keys)
        noted.clear()
    }

    watch([scopeKey, items, sourceSize], run, { immediate: true, flush: 'pre' })

    function itemClass(item) {
        if (phase.value === 'pending') return 'list-arriving'
        const key = getKey(item)
        return plan.value.has(key) || live.has(key) ? 'list-entering' : null
    }

    function itemStyle(item) {
        const key = getKey(item)
        const index = plan.value.get(key) ?? live.get(key)
        return index === undefined ? null : { '--list-enter-index': index }
    }

    function noteLive(id) {
        noted.add(id)
    }

    function dropLive(id) {
        noted.delete(id)
        liveTimers.get(id)?.()
        liveTimers.delete(id)
        live.delete(id)
    }

    /** While pending, the start waits (capped) for `promise` when `key` is off screen. */
    function holdTarget(key, promise) {
        if (phase.value !== 'pending') return
        held = { key, settled: Promise.resolve(promise).then(() => {}, () => {}) }
    }

    onScopeDispose(() => {
        generation++
        cancelAll()
    })

    return { itemClass, itemStyle, noteLive, dropLive, holdTarget }
}
