import { test } from 'node:test'
import assert from 'node:assert/strict'
import {
    createAppPreferencesMirror, PREFERENCES_STYLE_ID, REDUCE_EFFECTS_ATTRIBUTE, REDUCE_MOTION_ATTRIBUTE,
} from './appPreferences.js'

function classList(initial = []) {
    const values = new Set(initial)
    return { contains: name => values.has(name), toggle: (name, on) => (on ? values.add(name) : values.delete(name)) }
}

function fakeChild() {
    const attributes = new Set()
    const root = { toggleAttribute: (name, on) => (on ? attributes.add(name) : attributes.delete(name)),
        hasAttribute: name => attributes.has(name) }
    const head = { children: [], prepend(node) { this.children.unshift(node) } }
    return { document: { documentElement: root, head, createElement: () => ({ id: '', textContent: '' }) }, root, head }
}

function fakeObserver() {
    const instances = []
    class Observer {
        constructor(callback) { this.callback = callback; instances.push(this) }
        observe(target, options) { this.target = target; this.options = options }
        disconnect() { this.disconnected = true }
    }
    return { Observer, instances }
}

// These tests catch a font size or a reduce flag that is missed at load, never updated,
// or forced on the page instead of exposed to it.
test('font size and reduce flags are copied at once and follow the parent', () => {
    const parentRoot = { classList: classList(['reduce-motion']) }
    let fontSize = '18px'
    const parent = { document: { documentElement: parentRoot }, getComputedStyle: () => ({ fontSize }) }
    const child = fakeChild()
    const { Observer, instances } = fakeObserver()
    const mirror = createAppPreferencesMirror({ document: child.document, window: { parent }, MutationObserver: Observer })
    const style = child.head.children[0]
    assert.equal(style.id, PREFERENCES_STYLE_ID)
    assert.equal(style.textContent, ':where(:root) { --twicc-root-font-size: 18px; }')
    assert.equal(child.root.hasAttribute(REDUCE_MOTION_ATTRIBUTE), true)
    assert.equal(child.root.hasAttribute(REDUCE_EFFECTS_ATTRIBUTE), false)
    assert.equal(instances[0].target, parentRoot)
    assert.deepEqual(instances[0].options.attributeFilter, ['class', 'style'])
    fontSize = '22px'
    parentRoot.classList.toggle('reduce-effects', true)
    instances[0].callback([])
    assert.equal(style.textContent, ':where(:root) { --twicc-root-font-size: 22px; }')
    assert.equal(child.root.hasAttribute(REDUCE_EFFECTS_ATTRIBUTE), true)
    parentRoot.classList.toggle('reduce-motion', false)
    parentRoot.classList.toggle('reduce-effects', false)
    instances[0].callback([])
    assert.equal(child.root.hasAttribute(REDUCE_MOTION_ATTRIBUTE), false)
    assert.equal(child.root.hasAttribute(REDUCE_EFFECTS_ATTRIBUTE), false)
    mirror.dispose()
    assert.equal(instances[0].disconnected, true)
})

test('a top-level page and a cross-origin parent leave the document untouched', () => {
    const { Observer, instances } = fakeObserver()
    const top = fakeChild()
    const topWindow = {}
    topWindow.parent = topWindow
    createAppPreferencesMirror({ document: top.document, window: topWindow, MutationObserver: Observer }).dispose()
    const crossOrigin = fakeChild()
    const parent = { get document() { throw new Error('SecurityError') } }
    createAppPreferencesMirror({ document: crossOrigin.document, window: { parent }, MutationObserver: Observer }).dispose()
    assert.equal(top.head.children.length + crossOrigin.head.children.length, 0)
    assert.equal(instances.length, 0)
})
