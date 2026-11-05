import { test } from 'node:test'
import assert from 'node:assert/strict'
import { nextTick } from 'vue'
import { registerAndroidMenuAnchor, watchAndroidMenuPlacement } from './androidMenuPlacement.js'

function fixture(t) {
    const listeners = new Map(), observers = []
    const window = { innerWidth: 400, innerHeight: 800,
        getComputedStyle: node => ({ visibility: node.visibility, display: 'block' }),
        addEventListener: (name, fn) => listeners.set(name, fn), removeEventListener: name => listeners.delete(name),
        ResizeObserver: class { constructor(fn) { this.callback = fn; observers.push(this) } observe() {} disconnect() {} },
        MutationObserver: class { constructor(fn) { this.callback = fn; observers.push(this) } observe() {} disconnect() {} },
    }
    let target
    const placement = watchAndroidMenuPlacement(window, value => { target = value })
    t.after(placement.stop)
    function anchor() {
        const rail = { isConnected: true, visibility: 'visible', parentElement: null,
            rect: { width: 54, height: 800, left: 0, right: 54, top: 0, bottom: 800 },
            getBoundingClientRect() { return this.rect } }
        const element = { isConnected: true, closest: () => rail }
        const release = registerAndroidMenuAnchor(element)
        t.after(release)
        return { rail, element, release }
    }
    return { anchor, placement, window, listeners, observers, get target() { return target } }
}
test('no rail selects the fixed placement; mounting and removing a visible rail updates the target', async t => {
    const f = fixture(t)
    assert.equal(f.target, null)
    const a = f.anchor()
    await nextTick()
    assert.equal(f.target, a.element)
    a.release()
    await nextTick()
    assert.equal(f.target, null)
})
test('hidden, collapsed, offscreen, and disconnected rails select the fixed placement', async t => {
    const f = fixture(t), a = f.anchor()
    await nextTick()
    a.rail.visibility = 'hidden'
    f.observers.at(-1).callback()
    assert.equal(f.target, null)
    a.rail.visibility = 'visible'
    a.rail.rect.width = 0
    f.placement.refresh()
    assert.equal(f.target, null)
    a.rail.rect.width = 54
    a.rail.rect.bottom = 0
    f.placement.refresh()
    assert.equal(f.target, null)
    a.rail.rect.bottom = 800
    f.listeners.get('resize')()
    assert.equal(f.target, a.element)
    a.element.isConnected = false
    f.placement.refresh()
    assert.equal(f.target, null)
})
test('old anchor cleanup cannot remove its replacement and stopping releases observers/listeners', async t => {
    const f = fixture(t), old = f.anchor(), current = f.anchor()
    await nextTick()
    assert.equal(f.target, current.element)
    old.release()
    await nextTick()
    assert.equal(f.target, current.element)
    f.placement.stop()
    assert.equal(f.target, null)
    assert.equal(f.listeners.size, 0)
})
