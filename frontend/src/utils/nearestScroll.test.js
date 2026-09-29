// Run with: node --test src/utils/nearestScroll.test.js (from the frontend dir)
// Reveal the open row only near an edge (visual refresh step 6a-bis §10,
// docs/plans/2026-09-29-sidebar-row-glide-design.md): the one nearest computation shared
// by the virtual scroller and the artifacts list, on plain numbers; and the smooth reveal
// (§10.7): when it is smooth, and the smooth scroll with its end race.
import test from 'node:test'
import assert from 'node:assert/strict'

import { nearestScrollTop, revealBehavior, startSmoothScroll } from './nearestScroll.js'

// A 400px viewport scrolled to 1000 in a list that can scroll to 3000, zone margins 75 / 121.
const base = { scrollTop: 1000, viewport: 400, scrollMax: 3000, marginTop: 75, marginBottom: 121 }

test('a row inside the zone: the current scroll', () => {
    assert.equal(nearestScrollTop({ ...base, top: 1100, height: 36 }), 1000)
    // On both zone edges exactly: still inside.
    assert.equal(nearestScrollTop({ ...base, top: 1075, height: 36 }), 1000)
    assert.equal(nearestScrollTop({ ...base, top: 1279 - 36, height: 36 }), 1000)
})

test('a row above the zone: its top comes to the top band edge', () => {
    assert.equal(nearestScrollTop({ ...base, top: 1050, height: 36 }), 1050 - 75)
    assert.equal(nearestScrollTop({ ...base, top: 200, height: 36 }), 200 - 75, 'far above')
})

test('a row below the zone: its bottom comes to the bottom band edge', () => {
    assert.equal(nearestScrollTop({ ...base, top: 1260, height: 36 }), 1296 - 400 + 121)
    assert.equal(nearestScrollTop({ ...base, top: 2500, height: 36 }), 2536 - 400 + 121, 'far below')
})

test('a row that does not fit, zone < h <= viewport - marginBottom: its bottom at the bottom edge', () => {
    // Zone: 400 - 75 - 121 = 204. h = 250: over the zone, under 400 - 121 = 279.
    assert.equal(nearestScrollTop({ ...base, top: 1500, height: 250 }), 1750 - 400 + 121)
    // The same when it starts above the zone (rule 2 comes before rule 3).
    assert.equal(nearestScrollTop({ ...base, top: 900, height: 250 }), 1150 - 400 + 121)
})

test('a row that does not fit, h > viewport - marginBottom: its top', () => {
    assert.equal(nearestScrollTop({ ...base, top: 1500, height: 300 }), 1500)
    assert.equal(nearestScrollTop({ ...base, top: 900, height: 300 }), 900)
})

test('a zero or negative zone: the row\'s top', () => {
    const short = { scrollTop: 500, viewport: 150, scrollMax: 3000, marginTop: 75, marginBottom: 121.875 }
    // 36 >= 150 - 121.875: no row can pass the zone test, the top wins.
    assert.equal(nearestScrollTop({ ...short, top: 560, height: 36 }), 560)
    assert.equal(nearestScrollTop({ ...short, top: 100, height: 36 }), 100)
    assert.equal(nearestScrollTop({ ...short, top: 900, height: 36 }), 900)
})

test('a negative marginTop (the list padding shifts the zone)', () => {
    const shifted = { ...base, marginTop: -4 }
    assert.equal(nearestScrollTop({ ...shifted, top: 997, height: 36 }), 1000, 'inside: -4 lets it start 3px above')
    assert.equal(nearestScrollTop({ ...shifted, top: 990, height: 36 }), 994)
})

test('clamped at 0 and at scrollMax', () => {
    assert.equal(nearestScrollTop({ ...base, top: 30, height: 36 }), 0, 'top band above the list start')
    assert.equal(nearestScrollTop({ ...base, scrollTop: 2800, top: 3380, height: 36 }), 3000, 'bottom band past the end')
    assert.equal(nearestScrollTop({ ...base, scrollTop: 0, scrollMax: -20, top: 500, height: 36 }), 0, 'no scroll range')
})

test('margins default to 0: plain nearest', () => {
    const plain = { scrollTop: 1000, viewport: 400, scrollMax: 3000 }
    assert.equal(nearestScrollTop({ ...plain, top: 1000, height: 36 }), 1000)
    assert.equal(nearestScrollTop({ ...plain, top: 1364, height: 36 }), 1000)
    assert.equal(nearestScrollTop({ ...plain, top: 990, height: 36 }), 990)
    assert.equal(nearestScrollTop({ ...plain, top: 1370, height: 36 }), 1006)
})

// ---------------------------------------------------------------------------
// revealBehavior (§10.7)
// ---------------------------------------------------------------------------

