import test from 'node:test'
import assert from 'node:assert/strict'
import * as buffer from './streamingBuffer.js'
import { createStreamPublicationIdentity, streamPublicationRegistry } from './streamPublicationRegistry.js'

const INTERVAL = 1000 / 30
const TOLERANCE = 1e-7
const options = { visibilityManaged: false, active: true, messageId: 'message' }

function harness(run) {
    const saved = [globalThis.performance, globalThis.requestAnimationFrame, globalThis.cancelAnimationFrame]
    const pending = new Map(), callbacks = new Map(), publications = [], executions = [], registrations = []
    let now = 0, next = 0
    const time = at => { assert.ok(at >= now, 'monotonic clock'); now = at }
    globalThis.performance = { now: () => now }
    globalThis.requestAnimationFrame = callback => {
        const handle = ++next
        pending.set(handle, callback)
        callbacks.set(handle, callback)
        registrations.push({ handle, time: now })
        return handle
    }
    globalThis.cancelAnimationFrame = handle => pending.delete(handle)
    const record = text => publications.push({ time: now, text })
    buffer.initBuffer('session', 0, record, options)
    const h = {
        pending, publications, executions, registrations, record,
        feed(text, at) { time(at); buffer.feedDelta('session', 0, text) },
        frame(at) {
            time(at)
            const batch = [...pending]
            for (const [handle] of batch) pending.delete(handle)
            for (const [handle, callback] of batch) { executions.push({ handle, time: now }); callback(-999) }
        },
        advanceFrames(from, through, hz) {
            for (let i = 1; from + i * 1000 / hz <= through + TOLERANCE; i++) {
                const at = from + i * 1000 / hz
                if (at >= now) h.frame(at)
            }
        },
        invokeCanceled(handle, at) { time(at); callbacks.get(handle)(-999) },
    }
    try { run(h) }
    finally { buffer.destroyAllBuffers(); [globalThis.performance, globalThis.requestAnimationFrame, globalThis.cancelAnimationFrame] = saved }
}
function gaps(publications) {
    for (let i = 1; i < publications.length; i++) {
        assert.ok(publications[i].time - publications[i - 1].time >= INTERVAL - TOLERANCE)
        assert.ok(publications[i].text.startsWith(publications[i - 1].text))
        assert.ok(publications[i].text.length > publications[i - 1].text.length)
    }
}
for (const hz of [60, 90, 120, 144, 240]) {
    test(`regular publications obey the interval at ${hz} Hz`, () => harness(h => {
        let full = 'x'.repeat(1000)
        h.feed(full, 0)
        for (let i = 1; i * 1000 / hz < 1000; i++) {
            const at = i * 1000 / hz
            full += 'y'.repeat(1000)
            h.feed('y'.repeat(1000), at)
            h.frame(at)
        }
        gaps(h.publications)
        assert.ok(h.publications.length <= Math.ceil(1000 / INTERVAL))
        assert.equal(buffer.flushBuffer('session', 0), full)
        assert.equal(h.publications.at(-1).text, full)
    }))
    test(`a first burst catches up within the episode bound at ${hz} Hz`, () => harness(h => {
        const full = 'x'.repeat(10000)
        h.feed(full, 0)
        h.advanceFrames(0, 250 + INTERVAL + 1000 / hz, hz)
        assert.equal(h.publications.at(-1).text, full)
        gaps(h.publications)
        assert.equal(h.pending.size, 0)
    }))
}
test('skipped opportunities do not consume smoothing elapsed or credit', () => {
    const trace = skipped => {
        let result
        harness(h => {
            h.feed('x'.repeat(1000), 0)
            h.frame(1)
            if (skipped) { h.frame(11); h.frame(21) }
            h.frame(35)
            assert.deepEqual(h.publications.map(p => p.text.length), [1, 8])
            result = h.publications
        })
        return result
    }
    assert.deepEqual(trace(true), trace(false))
})
test('continuous arrivals preserve episode age and long frame gaps publish once', () => harness(h => {
    let full = 'x'.repeat(10000)
    h.feed(full, 0)
    h.frame(1)
    assert.equal(h.publications[0].text.length, 1)
    // Remove the initial burst from the five-arrival estimate before eligibility.
    for (let at = 2; at <= 6; at++) {
        full += 'y'
        h.feed('y', at)
    }
    let caughtUp = null
    for (let at = 10; at <= 280; at += 10) {
        full += 'y'
        h.feed('y', at)
        const previous = h.publications.at(-1)
        const eligible = at - previous.time >= INTERVAL
        const firstDeadlineFrame = eligible && at >= 250 && caughtUp === null
        h.frame(at)
        const published = h.publications.at(-1)
        if (firstDeadlineFrame) {
            assert.equal(published.time, at)
            assert.equal(published.text, full)
            caughtUp = published
        } else if (caughtUp === null) {
            assert.ok(published.text.length < full.length, `episode must remain unfinished at ${at} ms`)
        }
    }
    assert.equal(caughtUp?.time, 280)
    gaps(h.publications)
    h.feed('z'.repeat(10000), 310)
    const count = h.publications.length
    h.frame(2310)
    assert.equal(h.publications.length, count + 1)
    assert.equal(h.publications.at(-1).text, full + 'z'.repeat(10000))
    assert.equal(h.pending.size, 0)
}))
test('idle arrival uses fresh elapsed and empty deltas schedule nothing', () => harness(h => {
    h.feed('', 0)
    assert.equal(h.pending.size, 0)
    h.feed('a', 10)
    h.frame(20)
    h.feed('abcdefghij', 2020)
    h.frame(2036)
    assert.equal(h.publications.at(-1).text, 'aabc')
}))
test('slow arrivals retain fractional credit across eligible publications', () => harness(h => {
    h.feed('x'.repeat(100), 0)
    h.frame(1)
    h.feed('y', 1000)
    h.frame(1001) // deadline resets the estimate
    h.feed('z'.repeat(100), 1010)
    h.frame(1035)
    h.frame(1069)
    h.frame(1103)
    assert.deepEqual(h.publications.slice(2).map(p => p.text.length), [106, 112, 119])
}))
for (const action of ['suspend', 'destroy', 'replace', 'snapshot', 'feed']) {
    test(`regular callback reentry: ${action}`, () => harness(h => {
        let first = true
        buffer.initBuffer('session', 0, text => {
            h.record(text)
            if (!first) return
            first = false
            if (action === 'suspend') buffer.setBufferActive('session', 'message', 0, false)
            if (action === 'destroy') buffer.destroySessionBuffers('session')
            if (action === 'replace') {
                buffer.initBuffer('session', 0, h.record, options)
                buffer.feedDelta('session', 0, 'replacement')
            }
            if (action === 'snapshot') buffer.snapshotBuffer('session', 'message', 0)
            if (action === 'feed') buffer.feedDelta('session', 0, 'suffix')
        }, options)
        h.feed('x'.repeat(1000), 0)
        h.frame(16)
        assert.equal(h.pending.size, action === 'replace' || action === 'feed' ? 1 : 0)
        if (action === 'replace' || action === 'feed') {
            const handles = [...h.pending.keys()]
            h.invokeCanceled(1, 20)
            assert.deepEqual([...h.pending.keys()], handles)
            h.frame(300)
            assert.equal(buffer.flushBuffer('session', 0), action === 'replace' ? 'replacement' : 'x'.repeat(1000) + 'suffix')
        }
    }))
}
test('feed after complete callback starts a new pending episode', () => harness(h => {
    let first = true
    buffer.initBuffer('session', 0, text => {
        h.record(text)
        if (first) { first = false; buffer.feedDelta('session', 0, 'x'.repeat(1000)) }
    }, options)
    h.feed('a', 0)
    h.frame(16)
    assert.equal(h.pending.size, 1)
    h.frame(50)
    assert.equal(h.publications.at(-1).text.length, 7)
    h.frame(265)
    assert.ok(h.publications.at(-1).text.length < 1001)
    h.frame(300)
    assert.equal(h.publications.at(-1).text.length, 1001)
}))
test('suspend callback cannot revive work after resume and stale delivery', () => harness(h => {
    let first = true
    buffer.initBuffer('session', 0, text => {
        h.record(text)
        if (first) { first = false; buffer.setBufferActive('session', 'message', 0, false) }
    }, options)
    h.feed('x'.repeat(1000), 0)
    h.frame(16)
    assert.equal(h.pending.size, 0)
    h.feed('hidden', 20)
    buffer.setBufferActive('session', 'message', 0, true)
    h.feed('suffix', 21)
    const handles = [...h.pending.keys()]
    h.invokeCanceled(1, 22)
    assert.deepEqual([...h.pending.keys()], handles)
    h.frame(30)
    assert.equal(h.publications.at(-1).text, 'x'.repeat(1000) + 'hidden')
    h.frame(55)
    assert.equal(buffer.flushBuffer('session', 0), 'x'.repeat(1000) + 'hiddensuffix')
}))
test('low arrival rate applies minimum progress only at eligible publications', () => harness(h => {
    h.feed('x'.repeat(100), 0)
    h.frame(1)
    h.feed('y', 200)
    h.frame(201)
    h.frame(211)
    h.frame(221)
    assert.deepEqual(h.publications.map(p => p.text.length), [1, 51])
    h.frame(235)
    assert.equal(h.publications.at(-1).text.length, 68)
    h.frame(269)
    assert.equal(h.publications.at(-1).text.length, 101)
}))

