import test from 'node:test'
import assert from 'node:assert/strict'
import * as buffer from './streamingBuffer.js'

function frames(run) {
    const saved = [globalThis.requestAnimationFrame, globalThis.cancelAnimationFrame, globalThis.performance]
    const pending = new Map(), callbacks = new Map(), publications = []
    let id = 0, now = 0
    globalThis.performance = { now: () => now }
    globalThis.requestAnimationFrame = fn => { pending.set(++id, fn); callbacks.set(id, fn); return id }
    globalThis.cancelAnimationFrame = id => pending.delete(id)
    try { run({ pending, callbacks, publications, advance(ms = 16) { now += ms; const [id, fn] = pending.entries().next().value; pending.delete(id); fn(now) }, time(ms) { now = ms } }) }
    finally { buffer.destroyAllBuffers(); [globalThis.requestAnimationFrame, globalThis.cancelAnimationFrame, globalThis.performance] = saved }
}
const init = (publications, active = false) => buffer.initBuffer('session', 0, text => publications.push(text), { messageId: 'message', active, visibilityManaged: false })
test('suspended feeds retain 1000 deltas without scheduling and resume once', () => frames(({ pending, publications }) => {
    init(publications)
    for (let i = 0; i < 1000; i++) buffer.feedDelta('session', 0, 'x')
    assert.equal(pending.size, 0)
    assert.deepEqual(publications, [])
    assert.equal(buffer.setBufferActive('session', 'message', 0, true), true)
    assert.deepEqual(publications, ['x'.repeat(1000)])
    assert.equal(pending.size, 0)
    buffer.snapshotBuffer('session', 'message', 0)
    assert.equal(publications.length, 1)
}))
test('visible buffers drain adaptively and visible flush completes text', () => frames(({ pending, publications, advance }) => {
    init(publications, true)
    buffer.feedDelta('session', 0, 'abcdefghij')
    advance()
    assert.equal(publications[0], 'abc')
    assert.equal(pending.size, 1)
    assert.equal(buffer.flushBuffer('session', 0), 'abcdefghij')
    assert.equal(publications.at(-1), 'abcdefghij')
    assert.equal(pending.size, 0)
}))
test('hidden flush retains full text without publication', () => frames(({ publications, pending }) => {
    init(publications)
    buffer.feedDelta('session', 0, 'complete')
    assert.equal(buffer.flushBuffer('session', 0), 'complete')
    assert.deepEqual(publications, [])
    assert.equal(pending.size, 0)
    assert.equal(buffer.flushBuffer('session', 0), null)
}))
test('canceled frame A cannot mutate or steal resumed frame B', () => frames(({ publications, pending, callbacks, advance, time }) => {
    init(publications, true)
    buffer.feedDelta('session', 0, 'old pending text')
    const a = callbacks.get(1)
    buffer.setBufferActive('session', 'message', 0, false)
    time(5000)
    buffer.setBufferActive('session', 'message', 0, true)
    buffer.feedDelta('session', 0, 'abcdefghij')
    const b = [...pending.keys()][0]
    const before = [...publications]
    a(9000)
    assert.deepEqual(publications, before)
    assert.deepEqual([...pending.keys()], [b])
    advance()
    assert.equal(publications.at(-1), 'old pending textabc')
    assert.equal(pending.size, 1)
}))
test('mismatched message and destroyed buffers cannot publish', () => frames(({ publications, callbacks }) => {
    init(publications, true)
    assert.equal(buffer.setBufferActive('session', 'other', 0, false), false)
    assert.equal(buffer.snapshotBuffer('session', 'other', 0), false)
    assert.equal(buffer.isBufferActive('session', 'other', 0), false)
    buffer.feedDelta('session', 0, 'old')
    const stale = callbacks.get(1)
    buffer.destroySessionBuffers('session')
    stale(20)
    assert.deepEqual(publications, [])
    assert.equal(buffer.setBufferActive('session', 'message', 0, true), false)
}))

test('managed buffers ignore active option and follow exact registry generation', async () => {
    const { createStreamPublicationIdentity, streamPublicationRegistry } = await import('./streamPublicationRegistry.js')
    frames(({ pending, publications }) => {
        const identity = createStreamPublicationIdentity('session', 'message', 0)
        buffer.initBuffer('session', 0, text => publications.push(text), { messageId: 'message', publicationIdentity: identity, active: true, visibilityManaged: true })
        buffer.feedDelta('session', 0, 'hidden')
        assert.equal(pending.size, 0)
        assert.equal(buffer.isBufferActive('session', 'message', 0, identity), false)
        const owner = streamPublicationRegistry.acquire(identity, { viewActive: true, bodyActive: true, intersection: 'inside' })
        assert.deepEqual(publications, ['hidden'])
        assert.equal(buffer.isBufferActive('session', 'message', 0, identity), true)
        assert.equal(buffer.isBufferActive('session', 'message', 0, createStreamPublicationIdentity('session', 'message', 0)), false)
        streamPublicationRegistry.release(owner)
        assert.equal(buffer.isBufferActive('session', 'message', 0, identity), false)
    })
})
