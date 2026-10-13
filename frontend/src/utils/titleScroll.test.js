// Run with: node --test src/utils/titleScroll.test.js (from the frontend dir)
import test from 'node:test'
import assert from 'node:assert/strict'

import { createTitleScrollReturn, easeOutCubic, hiddenEdges } from './titleScroll.js'

function makeEnv({ reduced = false } = {}) {
    const env = { timers: new Map(), frames: [], nextId: 1, reduced }
    env.matchMedia = (query) => ({ matches: query === '(prefers-reduced-motion: reduce)' && env.reduced })
    env.setTimeout = (callback) => {
        const id = env.nextId++
        env.timers.set(id, callback)
        return id
    }
    env.clearTimeout = (id) => env.timers.delete(id)
    env.requestAnimationFrame = (callback) => {
        const id = env.nextId++
        env.frames.push({ id, callback })
        return id
    }
    env.cancelAnimationFrame = (id) => {
        env.frames = env.frames.filter((frame) => frame.id !== id)
    }
    env.fireTimers = () => {
        const callbacks = [...env.timers.values()]
        env.timers.clear()
        callbacks.forEach((callback) => callback())
    }
    env.runFrame = (now) => {
        const frames = env.frames
        env.frames = []
        frames.forEach((frame) => frame.callback(now))
    }
    return env
}

const DELAY = 1000
const DURATION = 300
const make = (env, scrollLeft) => {
    const el = { scrollLeft }
    return { el, ctrl: createTitleScrollReturn(el, env, { delay: DELAY, duration: DURATION }) }
}

test('hiddenEdges: nothing hidden when the content fits', () => {
    assert.deepEqual(hiddenEdges({ scrollLeft: 0, scrollWidth: 100, clientWidth: 100 }), { start: false, end: false })
})

test('hiddenEdges: end hidden at the start, start hidden at the end, both in the middle', () => {
    assert.deepEqual(hiddenEdges({ scrollLeft: 0, scrollWidth: 300, clientWidth: 100 }), { start: false, end: true })
    assert.deepEqual(hiddenEdges({ scrollLeft: 200, scrollWidth: 300, clientWidth: 100 }), { start: true, end: false })
    assert.deepEqual(hiddenEdges({ scrollLeft: 80, scrollWidth: 300, clientWidth: 100 }), { start: true, end: true })
})

test('hiddenEdges: sub-pixel rounding does not show an edge', () => {
    assert.deepEqual(hiddenEdges({ scrollLeft: 0.4, scrollWidth: 300, clientWidth: 100 }).start, false)
    assert.deepEqual(hiddenEdges({ scrollLeft: 199.6, scrollWidth: 300, clientWidth: 100 }).end, false)
})

test('easeOutCubic runs from 0 to 1', () => {
    assert.equal(easeOutCubic(0), 0)
    assert.equal(easeOutCubic(1), 1)
    assert.ok(easeOutCubic(0.5) > 0.5)
})

test('the title goes back to its start after the delay, with an animation', () => {
    const env = makeEnv()
    const { el, ctrl } = make(env, 120)
    ctrl.onTouchStart()
    ctrl.onTouchEnd()
    assert.equal(env.timers.size, 1)
    env.fireTimers()
    env.runFrame(0)
    assert.equal(el.scrollLeft, 120)
    env.runFrame(150)
    assert.ok(el.scrollLeft > 0 && el.scrollLeft < 120)
    env.runFrame(300)
    assert.equal(el.scrollLeft, 0)
    assert.equal(env.frames.length, 0)
})

test('a scroll event restarts the timer (inertia)', () => {
    const env = makeEnv()
    const { ctrl } = make(env, 50)
    ctrl.onTouchEnd()
    const first = [...env.timers.keys()][0]
    ctrl.onScroll()
    assert.equal(env.timers.size, 1)
    assert.notEqual([...env.timers.keys()][0], first)
})

test('no timer while the finger is down', () => {
    const env = makeEnv()
    const { ctrl } = make(env, 50)
    ctrl.onTouchStart()
    ctrl.onScroll()
    assert.equal(env.timers.size, 0)
})

test('no timer when the title is already at its start', () => {
    const env = makeEnv()
    const { ctrl } = make(env, 0)
    ctrl.onTouchEnd()
    assert.equal(env.timers.size, 0)
})

test('a new touch cancels the timer and the running return', () => {
    const env = makeEnv()
    const { el, ctrl } = make(env, 100)
    ctrl.onTouchEnd()
    ctrl.onTouchStart()
    assert.equal(env.timers.size, 0)
    ctrl.onTouchEnd()
    env.fireTimers()
    env.runFrame(0)
    ctrl.onTouchStart()
    assert.equal(env.frames.length, 0)
    const stopped = el.scrollLeft
    env.runFrame(200)
    assert.equal(el.scrollLeft, stopped)
})

test('the scroll events of the return do not restart the timer', () => {
    const env = makeEnv()
    const { ctrl } = make(env, 100)
    ctrl.onTouchEnd()
    env.fireTimers()
    env.runFrame(0)
    env.runFrame(100)
    ctrl.onScroll()
    assert.equal(env.timers.size, 0)
})

test('reduced motion: the title jumps back without animation', () => {
    const env = makeEnv({ reduced: true })
    const { el, ctrl } = make(env, 100)
    ctrl.onTouchEnd()
    env.fireTimers()
    assert.equal(el.scrollLeft, 0)
    assert.equal(env.frames.length, 0)
})

test('reset and destroy cancel everything', () => {
    const env = makeEnv()
    const { el, ctrl } = make(env, 100)
    ctrl.onTouchEnd()
    ctrl.reset()
    assert.equal(el.scrollLeft, 0)
    assert.equal(env.timers.size, 0)
    el.scrollLeft = 80
    ctrl.onTouchEnd()
    env.fireTimers()
    ctrl.destroy()
    assert.equal(env.frames.length, 0)
})
