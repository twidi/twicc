import test from 'node:test'
import assert from 'node:assert/strict'
import { effectScope, shallowRef, ref, triggerRef, nextTick } from 'vue'
import { useVirtualScroll } from './useVirtualScroll.js'

function setup(t, { reactiveItems = false, container = null } = {}) {
    const scope = effectScope()
    t.after(() => scope.stop())
    const items = (reactiveItems ? ref : shallowRef)(Array.from({ length: 40 }, (_, id) => ({ id })))
    let calls = 0
    const warn = console.warn
    console.warn = () => {}
    let scroll
    try {
        scroll = scope.run(() => useVirtualScroll({ items, itemKey: item => { calls++; return item.id },
            minItemHeight: 20, buffer: 0, unloadBuffer: 0, containerRef: shallowRef(container) }))
    } finally { console.warn = warn }
    return { scroll, items, calls: () => calls, reset: () => { calls = 0 } }
}

function oracle(scroll, items) {
    let top = 0
    const expected = items.value.map(({ id }, index) => {
        const height = scroll.getItemHeight(id) ?? 20
        const entry = { index, key: id, top, height }
        top += height
        return entry
    })
    assert.deepEqual(scroll.positions.value, expected)
    assert.equal(scroll.totalHeight.value, top)
}

test('height-only updates preserve prefix entries and avoid key extraction', t => {
    const { scroll, items, reset, calls } = setup(t)
    const first = scroll.positions.value
    const retained = structuredClone(first)
    reset()
    scroll.updateItemHeight(35, 60)
    const suffix = scroll.positions.value
    assert.equal(calls(), 0)
    assert.strictEqual(suffix[34], first[34])
    scroll.seedItemHeight(2, 45)
    scroll.seedItemHeight(37, 80)
    oracle(scroll, items)
    assert.equal(calls(), 0)
    assert.deepEqual(first, retained)
})

test('equal effective heights and unchanged ranges retain separate identities', async t => {
    const { scroll } = setup(t)
    const first = scroll.positions.value
    const render = scroll.renderRange.value
    const visible = scroll.visibleRange.value
    assert.notStrictEqual(render, visible)
    scroll.seedItemHeight(10, 20)
    assert.strictEqual(scroll.positions.value, first)
    scroll.seedItemHeight(30, 80)
    await nextTick()
    assert.strictEqual(scroll.renderRange.value, render)
    assert.strictEqual(scroll.visibleRange.value, visible)
    scroll.scrollTop.value = 40
    await nextTick()
    assert.deepEqual(scroll.visibleRange.value, { start: 2, end: 3 })
})

test('all height writers reconcile future seeds, removal, clear, and shrink', t => {
    const { scroll, items } = setup(t)
    scroll.seedItemHeight(99, 100)
    items.value = [...items.value, { id: 99 }]
    oracle(scroll, items)
    scroll.updateItemHeight(99, 35)
    oracle(scroll, items)
    scroll.removeItemHeight(99)
    oracle(scroll, items)
    scroll.seedItemHeight(0, 100)
    scroll.seedItemHeight(20, 70)
    scroll.clearHeightCache()
    oracle(scroll, items)
})

test('hidden mutations remain lazy and immediate resume reconciles triggerRef topology', async t => {
    const container = { clientHeight: 100, scrollTop: 0, addEventListener() {}, removeEventListener() {} }
    const { scroll, items, calls, reset } = setup(t, { container })
    scroll.syncScrollPosition()
    scroll.suspend()
    const snapshot = scroll.positions.value
    const retained = structuredClone(snapshot)
    reset()
    items.value.push({ id: 99 })
    triggerRef(items)
    scroll.seedItemHeight(99, 75)
    assert.strictEqual(scroll.positions.value, snapshot)
    assert.equal(calls(), 0)
    scroll.resume()
    oracle(scroll, items)
    assert.deepEqual(snapshot, retained)
    await nextTick()
})

test('reactive key fields invalidate geometry without array replacement', t => {
    const { scroll, items } = setup(t, { reactiveItems: true })
    const first = scroll.positions.value
    items.value[3].id = 99
    scroll.seedItemHeight(99, 75)
    oracle(scroll, items)
    assert.strictEqual(scroll.positions.value[2], first[2])
})

test('batched heights correct the anchor synchronously with exact cumulative heights', t => {
    const container = { clientHeight: 100, scrollTop: 205, addEventListener() {}, removeEventListener() {} }
    const { scroll, items, calls, reset } = setup(t, { container })
    scroll.syncScrollPosition()
    const anchor = scroll.getScrollAnchor()
    reset()
    scroll.batchUpdateItemHeights(new Map([[1, 30.75], [8, 45.25], [30, 90]]))
    assert.equal(container.scrollTop, 241)
    assert.deepEqual(scroll.getScrollAnchor(), anchor)
    oracle(scroll, items)
    assert.equal(calls(), 0)
})

test('programmatic height updates publish geometry without anchor correction', t => {
    const container = { clientHeight: 100, scrollTop: 0, scrollHeight: 800, scrollTo({ top }) { this.scrollTop = top },
        addEventListener() {}, removeEventListener() {} }
    const { scroll, items, calls, reset } = setup(t, { container })
    scroll.syncScrollPosition()
    scroll.scrollToIndex(10, { behavior: 'smooth' })
    const top = container.scrollTop
    reset()
    scroll.batchUpdateItemHeights(new Map([[1, 70], [35, 90]]))
    oracle(scroll, items)
    assert.equal(container.scrollTop, top)
    assert.equal(calls(), 0)
    scroll.suspend() // Dispose the programmatic reset timer.
})

test('same-key replacement preserves geometry and resize and empty topology publish ranges', async t => {
    const { scroll, items } = setup(t)
    const first = scroll.positions.value
    items.value = items.value.map(item => ({ ...item, text: 'replacement' }))
    assert.strictEqual(scroll.positions.value, first)
    scroll.viewportHeight.value = 60
    await nextTick()
    assert.deepEqual(scroll.visibleRange.value, { start: 0, end: 4 })
    assert.deepEqual(scroll.renderRange.value, { start: 0, end: 4 })
    items.value = []
    await nextTick()
    assert.deepEqual(scroll.visibleRange.value, { start: 0, end: 0 })
    assert.deepEqual(scroll.renderRange.value, { start: 0, end: 0 })
    assert.equal(scroll.spacerBeforeHeight.value, 0)
    assert.equal(scroll.spacerAfterHeight.value, 0)
})