for (const boundary of ['activation', 'snapshot', 'flush']) {
    test(`${boundary} publishes complete text inside the interval`, () => harness(h => {
        h.feed('x'.repeat(1000), 0)
        h.frame(16)
        const stale = [...h.pending.keys()][0]
        h.feed('suffix', 20)
        if (boundary === 'activation') {
            buffer.setBufferActive('session', 'message', 0, false)
            h.feed('hidden', 21)
            assert.equal(h.pending.size, 0)
            buffer.setBufferActive('session', 'message', 0, true)
        } else if (boundary === 'snapshot') buffer.snapshotBuffer('session', 'message', 0)
        else assert.equal(buffer.flushBuffer('session', 0), 'x'.repeat(1000) + 'suffix')
        const full = 'x'.repeat(1000) + 'suffix' + (boundary === 'activation' ? 'hidden' : '')
        assert.equal(h.publications.length, 2)
        assert.equal(h.publications.at(-1).text, full)
        assert.equal(h.pending.size, 0)
        if (boundary === 'flush') return
        buffer.snapshotBuffer('session', 'message', 0)
        assert.equal(h.publications.length, 2)
        h.feed('new', 22)
        const handles = [...h.pending.keys()]
        h.invokeCanceled(stale, 23)
        assert.deepEqual([...h.pending.keys()], handles)
        h.frame(37)
        assert.equal(h.publications.length, 2)
        h.frame(56)
        assert.equal(h.publications.at(-1).text, full + 'new')
    }))
}
for (const boundary of ['activation', 'snapshot']) for (const reentry of ['feed', 'suspend', 'replace']) {
    test(`${boundary} callback preserves ${reentry} lifecycle`, () => harness(h => {
        let first = true
        buffer.initBuffer('session', 0, text => {
            h.record(text)
            if (!first) return
            first = false
            if (reentry === 'replace') buffer.initBuffer('session', 0, h.record, options)
            buffer.feedDelta('session', 0, 'suffix')
            if (reentry === 'suspend') buffer.setBufferActive('session', 'message', 0, false)
        }, { ...options, active: boundary !== 'activation' })
        h.feed('initial', 0)
        if (boundary === 'activation') buffer.setBufferActive('session', 'message', 0, true)
        else buffer.snapshotBuffer('session', 'message', 0)
        assert.equal(h.pending.size, reentry === 'suspend' ? 0 : 1)
        h.frame(16)
        if (reentry !== 'replace') assert.equal(h.publications.length, 1)
        h.frame(300)
        assert.equal(buffer.flushBuffer('session', 0), reentry === 'replace' ? 'suffix' : 'initialsuffix')
        if (reentry !== 'suspend') assert.equal(h.publications.at(-1).text, reentry === 'replace' ? 'suffix' : 'initialsuffix')
    }))
}
test('managed terminal replacement survives cleanup and old ownership release', () => harness(h => {
    const oldIdentity = createStreamPublicationIdentity('session', 'message', 0)
    const newIdentity = createStreamPublicationIdentity('session', 'message', 0)
    const state = { viewActive: true, bodyActive: true, intersection: 'inside' }
    let newOwner
    buffer.initBuffer('session', 0, text => {
        h.record(text)
        buffer.initBuffer('session', 0, h.record, { messageId: 'message', publicationIdentity: newIdentity })
        newOwner = streamPublicationRegistry.acquire(newIdentity, state)
        buffer.feedDelta('session', 0, 'replacement')
    }, { messageId: 'message', publicationIdentity: oldIdentity })
    const oldOwner = streamPublicationRegistry.acquire(oldIdentity, state)
    try {
        h.feed('old', 0)
        assert.equal(buffer.flushBuffer('session', 0), 'old')
        streamPublicationRegistry.release(oldOwner)
        assert.equal(buffer.isBufferActive('session', 'message', 0, newIdentity), true)
        h.frame(300)
        assert.equal(h.publications.at(-1).text, 'replacement')
        assert.equal(buffer.flushBuffer('session', 0), 'replacement')
        assert.equal(buffer.flushBuffer('session', 0), null)
    } finally { streamPublicationRegistry.release(oldOwner); streamPublicationRegistry.release(newOwner) }
}))
test('distinct block snapshots preserve the other block interval', () => harness(h => {
    const other = []
    buffer.initBuffer('session', 1, text => other.push({ time: performance.now(), text }), options)
    h.feed('x'.repeat(1000), 0)
    buffer.feedDelta('session', 1, 'y'.repeat(1000))
    h.frame(16)
    h.feed('suffix', 20)
    buffer.snapshotBuffer('session', 'message', 0)
    h.frame(32)
    assert.equal(other.length, 1)
    h.frame(50)
    assert.equal(other.length, 2)
    gaps(other)
}))
