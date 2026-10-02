import test from 'node:test'
import assert from 'node:assert/strict'
import { effectScope, nextTick, shallowRef, triggerRef } from 'vue'

import { useVirtualScroll } from './useVirtualScroll.js'

globalThis.requestAnimationFrame ??= (callback) => setTimeout(callback, 0)
globalThis.cancelAnimationFrame ??= (handle) => clearTimeout(handle)

function setup(t, { count = 2000, height = 40, viewport = 200, buffer = 0, unloadBuffer = 0 } = {}) {
    const scope = effectScope()
    t.after(() => scope.stop())
    const items = shallowRef(Array.from({ length: count }, (_, index) => ({ id: `row-${index}` })))
    const container = {
        clientHeight: viewport,
        scrollTop: 0,
        domHeight: count * height,
        scrollHeightReads: 0,
        get scrollHeight() {
            this.scrollHeightReads++
            return this.domHeight
        },
        addEventListener() {},
        removeEventListener() {},
    }
    const containerRef = shallowRef(container)
    let keyCalls = 0
    const warn = console.warn
    console.warn = (...args) => { if (!String(args[0]).includes('onUnmounted')) warn(...args) }
    let scroller
    try {
        scroller = scope.run(() => useVirtualScroll({
            items,
            itemKey: (item) => { keyCalls++; return item.id },
            minItemHeight: height,
            buffer,
            unloadBuffer,
            containerRef,
        }))
    } finally {
        console.warn = warn
    }
    scroller.syncScrollPosition()
    return { scroller, items, container, containerRef, keyCalls: () => keyCalls, resetKeyCalls: () => { keyCalls = 0 } }
}

function published(scroller) {
    return {
        positions: scroller.positions.value,
        totalHeight: scroller.totalHeight.value,
        renderRange: scroller.renderRange.value,
        visibleRange: scroller.visibleRange.value,
        spacerBeforeHeight: scroller.spacerBeforeHeight.value,
        spacerAfterHeight: scroller.spacerAfterHeight.value,
        scrollTop: scroller.scrollTop.value,
        viewportHeight: scroller.viewportHeight.value,
        anchor: scroller.getScrollAnchor(),
    }
}

function assertFrozen(scroller, expected) {
    const actual = published(scroller)
    assert.strictEqual(actual.positions, expected.positions)
    assert.strictEqual(actual.renderRange, expected.renderRange)
    assert.strictEqual(actual.visibleRange, expected.visibleRange)
    assert.deepEqual(actual, expected)
}

test('suspension retains one geometry generation through in-place edits and height seeds', async (t) => {
    const { scroller, items, container, keyCalls, resetKeyCalls } = setup(t)
    container.scrollTop = 4000
    scroller.syncScrollPosition()
    await nextTick()
    scroller.suspend()
    const snapshot = published(scroller)
    resetKeyCalls()

    for (let index = 0; index < 60; index++) {
        items.value[1500] = { id: `changed-${index}` }
        triggerRef(items)
        await nextTick()
        assertFrozen(scroller, snapshot)
    }
    scroller.seedItemHeight('row-120', 84)
    await nextTick()
    assertFrozen(scroller, snapshot)
    assert.equal(keyCalls(), 0, 'hidden in-place edits and height seeds do not construct geometry')

    container.scrollTop = 0
    container.clientHeight = 0
    scroller.syncScrollPosition()
    assertFrozen(scroller, snapshot)
    assert.equal(scroller.getScrollState().scrollHeight, container.domHeight, 'raw DOM queries remain available')
})

test('suspension publishes stable geometry across append, reorder, removal, and empty replacement', async (t) => {
    const { scroller, items, container, keyCalls } = setup(t, { count: 20 })
    container.scrollTop = 400
    scroller.syncScrollPosition()
    await nextTick()
    scroller.suspend()
    const snapshot = published(scroller)

    const variants = [
        [...items.value, { id: 'added' }],
        [...items.value].reverse(),
        items.value.slice(0, 3),
        [],
    ]
    for (const variant of variants) {
        items.value = variant
        await nextTick()
        const callsBeforeRead = keyCalls()
        assertFrozen(scroller, snapshot)
        assert.equal(keyCalls(), callsBeforeRead, 'geometry reads do not extract hidden item keys')
    }
})

test('suspend reconciles a pending active replacement before publishing ranges and spacers', async (t) => {
    const { scroller, items, container } = setup(t, { count: 10, height: 100, viewport: 200 })
    container.scrollTop = 250
    scroller.syncScrollPosition()
    await nextTick()
    assert.deepEqual(scroller.renderRange.value, { start: 2, end: 5 })

    items.value = items.value.slice(0, 3)
    scroller.suspend()
    const snapshot = published(scroller)
    assert.equal(snapshot.positions.length, 3)
    assert.equal(snapshot.totalHeight, 300)
    assert.deepEqual(snapshot.renderRange, { start: 2, end: 3 })
    assert.deepEqual(snapshot.visibleRange, { start: 2, end: 3 })
    assert.equal(snapshot.spacerBeforeHeight, 200)
    assert.equal(snapshot.spacerAfterHeight, 0)
    assert.deepEqual(snapshot.anchor, { index: 2, key: 'row-2', offset: 50 })
    await nextTick()
    assertFrozen(scroller, snapshot)
})

test('active scroll and resize retain the existing hysteresis thresholds', async (t) => {
    const { scroller, container } = setup(t, { count: 10, height: 100, viewport: 100, buffer: 0, unloadBuffer: 200 })
    await nextTick()
    assert.deepEqual(scroller.renderRange.value, { start: 0, end: 2 })

    container.scrollTop = 150
    scroller.syncScrollPosition()
    await nextTick()
    assert.deepEqual(scroller.renderRange.value, { start: 0, end: 3 })

    container.scrollTop = 350
    scroller.syncScrollPosition()
    await nextTick()
    assert.deepEqual(scroller.renderRange.value, { start: 1, end: 5 })

    container.clientHeight = 200
    scroller.updateViewportHeight(200)
    await nextTick()
    assert.deepEqual(scroller.renderRange.value, { start: 1, end: 6 })
})
