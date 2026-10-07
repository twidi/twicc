import test from 'node:test'
import assert from 'node:assert/strict'
import { moveVisibleItems, getDropIndex, getScrollStep, startListReorder } from './listReorder.js'

test('moving across multiple visible rows preserves hidden slots', () => {
    const items = ['a', 'hidden', 'b', 'c', 'hidden-2', 'd']
    assert.equal(moveVisibleItems(items, [0, 2, 3, 5], 0, 3), true)
    assert.deepEqual(items, ['b', 'hidden', 'c', 'd', 'hidden-2', 'a'])
    assert.equal(moveVisibleItems(items, [0, 2, 3, 5], 3, 0), true)
    assert.deepEqual(items, ['a', 'hidden', 'b', 'c', 'hidden-2', 'd'])
})

test('invalid and unchanged moves leave the list intact', () => {
    for (const [from, to] of [[0, 0], [-1, 1], [0, 5], [0.5, 1]]) {
        const items = ['a', 'b', 'c']
        assert.equal(moveVisibleItems(items, [0, 1, 2], from, to), false)
        assert.deepEqual(items, ['a', 'b', 'c'])
    }
})

test('drop position crosses variable-height rows in either direction and clamps at the ends', () => {
    const rects = [{ top: 0, height: 40 }, { top: 50, height: 100 }, { top: 160, height: 30 }]
    assert.equal(getDropIndex(rects, 0, 90), 0)
    assert.equal(getDropIndex(rects, 0, 110), 1)
    assert.equal(getDropIndex(rects, 0, 100), 1)
    assert.equal(getDropIndex(rects, 0, 300), 2)
    assert.equal(getDropIndex(rects, 2, 90), 1)
    assert.equal(getDropIndex(rects, 2, 100), 1)
    assert.equal(getDropIndex(rects, 2, -50), 0)
})

test('auto-scroll runs only near the visible scroll edges', () => {
    assert.equal(getScrollStep(100, 100, 400), -12)
    assert.equal(getScrollStep(400, 100, 400), 12)
    assert.equal(getScrollStep(250, 100, 400), 0)
    assert.equal(getScrollStep(20, 100, 400), -12)
})

function eventTarget() {
    const listeners = new Map()
    return {
        addEventListener(type, handler) {
            if (!listeners.has(type)) listeners.set(type, new Set())
            listeners.get(type).add(handler)
        },
        removeEventListener(type, handler) { listeners.get(type)?.delete(handler) },
        dispatch(type, event = {}) {
            for (const handler of listeners.get(type) || []) handler(event)
        },
        listenerCount() { return [...listeners.values()].reduce((sum, set) => sum + set.size, 0) },
    }
}

function dragFixture(pointerType = 'mouse') {
    const frames = new Map()
    let nextFrame = 0
    const view = {
        ...eventTarget(), innerHeight: 500,
        getComputedStyle: (node) => ({ overflowY: node.overflowY || 'visible' }),
        requestAnimationFrame(callback) { frames.set(++nextFrame, callback); return nextFrame },
        cancelAnimationFrame(id) { frames.delete(id) },
    }
    const doc = { ...eventTarget(), defaultView: view }
    const scroller = {
        nodeType: 1, overflowY: 'auto', scrollTop: 0, getRootNode: () => ({}),
        getBoundingClientRect: () => ({ top: 0, bottom: 200 }),
    }
    const list = {
        nodeType: 1, parentElement: scroller, isConnected: true,
        hasAttribute: (name) => name === 'data-reorder-list',
        getClientRects: () => [1],
        getBoundingClientRect: () => ({ top: -scroller.scrollTop }),
    }
    const rows = ['a', 'b', 'c', 'd'].map((text, index) => ({
        parentElement: list, textContent: text, style: { cssText: 'color: red;' },
        hasAttribute: (name) => name === 'data-reorder-row',
        getBoundingClientRect: () => ({ top: index * 50, bottom: index * 50 + 40, height: 40 }),
    }))
    list.children = rows
    let captured = false
    const handle = {
        ...eventTarget(), ownerDocument: doc, closest: () => rows[0],
        setPointerCapture() { captured = true },
        hasPointerCapture() { return captured },
        releasePointerCapture() { captured = false },
    }
    const drops = []
    const active = []
    const pointer = (y, id = 1) => ({
        button: 0, isPrimary: true, pointerId: id, pointerType,
        clientX: 10, clientY: y, preventDefault() {},
    })
    const cancel = startListReorder(pointer(20), handle, (...args) => drops.push(args), (value) => active.push(value))
    return {
        doc, view, handle, rows, list, scroller, drops, active, pointer, cancel,
        tick() {
            const pending = [...frames.values()]
            frames.clear()
            pending.forEach((callback) => callback())
        },
        assertClean() {
            assert.equal(doc.listenerCount() + view.listenerCount() + handle.listenerCount(), 0)
            assert.equal(frames.size, 0)
            assert.equal(captured, false)
            assert.ok(rows.every((row) => row.style.cssText === 'color: red;'))
        },
    }
}

for (const pointerType of ['mouse', 'touch', 'pen']) {
    test(`${pointerType} can cross three rows and commits only at drop`, () => {
        const drag = dragFixture(pointerType)
        drag.doc.dispatch('pointermove', drag.pointer(180, 2))
        assert.deepEqual(drag.active, [])
        drag.doc.dispatch('pointermove', drag.pointer(180))
        assert.deepEqual(drag.drops, [])
        assert.deepEqual(drag.active, [true])
        drag.doc.dispatch('pointerup', drag.pointer(180))
        assert.deepEqual(drag.drops, [[0, 3]])
        drag.assertClean()
    })
}

test('a click on the handle does not reorder', () => {
    const drag = dragFixture()
    drag.doc.dispatch('pointerup', drag.pointer(20))
    assert.deepEqual(drag.drops, [])
    drag.assertClean()
})

for (const reason of ['pointercancel', 'Escape', 'blur', 'lostpointercapture', 'unmount', 'remote-update']) {
    test(`${reason} cancels a drag without changing the order`, () => {
        const drag = dragFixture('touch')
        drag.doc.dispatch('pointermove', drag.pointer(180))
        if (reason === 'Escape') drag.doc.dispatch('keydown', { key: 'Escape', preventDefault() {}, stopPropagation() {} })
        else if (reason === 'blur') drag.view.dispatch('blur')
        else if (reason === 'lostpointercapture') drag.handle.dispatch(reason, drag.pointer(180))
        else if (reason === 'unmount') drag.cancel()
        else if (reason === 'remote-update') { drag.rows[1].textContent = 'updated'; drag.tick() }
        else drag.doc.dispatch(reason, drag.pointer(180))
        assert.deepEqual(drag.drops, [])
        drag.assertClean()
    })
}

test('holding a touch near an edge scrolls the dialog body without more pointer moves', () => {
    const drag = dragFixture('touch')
    drag.doc.dispatch('pointermove', drag.pointer(190))
    drag.tick()
    drag.tick()
    assert.ok(drag.scroller.scrollTop > 0)
    drag.cancel()
    drag.assertClean()
})

test('dragging below the list keeps the preview inside its original scroll extent', () => {
    const drag = dragFixture('touch')
    drag.doc.dispatch('pointermove', drag.pointer(1000))
    assert.equal(drag.rows[0].style.transform, 'translateY(150px)')
    drag.cancel()
    drag.assertClean()
})