test('revealBehavior: smooth within the viewport, else auto', () => {
    const base = { viewport: 300, reduced: false, allowed: true }
    assert.equal(revealBehavior({ ...base, distance: 120 }), 'smooth')
    assert.equal(revealBehavior({ ...base, distance: 300 }), 'smooth', 'the viewport itself')
    assert.equal(revealBehavior({ ...base, distance: 301 }), 'auto', 'above the viewport')
    assert.equal(revealBehavior({ ...base, distance: 120, reduced: true }), 'auto', 'reduced motion')
    assert.equal(revealBehavior({ ...base, distance: 120, allowed: false }), 'auto', 'not allowed')
    assert.equal(revealBehavior({ ...base, distance: 0 }), 'auto', 'no distance')
})

// ---------------------------------------------------------------------------
// startSmoothScroll (§10.7)
// ---------------------------------------------------------------------------

/** Fake timers on a manual clock. */
function makeEnv() {
    const env = { now: 0, timers: new Map(), nextId: 1 }
    env.setTimeout = (fn, ms) => {
        const id = env.nextId++
        env.timers.set(id, { fn, at: env.now + ms })
        return id
    }
    env.clearTimeout = (id) => { env.timers.delete(id) }
    env.advance = (ms) => {
        env.now += ms
        for (const [id, t] of [...env.timers.entries()].sort((a, b) => a[1].at - b[1].at)) {
            if (t.at > env.now) break
            env.timers.delete(id)
            t.fn()
        }
    }
    return env
}

/** A fake scroll container: records scrollTo, keeps its listeners. */
function makeElement(scrollTop = 0) {
    const listeners = new Map()
    return {
        scrollTop,
        calls: [],
        scrollTo(options) { this.calls.push(options) },
        addEventListener(type, handler) {
            if (!listeners.has(type)) listeners.set(type, new Set())
            listeners.get(type).add(handler)
        },
        removeEventListener(type, handler) { listeners.get(type)?.delete(handler) },
        dispatch(type) { for (const handler of [...(listeners.get(type) ?? [])]) handler({ type }) },
        count(type) { return listeners.get(type)?.size ?? 0 },
    }
}

const flushMicrotasks = () => new Promise((resolve) => setImmediate(resolve))

function tracked(promise) {
    const state = { resolved: false }
    promise.then(() => { state.resolved = true })
    return state
}

test('startSmoothScroll: a smooth scrollTo to the target', () => {
    const env = makeEnv()
    const el = makeElement(100)
    startSmoothScroll(el, 250, env)
    assert.deepEqual(el.calls, [{ top: 250, behavior: 'smooth' }])
    assert.equal(el.count('scrollend'), 1)
    assert.equal(env.timers.size, 1)
})

test('startSmoothScroll: done on an at-target scrollend, the timer cleared and the listener removed', async () => {
    const env = makeEnv()
    const el = makeElement(100)
    const { done } = startSmoothScroll(el, 250, env)
    const state = tracked(done)
    el.scrollTop = 250.5
    el.dispatch('scrollend')
    await flushMicrotasks()
    assert.equal(state.resolved, true)
    assert.equal(env.timers.size, 0, 'the timer cleared')
    assert.equal(el.count('scrollend'), 0, 'the listener removed')
})

test('startSmoothScroll: a scrollend away from the target is ignored, the listener stays', async () => {
    const env = makeEnv()
    const el = makeElement(100)
    const { done } = startSmoothScroll(el, 250, env)
    const state = tracked(done)
    el.scrollTop = 180
    el.dispatch('scrollend')
    await flushMicrotasks()
    assert.equal(state.resolved, false)
    assert.equal(el.count('scrollend'), 1)
    el.scrollTop = 250
    el.dispatch('scrollend')
    await flushMicrotasks()
    assert.equal(state.resolved, true, 'a later at-target scrollend still ends it')
})

test('startSmoothScroll: done after 600ms without a scrollend, the listener removed', async () => {
    const env = makeEnv()
    const el = makeElement(100)
    const { done } = startSmoothScroll(el, 250, env)
    const state = tracked(done)
    env.advance(599)
    await flushMicrotasks()
    assert.equal(state.resolved, false)
    env.advance(1)
    await flushMicrotasks()
    assert.equal(state.resolved, true)
    assert.equal(el.count('scrollend'), 0, 'the listener removed')
})

test('startSmoothScroll: cancel resolves done and cleans up', async () => {
    const env = makeEnv()
    const el = makeElement(100)
    const { done, cancel } = startSmoothScroll(el, 250, env)
    const state = tracked(done)
    cancel()
    await flushMicrotasks()
    assert.equal(state.resolved, true)
    assert.equal(env.timers.size, 0, 'the timer cleared')
    assert.equal(el.count('scrollend'), 0, 'the listener removed')
    cancel()
    env.advance(600)
    el.scrollTop = 250
    el.dispatch('scrollend')
})
