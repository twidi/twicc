// Run with: node --test src/utils/chatReveal.test.js (from the frontend dir)
// Chat reveal controller (visual refresh step 5a, docs/plans/2026-09-28-chat-entrances-skeleton-design.md §7.2):
// when the chat is hidden and when the skeleton shows, on a fake clock.
import test from 'node:test'
import assert from 'node:assert/strict'

import { CHAT_SKELETON_DELAY_MS, CHAT_SKELETON_MIN_VISIBLE_MS, createChatReveal } from './chatReveal.js'

function makeClock() {
    const clock = { now: 0, timers: new Map(), nextId: 1 }
    clock.setTimeout = (fn, ms) => {
        const id = clock.nextId++
        clock.timers.set(id, { fn, at: clock.now + ms })
        return id
    }
    clock.clearTimeout = (id) => { clock.timers.delete(id) }
    /** Run every timer due within `ms` (a 0ms timer runs at advance(0)), in time order. */
    clock.advance = (ms = 0) => {
        const target = clock.now + ms
        for (;;) {
            const due = [...clock.timers.entries()].filter(([, t]) => t.at <= target).sort((a, b) => a[1].at - b[1].at)[0]
            if (!due) break
            clock.timers.delete(due[0])
            clock.now = due[1].at
            due[1].fn()
        }
        clock.now = target
    }
    return clock
}

function setup() {
    const clock = makeClock()
    const changes = []
    const reveal = createChatReveal({
        now: () => clock.now,
        setTimeout: clock.setTimeout,
        clearTimeout: clock.clearTimeout,
        onChange: (state) => changes.push({ ...state }),
    })
    return { clock, changes, reveal, state: reveal.state }
}

test('constants', () => {
    assert.equal(CHAT_SKELETON_DELAY_MS, 300)
    assert.equal(CHAT_SKELETON_MIN_VISIBLE_MS, 300)
})

test('a fresh controller has the initial state; setBusy(false) on it changes nothing', () => {
    const { clock, changes, reveal, state } = setup()
    assert.deepEqual(state, { hidden: false, skeletonShown: false, startedAt: null, phaseId: 0 })
    reveal.setBusy(false)
    assert.equal(clock.timers.size, 0)
    assert.equal(changes.length, 0)
})

test('a quick phase: hidden at once, revealed after the 0ms settle, no skeleton', () => {
    const { clock, reveal, state } = setup()
    reveal.setBusy(true)
    assert.equal(state.hidden, true)
    assert.equal(state.phaseId, 1)
    clock.advance(100)
    reveal.setBusy(false)
    assert.equal(state.hidden, true, 'not in the same task')
    clock.advance(0)
    assert.equal(state.hidden, false)
    assert.equal(state.skeletonShown, false)
    assert.equal(state.startedAt, null)
    clock.advance(1000)
    assert.equal(state.skeletonShown, false, 'the shown timer was cancelled')
})

test('a slow phase: skeleton at 300ms, held until 600ms', () => {
    const { clock, reveal, state } = setup()
    reveal.setBusy(true)
    clock.advance(299)
    assert.equal(state.skeletonShown, false)
    clock.advance(1)
    assert.equal(state.skeletonShown, true)
    clock.advance(200)
    reveal.setBusy(false)
    clock.advance(99)
    assert.equal(state.hidden, true)
    clock.advance(1)
    assert.equal(state.hidden, false)
    assert.equal(state.skeletonShown, false)
})

test('busy false then true in the same task: never reveals, same phase', () => {
    const { clock, reveal, state } = setup()
    reveal.setBusy(true)
    clock.advance(50)
    const { phaseId, startedAt } = state
    reveal.setBusy(false)
    reveal.setBusy(true)
    clock.advance(0)
    assert.equal(state.hidden, true)
    assert.equal(state.phaseId, phaseId)
    assert.equal(state.startedAt, startedAt)
})

