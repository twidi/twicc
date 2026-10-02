import test from 'node:test'
import assert from 'node:assert/strict'
import { createStreamPublicationRegistry, createStreamPublicationIdentity } from './streamPublicationRegistry.js'
const inside = { viewActive: true, bodyActive: true, intersection: 'inside' }
const unknown = { ...inside, intersection: 'unknown' }
function fixture() {
    const listeners = new Set(), transitions = [], snapshots = []
    const document = { hidden: false, addEventListener(name, fn) { listeners.add(fn) }, removeEventListener(name, fn) { listeners.delete(fn) } }
    const registry = createStreamPublicationRegistry({ document })
    const identity = createStreamPublicationIdentity('session', 'message', 0)
    const bind = (id = identity) => registry.bindBlock(id, { setActive: value => transitions.push(value), snapshot: () => snapshots.push(id) })
    return { registry, identity, bind, transitions, snapshots, listeners, document, visibility(hidden) { document.hidden = hidden; for (const fn of listeners) fn() } }
}
test('two consumers aggregate and duplicate release cannot suspend another owner', () => {
    const f = fixture(), unbind = f.bind()
    const a = f.registry.acquire(f.identity, inside), b = f.registry.acquire(f.identity, inside)
    assert.deepEqual(f.transitions, [true])
    f.registry.release(a); f.registry.release(a)
    assert.deepEqual(f.transitions, [true])
    f.registry.release(b)
    assert.deepEqual(f.transitions, [true, false])
    unbind(); assert.equal(f.listeners.size, 0)
})
test('pending owner binds and replacement generation rejects old tokens', () => {
    const f = fixture(), old = f.registry.acquire(f.identity, inside)
    f.bind()
    const next = createStreamPublicationIdentity('session', 'message', 0)
    assert.notEqual(next.generation, f.identity.generation)
    const unbind = f.bind(next)
    f.registry.update(old, inside)
    assert.deepEqual(f.transitions, [true, false])
    f.registry.acquire(next, inside)
    assert.deepEqual(f.transitions, [true, false, true])
    unbind(); assert.equal(f.listeners.size, 0)
})
test('unknown owners share bootstrap without activating and observation catches up once', () => {
    const f = fixture(), unbind = f.bind()
    const a = f.registry.acquire(f.identity, unknown), b = f.registry.acquire(f.identity, unknown)
    assert.equal(f.snapshots.length, 1)
    assert.deepEqual(f.transitions, [])
    f.registry.update(a, inside)
    f.registry.update(b, inside)
    assert.deepEqual(f.transitions, [true])
    f.registry.acquire(f.identity, unknown)
    assert.equal(f.snapshots.length, 1)
    unbind(); assert.equal(f.listeners.size, 0)
})
test('hidden document and ineligible body cannot bootstrap; document restores aggregate', () => {
    const f = fixture(), unbind = f.bind()
    f.visibility(true)
    const a = f.registry.acquire(f.identity, unknown)
    f.registry.acquire(f.identity, { ...unknown, bodyActive: false })
    assert.equal(f.snapshots.length, 0)
    f.visibility(false)
    assert.equal(f.snapshots.length, 1)
    f.registry.update(a, inside)
    f.visibility(true); f.visibility(false)
    assert.deepEqual(f.transitions, [true, false, true])
    f.registry.dispose(); unbind(); assert.equal(f.listeners.size, 0)
})
test('atomic updates retain activity and bootstrap disposal permits another owner', () => {
    const f = fixture(), unbind = f.bind()
    const a = f.registry.acquire(f.identity, unknown), b = f.registry.acquire(f.identity, unknown)
    f.registry.release(a)
    assert.equal(f.snapshots.length, 2)
    f.registry.update(b, inside); f.registry.update(b, inside)
    assert.deepEqual(f.transitions, [true])
    f.registry.update(b, { ...inside, viewActive: false })
    assert.deepEqual(f.transitions, [true, false])
    unbind(); assert.equal(f.listeners.size, 0)
})
