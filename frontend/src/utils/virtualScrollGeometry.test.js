import test from 'node:test'
import assert from 'node:assert/strict'

const module = await import('./virtualScrollGeometry.js').catch(error => {
    if (error.code !== 'ERR_MODULE_NOT_FOUND') throw error
    return {}
})

function setup() {
    assert.equal(typeof module.createVirtualScrollGeometry, 'function', 'geometry factory must exist')
    const geometry = module.createVirtualScrollGeometry({ minItemHeight: 24 })
    const heights = new Map()
    const reads = []
    const read = key => { reads.push(key); return heights.get(key) }
    return { geometry, heights, reads, read }
}

function oracle(keys, heights) {
    let top = 0
    return keys.map((key, index) => {
        const height = heights.get(key) ?? 24
        const entry = { index, key, top, height }
        top += height
        return entry
    })
}

function assertGeometry(actual, keys, heights) {
    const expected = oracle(keys, heights)
    assert.deepEqual(actual, expected)
    const total = positions => positions.length ? positions.at(-1).top + positions.at(-1).height : 0
    assert.equal(total(actual), total(expected))
}

test('structural edits and mixed height changes match a full oracle', () => {
    const { geometry, heights, read } = setup()
    const snapshots = [[], ['a', 'b', 'c'], ['a', 'b', 'c', 'd'], ['z', 'a', 'b', 'c', 'd'],
        ['z', 'a', 'x', 'b', 'c', 'd'], ['z', 'a', 'x', 'c', 'd'], ['z', 'a'],
        ['a', 'z'], ['a', 'replacement'], [], ['a', 'a', 'b'], ['a', 'b']]
    const retained = []
    for (const keys of snapshots) {
        if (keys.length) {
            heights.set(keys.at(-1), 37.5)
            geometry.invalidateKey(keys.at(-1))
        }
        const positions = geometry.reconcile(Object.freeze(keys), read)
        assertGeometry(positions, keys, heights)
        retained.push([positions, structuredClone(positions)])
    }
    for (const [positions, copy] of retained) assert.deepEqual(positions, copy)
})

test('index 799 rebuilds exactly 201 entries and retains 799 prefix references', () => {
    const { geometry, heights, reads, read } = setup()
    let keyReads = 0
    const keys = new Proxy(Object.freeze(Array.from({ length: 1000 }, (_, index) => index)), {
        get(target, property, receiver) {
            if (/^\d+$/.test(String(property))) keyReads++
            return Reflect.get(target, property, receiver)
        },
    })
    const old = geometry.reconcile(keys, read)
    const copy = structuredClone(old)
    reads.length = 0
    keyReads = 0
    heights.set(799, 48)
    geometry.invalidateKey(799)
    assert.deepEqual(reads, [])
    const next = geometry.reconcile(keys, read)
    assert.deepEqual(reads, Array.from({ length: 201 }, (_, index) => index + 799))
    assert.equal(keyReads, 201)
    assert.notEqual(next, old)
    for (let index = 0; index < 1000; index++) {
        if (index < 799) assert.equal(next[index], old[index])
        else assert.notEqual(next[index], old[index])
    }
    assert.deepEqual(old, copy)
    assertGeometry(next, keys, heights)
})

test('identical snapshots require no height reads and equal estimates retain publication', () => {
    const { geometry, heights, reads, read } = setup()
    const keys = Object.freeze(['a', 'b'])
    const old = geometry.reconcile(keys, read)
    reads.length = 0
    assert.equal(geometry.reconcile(keys, read), old)
    assert.equal(geometry.reconcile(Object.freeze([...keys]), read), old)
    assert.deepEqual(reads, [])
    heights.set('a', 24)
    geometry.invalidateKey('a')
    assert.equal(geometry.reconcile(keys, read), old)
    reads.length = 0
    assert.equal(geometry.reconcile(keys, read), old)
    assert.deepEqual(reads, [])
})

test('unknown future seeds leave current geometry intact and survive insertion', () => {
    const { geometry, heights, reads, read } = setup()
    const keys = Object.freeze(['a', 'b'])
    const old = geometry.reconcile(keys, read)
    reads.length = 0
    heights.set('future', 90)
    geometry.invalidateKey('future')
    assert.equal(geometry.reconcile(keys, read), old)
    assert.deepEqual(reads, [])
    const next = geometry.reconcile(Object.freeze(['a', 'future', 'b']), read)
    assert.equal(next[0], old[0])
    assertGeometry(next, ['a', 'future', 'b'], heights)
})

test('batched invalidation takes the minimum and opposite changes do not cancel', () => {
    const { geometry, heights, reads, read } = setup()
    const keys = Object.freeze(['a', 'b', 'c', 'd'])
    const old = geometry.reconcile(keys, read)
    reads.length = 0
    heights.set('d', 14)
    geometry.invalidateKey('d')
    heights.set('b', 34)
    geometry.invalidateKey('b')
    const next = geometry.reconcile(keys, read)
    assert.deepEqual(reads, ['b', 'c', 'd'])
    assert.equal(next[0], old[0])
    assertGeometry(next, keys, heights)
    assert.equal(next.at(-1).top + next.at(-1).height, 96)
    reads.length = 0
    heights.set('a', 50)
    geometry.invalidateKey('a')
    assertGeometry(geometry.reconcile(keys, read), keys, heights)
    assert.deepEqual(reads, keys)
})

test('delete and clear invalidations restore estimates without mutating publications', () => {
    const { geometry, heights, read } = setup()
    const keys = Object.freeze(['a', 'b', 'c'])
    heights.set('a', 0)
    heights.set('b', 45)
    heights.set('c', 60)
    const old = geometry.reconcile(keys, read)
    geometry.invalidateKey('b')
    heights.delete('b')
    assertGeometry(geometry.reconcile(keys, read), keys, heights)
    for (const key of heights.keys()) geometry.invalidateKey(key)
    heights.clear()
    assertGeometry(geometry.reconcile(keys, read), keys, heights)
    assert.deepEqual(old, [
        { index: 0, key: 'a', top: 0, height: 0 },
        { index: 1, key: 'b', top: 0, height: 45 },
        { index: 2, key: 'c', top: 45, height: 60 },
    ])
})

test('duplicate keys reconstruct conservatively and invalidate every occurrence', () => {
    const { geometry, heights, reads, read } = setup()
    const keys = Object.freeze(['x', 'a', 'a', 'b'])
    const old = geometry.reconcile(keys, read)
    heights.set('a', 80)
    geometry.invalidateKey('a')
    reads.length = 0
    const next = geometry.reconcile(keys, read)
    assert.deepEqual(reads, keys)
    assertGeometry(next, keys, heights)
    assert.notEqual(next[0], old[0])
})

test('structural reconciliation combines pending height invalidation with its first mismatch', () => {
    const { geometry, heights, reads, read } = setup()
    const old = geometry.reconcile(Object.freeze(['a', 'b', 'c', 'd']), read)
    heights.set('b', 60)
    geometry.invalidateKey('b')
    reads.length = 0
    const keys = Object.freeze(['a', 'b', 'c', 'e'])
    const next = geometry.reconcile(keys, read)
    assert.deepEqual(reads, ['b', 'c', 'e'])
    assert.equal(next[0], old[0])
    assertGeometry(next, keys, heights)
})