test('busy again during a hold, then false past the hold: reveals, same phase', () => {
    const { clock, reveal, state } = setup()
    reveal.setBusy(true)
    clock.advance(400)
    reveal.setBusy(false)
    clock.advance(0)               // settle: hold until 600
    clock.advance(100)
    reveal.setBusy(true)           // cancels the hold
    clock.advance(500)
    assert.equal(state.hidden, true)
    reveal.setBusy(false)          // at 1000, past the hold
    clock.advance(0)
    assert.equal(state.hidden, false)
    assert.equal(state.phaseId, 1)
})

test('restartClock: new phase, skeleton 300ms later; ignored when not hidden', () => {
    const { clock, reveal, state } = setup()
    reveal.restartClock()
    assert.equal(state.phaseId, 0, 'not hidden: ignored')
    assert.equal(clock.timers.size, 0)

    reveal.setBusy(true)
    clock.advance(350)
    assert.equal(state.skeletonShown, true)
    reveal.restartClock()
    assert.equal(state.phaseId, 2)
    assert.equal(state.startedAt, 350)
    assert.equal(state.skeletonShown, false)
    clock.advance(299)
    assert.equal(state.skeletonShown, false)
    clock.advance(1)
    assert.equal(state.skeletonShown, true)
})

test('a second busy period after finish starts a new phase', () => {
    const { clock, reveal, state } = setup()
    reveal.setBusy(true)
    reveal.setBusy(false)
    clock.advance(0)
    assert.equal(state.hidden, false)
    clock.advance(1000)
    reveal.setBusy(true)
    assert.equal(state.phaseId, 2)
    assert.equal(state.startedAt, 1000)
    assert.equal(state.skeletonShown, false)
    clock.advance(300)
    assert.equal(state.skeletonShown, true)
})

test('restartClock during a hold: the hold goes, new phase, finishes at once', () => {
    const { clock, reveal, state } = setup()
    reveal.setBusy(true)
    clock.advance(400)
    reveal.setBusy(false)
    clock.advance(0)
    assert.equal(state.hidden, true, 'holding until 600')
    reveal.restartClock()
    assert.equal(state.phaseId, 2)
    clock.advance(0)
    assert.equal(state.hidden, false, 'nothing was shown in the new phase')
    assert.equal(clock.timers.size, 0)
})

test('onChange is called on every state change, with the new state', () => {
    const { clock, changes, reveal } = setup()
    reveal.setBusy(true)
    assert.deepEqual(changes, [{ hidden: true, skeletonShown: false, startedAt: 0, phaseId: 1 }])
    clock.advance(300)
    assert.deepEqual(changes.at(-1), { hidden: true, skeletonShown: true, startedAt: 0, phaseId: 1 })
    reveal.setBusy(false)
    assert.equal(changes.length, 2, 'the settle timer is not a state change')
    clock.advance(300)
    assert.deepEqual(changes.at(-1), { hidden: false, skeletonShown: false, startedAt: null, phaseId: 1 })
    assert.equal(changes.length, 3)
})

test('setBusy(false) twice during a hold, then true: no reveal at the old hold end', () => {
    const { clock, reveal, state } = setup()
    reveal.setBusy(true)
    clock.advance(400)
    reveal.setBusy(false)
    clock.advance(0)
    reveal.setBusy(false)
    reveal.setBusy(true)
    clock.advance(1000)
    assert.equal(state.hidden, true)
    assert.equal(clock.timers.size, 0)
})

test('restartClock in the 0ms settle window leaves one settle timer', () => {
    const { clock, reveal, state } = setup()
    reveal.setBusy(true)
    reveal.setBusy(false)
    reveal.restartClock()
    // The shown timer and one settle timer.
    assert.equal(clock.timers.size, 2)
    clock.advance(0)
    assert.equal(state.hidden, false)
    assert.equal(clock.timers.size, 0)
})

test('dispose cancels every timer', () => {
    const { clock, reveal } = setup()
    reveal.setBusy(true)
    clock.advance(400)
    reveal.setBusy(false)
    clock.advance(0)
    reveal.setBusy(true)
    reveal.setBusy(false)
    assert.ok(clock.timers.size > 0)
    reveal.dispose()
    assert.equal(clock.timers.size, 0)
})
