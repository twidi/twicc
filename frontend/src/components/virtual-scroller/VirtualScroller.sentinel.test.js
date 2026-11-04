import test from 'node:test'
import assert from 'node:assert/strict'
import { h, reactive, ref } from 'vue'
import { virtualScroller, makeRenderer, flush, descendants } from '../../../tests/helpers/scrollerComponentHarness.js'

// The "at bottom" flag feeds the chat navigation buttons. Its only writer is the
// bottom-sentinel observer, which reports changes only and is muted while the
// scroller is suspended.
async function fixture(t) {
    const instances = []
    const original = globalThis.IntersectionObserver
    globalThis.IntersectionObserver = class {
        constructor(callback, options) {
            this.callback = callback
            this.options = options
            this.targets = new Set()
            this.log = []
            instances.push(this)
        }
        observe(element) { this.targets.add(element); this.log.push('observe') }
        unobserve(element) { this.targets.delete(element); this.log.push('unobserve') }
        disconnect() { this.targets.clear() }
    }
    t.after(() => { globalThis.IntersectionObserver = original })

    const { renderer, root } = makeRenderer()
    const items = ref([reactive({ id: 1 }), reactive({ id: 2 })])
    const scroller = ref(null)
    const component = virtualScroller()
    const app = renderer.createApp({ setup: () => () => h(component, { ref: scroller, items: items.value, itemKey: row => row.id }, { default: () => h('span') }) })
    app.mount(root)
    t.after(() => app.unmount())
    await flush()
    // The scroller observes one sentinel; the row observers are separate instances.
    const sentinel = instances.find(instance => instance.options?.rootMargin === '0px 0px 150px 0px')
    assert.ok(sentinel, 'the sentinel observer exists')
    return { root, sentinel, scroller: scroller.value, report: isIntersecting => sentinel.callback([{ isIntersecting }]) }
}

test('resuming asks the browser for the sentinel state again', async t => {
    const f = await fixture(t)
    assert.deepEqual(f.sentinel.log, ['observe'])
    f.scroller.suspend()
    await flush()
    assert.deepEqual(f.sentinel.log, ['observe'])
    f.scroller.resume()
    await flush()
    assert.deepEqual(f.sentinel.log, ['observe', 'unobserve', 'observe'])
    assert.equal(f.sentinel.targets.size, 1)
})

test('a report dropped during the suspension is replaced by the fresh one after the resume', async t => {
    const f = await fixture(t)
    f.report(true)
    assert.equal(f.scroller.isAtBottom(), true)
    f.scroller.suspend()
    await flush()
    f.report(false)
    assert.equal(f.scroller.isAtBottom(), true, 'muted while suspended')
    f.scroller.resume()
    await flush()
    f.report(false)
    assert.equal(f.scroller.isAtBottom(), false)
})

test('nothing is re-observed when the scroller was not suspended', async t => {
    const f = await fixture(t)
    f.scroller.resume()
    await flush()
    assert.deepEqual(f.sentinel.log, ['observe'])
})

test('holding the scroll position turns the native pin off until the change is laid out', async t => {
    const frames = []
    const original = globalThis.requestAnimationFrame
    globalThis.requestAnimationFrame = callback => { frames.push(callback); return frames.length }
    t.after(() => { globalThis.requestAnimationFrame = original })
    const f = await fixture(t)
    const container = () => descendants(f.root, node => String(node.props.class).includes('virtual-scroller'))[0].props.class
    f.report(true); await flush()
    assert.match(container(), /at-bottom/)
    f.scroller.holdScrollPosition(); await flush()
    assert.doesNotMatch(container(), /at-bottom/)
    assert.equal(f.scroller.isAtBottom(), true, 'the sentinel observer stays the only writer of the flag')
    frames.shift()(); await flush()
    assert.doesNotMatch(container(), /at-bottom/, 'one frame does not cover the anchoring of the layout')
    frames.shift()(); await flush()
    assert.match(container(), /at-bottom/)
})
