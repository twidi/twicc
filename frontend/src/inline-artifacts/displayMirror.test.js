import { test } from 'node:test'
import assert from 'node:assert/strict'
import { createDisplayMirror, DISPLAY_ATTRIBUTE } from './displayMirror.js'

function element(attributes = {}) {
    const values = new Map(Object.entries(attributes))
    return {
        getAttribute: name => values.has(name) ? values.get(name) : null,
        setAttribute: (name, value) => values.set(name, String(value)),
        removeAttribute: name => values.delete(name),
    }
}

function fakeObserver() {
    const instances = []
    class Observer {
        constructor(callback) {
            this.callback = callback
            this.disconnected = false
            instances.push(this)
        }
        observe(target, options) {
            this.target = target
            this.options = options
        }
        disconnect() { this.disconnected = true }
    }
    return { Observer, instances }
}

// These tests catch a root that misses the initial value, ignores later
// changes, or gets touched by frames that carry no display information.
test('the root copies the frame display at once and follows later changes', () => {
    const frame = element({ [DISPLAY_ATTRIBUTE]: 'inline' })
    const root = element()
    const { Observer, instances } = fakeObserver()
    const mirror = createDisplayMirror({
        document: { documentElement: root }, window: { frameElement: frame }, MutationObserver: Observer,
    })
    assert.equal(root.getAttribute(DISPLAY_ATTRIBUTE), 'inline')
    assert.equal(instances[0].target, frame)
    assert.deepEqual(instances[0].options.attributeFilter, [DISPLAY_ATTRIBUTE])
    frame.setAttribute(DISPLAY_ATTRIBUTE, 'fullscreen')
    instances[0].callback([])
    assert.equal(root.getAttribute(DISPLAY_ATTRIBUTE), 'fullscreen')
    frame.removeAttribute(DISPLAY_ATTRIBUTE)
    instances[0].callback([])
    assert.equal(root.getAttribute(DISPLAY_ATTRIBUTE), null)
    mirror.dispose()
    assert.equal(instances[0].disconnected, true)
})

test('frames without the attribute, top-level pages, and cross-origin parents leave the root untouched', () => {
    const { Observer, instances } = fakeObserver()
    const root = element()
    createDisplayMirror({ document: { documentElement: root }, window: { frameElement: element() }, MutationObserver: Observer })
    assert.equal(root.getAttribute(DISPLAY_ATTRIBUTE), null)
    createDisplayMirror({ document: { documentElement: root }, window: { frameElement: null }, MutationObserver: Observer })
    const crossOrigin = { get frameElement() { throw new Error('SecurityError') } }
    createDisplayMirror({ document: { documentElement: root }, window: crossOrigin, MutationObserver: Observer }).dispose()
    assert.equal(root.getAttribute(DISPLAY_ATTRIBUTE), null)
    assert.equal(instances.length, 1)
})
