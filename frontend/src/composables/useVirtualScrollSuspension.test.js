import test from 'node:test'
import assert from 'node:assert/strict'
import { effectScope, nextTick, shallowRef, triggerRef } from 'vue'

import { useVirtualScroll } from './useVirtualScroll.js'

globalThis.requestAnimationFrame ??= (callback) => setTimeout(callback, 0)
globalThis.cancelAnimationFrame ??= (handle) => clearTimeout(handle)

function setup(t, { count = 2000, height = 40, viewport = 200, buffer = 0, unloadBuffer = 0 } = {}) {
    const scope = effectScope()
    t.after(() => scope.stop())
    const items = shallowRef(Array.from({ length: count }, (_, index) => ({ id: `suspension-test:row-${index}` })))
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

// Vue also uses Maps. Count only the scroller cache, and keep native iterators intact.
function countHeightCacheTraversals(t) {
    const methods = ['keys', 'values', 'entries', Symbol.iterator]
    const native = new Map(methods.map(method => [method, Map.prototype[method]]))
    const owned = new WeakSet()
    let cache = null
    const counts = { keys: 0, values: 0, entries: 0, iterator: 0 }
    function isHeightCache(map) {
        if (owned.has(map)) return true
        const first = native.get('keys').call(map).next()
        if (first.done || typeof first.value !== 'string' || !first.value.startsWith('suspension-test:')) return false
        owned.add(map)
        cache = map
        return true
    }
    for (const method of methods) {
        const name = method === Symbol.iterator ? 'iterator' : method
        Map.prototype[method] = function (...args) {
            if (isHeightCache(this)) counts[name]++
            return native.get(method).apply(this, args)
        }
    }
    t.after(() => {
        for (const method of methods) Map.prototype[method] = native.get(method)
    })
    return { counts, cache: () => cache, reset: () => { for (const name of Object.keys(counts)) counts[name] = 0 } }
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
        items.value[1500] = { id: `suspension-test:changed-${index}` }
        triggerRef(items)
        await nextTick()
        assertFrozen(scroller, snapshot)
    }
    scroller.seedItemHeight('suspension-test:row-120', 84)
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
        [...items.value, { id: 'suspension-test:added' }],
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
    assert.deepEqual(snapshot.anchor, { index: 2, key: 'suspension-test:row-2', offset: 50 })
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

test('60 hidden replacements defer both key cleanup and cache traversal', { concurrency: false }, async (t) => {
    const traversals = countHeightCacheTraversals(t)
    const { scroller, items, keyCalls, resetKeyCalls } = setup(t)
    scroller.seedItemHeight('suspension-test:row-0', 80)
    scroller.invalidateZeroHeights() // Identify the raw reactive cache without a product diagnostic API.
    traversals.reset()
    scroller.suspend()
    const snapshot = published(scroller)
    resetKeyCalls()

    for (let index = 0; index < 60; index++) {
        const replacement = items.value.slice()
        replacement[1500] = { id: `suspension-test:changed-${index}` }
        items.value = replacement
        await nextTick()
        assertFrozen(scroller, snapshot)
        assert.equal(keyCalls(), 0, `replacement ${index} did not extract hidden keys`)
        assert.deepEqual(traversals.counts, { keys: 0, values: 0, entries: 0, iterator: 0 })
    }

    scroller.resume()
    assert.equal(keyCalls(), 4000, 'one latest cleanup pass and one latest geometry pass')
    assert.equal(scroller.positions.value[1500].key, 'suspension-test:changed-59')
    assert.equal(scroller.getItemHeight('suspension-test:row-0'), 80)
    await nextTick()
    assert.equal(keyCalls(), 4000, 'queued post-flush cleanup does not repeat')
})

test('immediate resume flushes latest cleanup and repeated zero invalidation once', { concurrency: false }, async (t) => {
    const traversals = countHeightCacheTraversals(t)
    const { scroller, items, keyCalls, resetKeyCalls } = setup(t, { count: 5 })
    scroller.seedItemHeight('suspension-test:row-1', 70)
    scroller.seedItemHeight('suspension-test:row-0', 75)
    scroller.seedItemHeight('suspension-test:removed', 90)
    scroller.invalidateZeroHeights()
    assert.ok(traversals.cache(), 'test located the height cache')
    traversals.cache().set('suspension-test:zero', 0)
    traversals.reset()
    scroller.suspend()
    resetKeyCalls()
    items.value = items.value.slice(1)
    scroller.seedItemHeight('suspension-test:row-1', 88)
    scroller.invalidateZeroHeights()
    scroller.invalidateZeroHeights()
    assert.deepEqual(traversals.counts, { keys: 0, values: 0, entries: 0, iterator: 0 })
    assert.equal(keyCalls(), 0)

    scroller.resume() // The post-flush watcher has not run.
    assert.equal(keyCalls(), 8, 'four cleanup keys and four geometry keys')
    assert.equal(scroller.getItemHeight('suspension-test:row-1'), 88)
    for (const key of ['row-0', 'removed', 'zero']) {
        assert.equal(scroller.getItemHeight(`suspension-test:${key}`), undefined)
    }
    await nextTick()
    assert.equal(keyCalls(), 8, 'the queued watcher does not clean twice')
    assert.equal(traversals.counts.entries, 1, 'cleanup and zero invalidation share one cache pass')
})

test('latest empty list publishes empty ranges and releases anchors on resume', async (t) => {
    const { scroller, items, container } = setup(t, { count: 10, height: 100 })
    container.scrollTop = 250
    scroller.syncScrollPosition()
    await nextTick()
    scroller.suspend()
    items.value = []
    scroller.resume()
    assert.equal(scroller.suspended.value, false)
    assert.equal(scroller.positions.value.length, 0)
    assert.equal(scroller.totalHeight.value, 0)
    assert.deepEqual(scroller.renderRange.value, { start: 0, end: 0 })
    assert.deepEqual(scroller.visibleRange.value, { start: 0, end: 0 })
    assert.equal(scroller.spacerBeforeHeight.value, 0)
    assert.equal(scroller.spacerAfterHeight.value, 0)
    assert.equal(scroller.getScrollAnchor(), null)
    await nextTick()
    assert.deepEqual(scroller.renderRange.value, { start: 0, end: 0 })
})

test('manual entry revokes automatic and pending resume authority without recapture', async (t) => {
    const { scroller, container, containerRef } = setup(t, { count: 10, height: 100 })
    container.scrollTop = 250
    scroller.syncScrollPosition()
    await nextTick()
    scroller.updateViewportHeight(0)
    const snapshot = published(scroller)
    scroller.suspend()
    assertFrozen(scroller, snapshot)
    scroller.updateViewportHeight(200)
    assert.equal(scroller.suspended.value, true, 'manual takeover prevents automatic resume')

    container.clientHeight = 0
    scroller.resume()
    assert.equal(scroller.suspended.value, true)
    scroller.suspend()
    container.clientHeight = 200
    scroller.updateViewportHeight(200)
    assert.equal(scroller.suspended.value, true, 'second manual entry revokes pending resume')
    assertFrozen(scroller, snapshot)

    containerRef.value = null
    scroller.resume()
    containerRef.value = container
    scroller.updateViewportHeight(200)
    assert.equal(scroller.suspended.value, true, 'missing container does not authorize a later resume')
    scroller.resume()
    assert.equal(scroller.suspended.value, false)

    scroller.updateViewportHeight(0)
    assert.equal(scroller.suspended.value, true)
    scroller.invalidateZeroHeights()
    container.clientHeight = 200
    scroller.updateViewportHeight(200)
    assert.equal(scroller.suspended.value, false, 'automatic origin resumes when visible')
})

test('zero-height resume keeps pending maintenance until a positive measurement', { concurrency: false }, async (t) => {
    const traversals = countHeightCacheTraversals(t)
    const { scroller, items, container } = setup(t, { count: 5, height: 100 })
    scroller.seedItemHeight('suspension-test:row-0', 140)
    scroller.invalidateZeroHeights()
    traversals.reset()
    scroller.suspend()
    items.value = items.value.slice(1)
    scroller.invalidateZeroHeights()
    container.clientHeight = 0
    scroller.resume()
    await nextTick()
    assert.equal(scroller.suspended.value, true)
    assert.equal(scroller.getItemHeight('suspension-test:row-0'), 140)
    assert.deepEqual(traversals.counts, { keys: 0, values: 0, entries: 0, iterator: 0 })
    container.clientHeight = 200
    scroller.updateViewportHeight(200)
    assert.equal(scroller.suspended.value, false)
    assert.equal(scroller.getItemHeight('suspension-test:row-0'), undefined)
    assert.equal(traversals.counts.entries, 1)
    assert.equal(scroller.viewportHeight.value, 200)
})

test('resume selects surviving anchor offset, then falls back to index and final item', async (t) => {
    const { scroller, items, container } = setup(t, { count: 10, height: 100, viewport: 100 })
    container.scrollTop = 450
    scroller.syncScrollPosition()
    await nextTick()
    scroller.suspend()
    items.value = [items.value[9], items.value[3], ...items.value.slice(5, 9)]
    scroller.resume()
    assert.equal(container.scrollTop, 250, 'surviving row 3 uses its own 150px offset')

    scroller.suspend()
    items.value = Array.from({ length: 8 }, (_, index) => ({ id: `suspension-test:new-${index}` }))
    scroller.resume()
    assert.equal(container.scrollTop, 250, 'removed keys fall back to the saved primary index')

    scroller.suspend()
    items.value = Array.from({ length: 2 }, (_, index) => ({ id: `suspension-test:short-${index}` }))
    scroller.resume()
    assert.equal(container.scrollTop, 100, 'short lists use their final item and clamp to their maximum')
})

test('active replacement suspended before the post watcher defers its cleanup', { concurrency: false }, async (t) => {
    const traversals = countHeightCacheTraversals(t)
    const { scroller, items } = setup(t, { count: 4, height: 100 })
    scroller.seedItemHeight('suspension-test:row-0', 120)
    scroller.invalidateZeroHeights()
    traversals.reset()
    items.value = items.value.slice(1)
    scroller.suspend()
    assert.equal(scroller.positions.value.length, 3, 'capture uses the active replacement')
    await nextTick()
    assert.deepEqual(traversals.counts, { keys: 0, values: 0, entries: 0, iterator: 0 })
    assert.equal(scroller.getItemHeight('suspension-test:row-0'), 120)
    scroller.resume()
    assert.equal(scroller.getItemHeight('suspension-test:row-0'), undefined)
    assert.equal(traversals.counts.entries, 1)
})

test('clamped anchor restore publishes intended window and retries within eight frames', async (t) => {
    const nativeRequest = globalThis.requestAnimationFrame
    const nativeCancel = globalThis.cancelAnimationFrame
    let nextHandle = 0
    const queued = new Map()
    globalThis.requestAnimationFrame = (callback) => {
        const handle = ++nextHandle
        queued.set(handle, callback)
        return handle
    }
    globalThis.cancelAnimationFrame = (handle) => queued.delete(handle)
    t.after(() => {
        globalThis.requestAnimationFrame = nativeRequest
        globalThis.cancelAnimationFrame = nativeCancel
    })
    const frame = () => {
        const [handle, callback] = queued.entries().next().value
        queued.delete(handle)
        callback()
    }
    const { scroller, container } = setup(t, { count: 10, height: 100, viewport: 100 })
    let actualTop = 0
    let writes = 0
    Object.defineProperty(container, 'scrollTop', {
        get: () => actualTop,
        set(value) {
            writes++
            actualTop = Math.max(0, Math.min(value, container.domHeight - container.clientHeight))
        },
    })
    container.scrollTop = 450
    scroller.syncScrollPosition()
    await nextTick()
    scroller.suspend()
    container.domHeight = 100
    scroller.resume()
    assert.equal(container.scrollTop, 0, 'the DOM clamps the first write')
    assert.equal(scroller.scrollTop.value, 450, 'reactive state holds the intended anchor')
    await nextTick()
    assert.deepEqual(scroller.renderRange.value, { start: 4, end: 6 })
    assert.equal(queued.size, 1)
    container.domHeight = 1000
    frame()
    assert.equal(container.scrollTop, 450)
    assert.equal(queued.size, 0)

    scroller.suspend()
    container.domHeight = 100
    scroller.resume()
    assert.equal(queued.size, 1)
    const writesBeforeOverride = writes
    scroller.scrollToTop()
    frame()
    assert.equal(writes, writesBeforeOverride + 1, 'explicit scroll blocks the retry write')
    assert.equal(queued.size, 0)

    container.domHeight = 1000
    container.scrollTop = 450
    scroller.syncScrollPosition()
    await nextTick()
    scroller.suspend()
    container.domHeight = 100
    scroller.resume()
    const writesBeforeRetries = writes
    for (let attempt = 0; attempt < 7; attempt++) frame()
    assert.equal(writes, writesBeforeRetries + 7, 'restore stops after eight writes total')
    assert.equal(queued.size, 0)
})
