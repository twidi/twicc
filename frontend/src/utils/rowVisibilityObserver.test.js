import test from 'node:test'
import assert from 'node:assert/strict'
import { createRowVisibilityObserver } from './rowVisibilityObserver.js'
test('one root-local observer reports independent rows and rejects stale deliveries', () => {
    const instances = []
    class IO {
        constructor(callback, options) { this.callback = callback; this.options = options; this.targets = new Set(); instances.push(this) }
        observe(element) { this.targets.add(element) }
        unobserve(element) { this.targets.delete(element) }
        disconnect() { this.targets.clear() }
    }
    const root = {}, a = {}, b = {}, states = []
    const observer = createRowVisibilityObserver({ root, IntersectionObserver: IO })
    const release = observer.observe(a, state => states.push(['a', state]))
    observer.observe(b, state => states.push(['b', state]))
    assert.equal(instances.length, 1)
    assert.equal(instances[0].options.root, root)
    assert.equal(instances[0].options.rootMargin, '200px 0px')
    assert.deepEqual(states, [])
    instances[0].callback([{ target: a, isIntersecting: true }, { target: b, isIntersecting: false }])
    assert.deepEqual(states, [['a', 'inside'], ['b', 'outside']])
    release(); release()
    instances[0].callback([{ target: a, isIntersecting: true }])
    assert.equal(states.length, 2)
    observer.disconnect()
    instances[0].callback([{ target: b, isIntersecting: true }])
    assert.equal(states.length, 2)
    assert.equal(instances[0].targets.size, 0)
})
test('observer absence degrades mounted rows to inside without polling', () => {
    const states = [], observer = createRowVisibilityObserver({ root: {}, IntersectionObserver: null })
    const release = observer.observe({}, value => states.push(value))
    assert.deepEqual(states, ['inside'])
    release(); observer.disconnect()
})
test('new observer epoch ignores old delivery after recovery on the same target', () => {
    const deliveries = []
    class IO { constructor(callback) { deliveries.push(callback) } observe() {} unobserve() {} disconnect() {} }
    const element = {}, states = []
    const old = createRowVisibilityObserver({ root: {}, IntersectionObserver: IO })
    old.observe(element, state => states.push(state))
    old.disconnect()
    const next = createRowVisibilityObserver({ root: {}, IntersectionObserver: IO })
    next.observe(element, state => states.push(state))
    deliveries[1]([{ target: element, isIntersecting: true }])
    deliveries[0]([{ target: element, isIntersecting: false }])
    assert.deepEqual(states, ['inside'])
    next.disconnect()
})
test('unregister and register the same element rejects the previous observation', () => {
    const deliveries = []
    class IO { constructor(callback) { deliveries.push(callback) } observe() {} unobserve() {} disconnect() {} }
    const element = {}, states = []
    const observer = createRowVisibilityObserver({ root: {}, IntersectionObserver: IO })
    const release = observer.observe(element, state => states.push(['old', state]))
    release()
    observer.observe(element, state => states.push(['new', state]))
    deliveries.at(-1)([{ target: element, isIntersecting: true }])
    deliveries[0]([{ target: element, isIntersecting: false }])
    assert.deepEqual(states, [['new', 'inside']])
    observer.disconnect()
})
