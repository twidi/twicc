import test from 'node:test'
import assert from 'node:assert/strict'
import { h, ref, KeepAlive } from 'vue'
import { makeRenderer, virtualScroller, flush } from '../../../tests/helpers/scrollerComponentHarness.js'

test('actual scroller emits once initially and once for equal recovery, including mount activation', async t => {
    const { renderer, root } = makeRenderer()
    const active = ref(true), updates = [], instance = ref()
    const component = virtualScroller()
    const items = [{ id: 1 }]
    const app = renderer.createApp({ setup: () => () => h(KeepAlive, () => active.value ? h(component, {
        ref: instance, items, itemKey: row => row.id, onUpdate: value => updates.push(value),
    }) : null) })
    app.mount(root); t.after(() => app.unmount())
    await flush()
    assert.equal(updates.length, 1)
    active.value = false; await flush(); active.value = true; await flush()
    assert.equal(updates.length, 2)
    assert.deepEqual(updates[1], updates[0])
    instance.value.seedItemHeight(1, 60); await flush()
    assert.equal(updates.length, 2)
})

test('deferred initial callbacks cannot publish after unmount', async () => {
    const { renderer, root } = makeRenderer()
    const updates = []
    const app = renderer.createApp(virtualScroller(), { items: [], itemKey: item => item.id, onUpdate: value => updates.push(value) })
    app.mount(root); app.unmount(); await flush()
    assert.equal(updates.length, 0)
})

test('hidden initial measurement and zero-height recovery each publish once', async t => {
    const observers = []
    const previous = globalThis.ResizeObserver
    globalThis.ResizeObserver = class {
        constructor(callback) { this.callback = callback; observers.push(this) }
        observe(target) { this.target = target }
        disconnect() {}
    }
    t.after(() => { globalThis.ResizeObserver = previous })
    const { renderer, root } = makeRenderer(0)
    const updates = []
    const app = renderer.createApp(virtualScroller(), { items: [], itemKey: item => item.id, onUpdate: value => updates.push(value) })
    app.mount(root); t.after(() => app.unmount()); await flush()
    assert.equal(updates.length, 0)
    const viewport = observers.find(observer => observer.target?.props.class?.includes('virtual-scroller'))
    function resize(height) {
        viewport.target.clientHeight = height
        viewport.callback([{ contentRect: { height } }])
    }
    resize(140); await flush(); assert.equal(updates.length, 1)
    resize(0); resize(140); await flush(); assert.equal(updates.length, 2)
    assert.deepEqual(updates[1], updates[0])
    resize(140); await flush(); assert.equal(updates.length, 2)
})

test('old recovery callbacks cannot publish in a newer epoch, which still accepts later changes', async t => {
    const { renderer, root } = makeRenderer()
    const instance = ref(), items = ref([{ id: 1 }]), updates = []
    const component = virtualScroller()
    const app = renderer.createApp({ setup: () => () => h(component, { ref: instance, items: items.value,
        itemKey: item => item.id, onUpdate: value => updates.push(value) }) })
    app.mount(root); t.after(() => app.unmount()); await flush()
    instance.value.suspend(); instance.value.resume()
    instance.value.suspend(); instance.value.resume()
    await flush(); assert.equal(updates.length, 2)
    items.value = [...items.value, { id: 2 }]; await flush(); assert.equal(updates.length, 3)
})
