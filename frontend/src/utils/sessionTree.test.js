import { test } from 'node:test'
import assert from 'node:assert/strict'
import { buildSessionTree } from './sessionTree.js'

const s = (id, o = {}) => ({ id, project_id: 'p', mtime: 100, spawned_by: null, ...o })
const lookupOf = list => id => list.find(x => x.id === id) ?? null
const build = (candidates, extra = []) =>
    buildSessionTree(candidates, { lookup: lookupOf([...candidates, ...extra]) })
const parent = (t, id) => t.nodes.get(id).parentId
const kids = (t, id) => t.nodes.get(id).childIds
const hint = (t, id) => t.nodes.get(id).hintParent?.id ?? null

test('child nests under a present parent', () => {
    const t = build([s('a'), s('b', { spawned_by: 'a' })])
    assert.deepEqual(t.rootIds, ['a'])
    assert.equal(parent(t, 'b'), 'a')
    assert.deepEqual(kids(t, 'a'), ['b'])
    assert.equal(hint(t, 'b'), null)
})

test('grandparent adopts when the parent is loaded but not a candidate', () => {
    const t = build([s('g'), s('c', { spawned_by: 'p1' })], [s('p1', { spawned_by: 'g' })])
    assert.deepEqual(t.rootIds, ['g'])
    assert.equal(parent(t, 'c'), 'g')
    assert.deepEqual(kids(t, 'g'), ['c'])
    assert.equal(hint(t, 'c'), null)
})

test('root with a loaded out-of-candidates parent carries hintParent', () => {
    const t = build([s('c', { spawned_by: 'p1' })], [s('p1')])
    assert.deepEqual(t.rootIds, ['c'])
    assert.equal(parent(t, 'c'), null)
    assert.equal(hint(t, 'c'), 'p1')
})

test('hidden, sub-agent, ephemeral and draft parents give no hint and end the chain', () => {
    const cases = [
        { hidden: true },
        { parent_session_id: 'x' },
        { ephemeral: true },
        { draft: true },
    ]
    for (const flags of cases) {
        const t = build([s('c', { spawned_by: 'p1' })], [s('p1', flags)])
        assert.deepEqual(t.rootIds, ['c'], JSON.stringify(flags))
        assert.equal(parent(t, 'c'), null)
        assert.equal(t.nodes.get('c').hintParent, null)
    }
})

test('unknown parent gives a root without hint', () => {
    const t = build([s('c', { spawned_by: 'nope' })])
    assert.deepEqual(t.rootIds, ['c'])
    assert.equal(parent(t, 'c'), null)
    assert.equal(t.nodes.get('c').hintParent, null)
})

test('a spawned_by cycle yields roots, never an empty forest', () => {
    const two = build([s('a', { spawned_by: 'b' }), s('b', { spawned_by: 'a' })])
    assert.deepEqual(two.rootIds, ['a', 'b'])
    for (const id of ['a', 'b']) {
        assert.equal(parent(two, id), null)
        assert.equal(hint(two, id), null)
        assert.deepEqual(kids(two, id), [])
    }

    const three = build([
        s('a', { spawned_by: 'c' }), s('b', { spawned_by: 'a' }), s('c', { spawned_by: 'b' }),
    ])
    assert.deepEqual(three.rootIds, ['a', 'b', 'c'])
    for (const id of ['a', 'b', 'c']) {
        assert.equal(parent(three, id), null)
        assert.equal(hint(three, id), null)
    }

    const self = build([s('a', { spawned_by: 'a' })])
    assert.deepEqual(self.rootIds, ['a'])
    assert.equal(parent(self, 'a'), null)
    assert.equal(hint(self, 'a'), null)

    const tail = build([
        s('a', { spawned_by: 'b' }), s('b', { spawned_by: 'a' }), s('t', { spawned_by: 'a' }),
    ])
    assert.deepEqual(tail.rootIds, ['a', 'b'])
    assert.equal(parent(tail, 't'), 'a')
    assert.deepEqual(kids(tail, 'a'), ['t'])
    assert.equal(hint(tail, 'a'), null)
    assert.equal(hint(tail, 'b'), null)
})

test('ephemeral and draft candidates are roots with no children even when a session points at them', () => {
    for (const flags of [{ ephemeral: true }, { draft: true }]) {
        const t = build([s('e', flags), s('c', { spawned_by: 'e' })])
        assert.deepEqual(t.rootIds, ['e', 'c'], JSON.stringify(flags))
        assert.deepEqual(kids(t, 'e'), [])
        assert.equal(hint(t, 'e'), null)
        assert.equal(parent(t, 'c'), null)
        assert.equal(hint(t, 'c'), null)
    }
})
