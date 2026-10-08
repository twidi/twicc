// frontend/src/stores/framePool.test.js
//
// Run with:  node --test src/stores/framePool.test.js   (from the frontend dir)
//
// No test framework is wired into the frontend, so this uses Node's built-in
// test runner (node:test). Pinia works in bare Node without a DOM.

import { test, beforeEach } from 'node:test'
import assert from 'node:assert/strict'
import { createPinia, setActivePinia } from 'pinia'
import { useFramePoolStore } from './framePool.js'

beforeEach(() => {
    setActivePinia(createPinia())
})

test('registration order is append-only and survives middle deletion', () => {
    const pool = useFramePoolStore()
    pool.register('a', { src: 'x' })
    pool.register('b', { src: 'x' })
    pool.register('c', { src: 'x' })
    pool.unregister('b')
    pool.register('d', { src: 'x' })
    assert.deepEqual(Object.keys(pool.frames), ['a', 'c', 'd'])
})

test('patch and setRect only touch existing frames', () => {
    const pool = useFramePoolStore()
    pool.patch('ghost', { visible: true }) // no throw, no creation
    pool.setRect('ghost', { x: 1, y: 1, width: 1, height: 1 })
    assert.deepEqual(Object.keys(pool.frames), [])
    pool.register('a', { src: 'x' })
    pool.patch('a', { visible: true, zTier: 'overlay' })
    assert.equal(pool.frames.a.visible, true)
    assert.equal(pool.frames.a.zTier, 'overlay')
})

test('divider drag depth nests and clamps', () => {
    const pool = useFramePoolStore()
    assert.equal(pool.isDividerDragging, false)
    pool.beginDividerDrag()
    pool.beginDividerDrag()
    pool.endDividerDrag()
    assert.equal(pool.isDividerDragging, true)
    pool.endDividerDrag()
    pool.endDividerDrag() // extra end must not go negative
    assert.equal(pool.isDividerDragging, false)
})

test('geometry epoch increments', () => {
    const pool = useFramePoolStore()
    pool.bumpGeometry()
    pool.bumpGeometry()
    assert.equal(pool.geometryEpoch, 2)
})

test('setFrameEl / setOverlayEl are independent and null-safe', () => {
    const pool = useFramePoolStore()
    pool.setFrameEl('ghost', {}) // no throw, no creation
    assert.deepEqual(Object.keys(pool.frames), [])
    pool.register('a', { src: 'x' })
    const iframe = { tag: 'iframe' }
    const overlay = { tag: 'div' }
    pool.setFrameEl('a', iframe)
    pool.setOverlayEl('a', overlay)
    assert.equal(pool.frameEl('a'), iframe)
    assert.equal(pool.frameOverlayEl('a'), overlay)
    pool.setFrameEl('a', null)
    assert.equal(pool.frameEl('a'), null)
    // Overlay is untouched by the frame setter.
    assert.equal(pool.frameOverlayEl('a'), overlay)
})

test('ensureRegistered patches existing descriptors without replacing frame or DOM references', () => {
    const pool = useFramePoolStore()
    const frame = pool.ensureRegistered('inline-a', { src: 'old', remountKey: 0 })
    pool.ensureRegistered('inline-b', { src: 'other' })
    const el = { tag: 'iframe' }
    pool.setFrameEl('inline-a', el)
    pool.patch('inline-a', { visible: true })
    const again = pool.ensureRegistered('inline-a', { src: 'new' })
    assert.equal(again, frame)
    assert.equal(frame.el, el)
    assert.equal(frame.visible, true)
    assert.equal(frame.src, 'new')
    assert.equal(frame.remountKey, 0)
    assert.deepEqual(Object.keys(pool.frames), ['inline-a', 'inline-b'])
})
