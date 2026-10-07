import test from 'node:test'
import assert from 'node:assert/strict'
import { getGroupedDropTarget, startGroupedReorder } from './groupedReorder.js'

const lists = [
    { groupId: null, top: 0, bottom: 350, rows: [{ top: 0, height: 40 }, { top: 50, height: 140 }, { top: 200, height: 100 }] },
    { groupId: 'one', top: 50, bottom: 190, rows: [{ top: 100, height: 40 }, { top: 145, height: 40 }] },
    { groupId: 'empty', top: 200, bottom: 300, rows: [] },
]

test('a leaf can enter a populated group at each insertion point', () => {
    assert.deepEqual(getGroupedDropTarget(lists, 60), { groupId: 'one', index: 0 })
    assert.deepEqual(getGroupedDropTarget(lists, 130), { groupId: 'one', index: 1 })
    assert.deepEqual(getGroupedDropTarget(lists, 180), { groupId: 'one', index: 2 })
})

test('a leaf can enter an empty group or leave groups through the outside target', () => {
    assert.deepEqual(getGroupedDropTarget(lists, 250), { groupId: 'empty', index: 0 })
    assert.deepEqual(getGroupedDropTarget(lists, 320, false, true), { groupId: null, index: 3 })
    assert.deepEqual(getGroupedDropTarget(lists, 5), { groupId: null, index: 0 })
})

test('groups reorder only in the root list and cannot nest', () => {
    assert.deepEqual(getGroupedDropTarget(lists, 250, true), { groupId: null, index: 3 })
    assert.deepEqual(getGroupedDropTarget(lists, -5, true), { groupId: null, index: 0 })
    assert.equal(getGroupedDropTarget([], 0), null)
})

function eventTarget() {
    const handlers = new Map()
    return {
        addEventListener(type, fn) { (handlers.get(type) || handlers.set(type, new Set()).get(type)).add(fn) },
        removeEventListener(type, fn) { handlers.get(type)?.delete(fn) },
        dispatch(type, event = {}) { for (const fn of handlers.get(type) || []) fn(event) },
        count() { return [...handlers.values()].reduce((total, fns) => total + fns.size, 0) },
    }
}

function fixture(pointerType, collapsed = false, sourceIsGroup = false) {
    const frames = new Map()
    let next = 0
    const view = { ...eventTarget(), innerHeight: 600,
        getComputedStyle: () => ({ overflowY: 'visible' }),
        requestAnimationFrame(fn) { frames.set(++next, fn); return next },
        cancelAnimationFrame(id) { frames.delete(id) },
    }
    const overlays = []
    function overlay() {
        const node = { style: {}, removeAttribute() {}, setAttribute() {}, showPopover() {}, querySelectorAll: () => [], remove() { overlays.splice(overlays.indexOf(node), 1) } }
        return node
    }
    const doc = { ...eventTarget(), defaultView: view, body: { append: node => overlays.push(node) }, createElement: overlay }
    const root = { dataset: { groupId: '' }, children: [], getBoundingClientRect: () => ({ top: 0, bottom: 300, left: 0, right: 300, width: 300 }) }
    const header = { dataset: { groupHeader: 'group' }, getBoundingClientRect: () => ({ top: 100, bottom: 140 }) }
    const child = { style: { display: collapsed ? 'none' : '' }, dataset: { groupId: 'group' }, children: [], parentElement: { querySelector: () => header }, getBoundingClientRect: () => ({ top: 150, bottom: 200, left: 20, width: 280 }) }
    const editor = { nodeType: 1, getRootNode: () => ({}), isConnected: true, getClientRects: () => [1],
        getBoundingClientRect: root.getBoundingClientRect,
        querySelectorAll: selector => selector === '[data-group-header]' ? [header] : [root, child], querySelector: () => ({ getBoundingClientRect: () => ({ top: 250, bottom: 300 }) }),
    }
    const row = { dataset: { index: '0' }, parentElement: root, style: { cssText: 'color:red' },
        closest: () => editor, hasAttribute: () => true, cloneNode: overlay,
        getBoundingClientRect: () => ({ top: 0, bottom: 40, left: 0, width: 300, height: 40 }),
    }
    root.children = [row, { hasAttribute: () => true, getBoundingClientRect: () => ({ top: 100, bottom: 200, height: 100 }) }]
    let capture = false
    const handle = { ...eventTarget(), ownerDocument: doc, closest: () => row,
        setPointerCapture() { capture = true }, hasPointerCapture: () => capture, releasePointerCapture() { capture = false },
    }
    const entries = [sourceIsGroup ? { type: 'group', id: 'source', label: 'Source', items: [] } : { label: 'Leaf' }, { type: 'group', id: 'group', label: 'Group', items: [] }]
    const drops = []
    const hovered = []
    const pointer = (y, pointerId = 1) => ({ button: 0, pointerType, isPrimary: true, pointerId, clientX: 10, clientY: y, preventDefault() {} })
    let currentEntries = entries
    const cancel = startGroupedReorder(pointer(20), handle, () => currentEntries, (...args) => drops.push(args), () => {}, id => hovered.push(id))
    return { doc, view, handle, entries, drops, hovered, pointer, cancel, overlays,
        replace() { currentEntries = structuredClone(entries) },
        tick() { const pending = [...frames.values()]; frames.clear(); pending.forEach(fn => fn()) },
        clean() { assert.equal(doc.count() + view.count() + handle.count(), 0); assert.equal(frames.size, 0); assert.equal(overlays.length, 0); assert.equal(capture, false); assert.equal(row.style.cssText, 'color:red') },
    }
}

for (const type of ['mouse', 'touch', 'pen']) {
    test(`${type} moves a leaf into an empty group and commits only on release`, () => {
        const drag = fixture(type)
        drag.doc.dispatch('pointermove', drag.pointer(140, 2))
        drag.doc.dispatch('pointermove', drag.pointer(140))
        assert.deepEqual(drag.drops, [])
        drag.doc.dispatch('pointerup', drag.pointer(140))
        assert.deepEqual(drag.drops, [[{ groupId: null, index: 0 }, { groupId: 'group', index: 0 }]])
        drag.clean()
    })
}

for (const reason of ['pointercancel', 'Escape', 'blur', 'unmount', 'remote-update', 'replacement', 'outside']) {
    test(`${reason} cancels without saving and removes previews and listeners`, () => {
        const drag = fixture('touch')
        drag.doc.dispatch('pointermove', drag.pointer(140))
        if (reason === 'Escape') drag.doc.dispatch('keydown', { key: 'Escape', preventDefault() {}, stopPropagation() {} })
        else if (reason === 'blur') drag.view.dispatch('blur')
        else if (reason === 'unmount') drag.cancel()
        else if (reason === 'remote-update') { drag.entries[0].label = 'Updated'; drag.tick() }
        else if (reason === 'replacement') { drag.replace(); drag.tick() }
        else if (reason === 'outside') drag.doc.dispatch('pointerup', drag.pointer(1000))
        else drag.doc.dispatch(reason, drag.pointer(140))
        assert.deepEqual(drag.drops, [])
        drag.clean()
    })
}


test('hovering a collapsed group opens it and accepts a drop before the next render', () => {
    const drag = fixture('touch', true)
    drag.doc.dispatch('pointermove', drag.pointer(120))
    assert.ok(drag.hovered.includes('group'))
    assert.equal(drag.overlays[1].style.display, 'none')
    drag.doc.dispatch('pointerup', drag.pointer(120))
    assert.deepEqual(drag.drops, [[{ groupId: null, index: 0 }, { groupId: 'group', index: 0 }]])
    drag.clean()
})

test('hidden children cannot accept drops outside their collapsed header', () => {
    const drag = fixture('mouse', true)
    drag.doc.dispatch('pointermove', drag.pointer(180))
    drag.doc.dispatch('pointerup', drag.pointer(180))
    assert.deepEqual(drag.hovered, [])
    assert.equal(drag.drops[0][1].groupId, null)
    drag.clean()
})

test('dragging an entire group keeps destination groups collapsed and cannot nest', () => {
    const drag = fixture('mouse', true, true)
    drag.doc.dispatch('pointermove', drag.pointer(120))
    drag.doc.dispatch('pointerup', drag.pointer(120))
    assert.deepEqual(drag.hovered, [])
    assert.equal(drag.drops[0][1].groupId, null)
    drag.clean()
})


test('an empty group places the drop marker inside its content, below the header', () => {
    const drag = fixture('mouse')
    drag.doc.dispatch('pointermove', drag.pointer(180))
    assert.equal(drag.overlays[1].style.top, '158px')
    drag.cancel()
    drag.clean()
})
