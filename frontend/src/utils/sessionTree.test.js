import { test } from 'node:test'
import assert from 'node:assert/strict'
import { buildSessionTree, flattenTree, TREE_MAX_INDENT_LEVEL } from './sessionTree.js'

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

// --- Task 3: effective state, ordering, triggers, summary ---

const run = (id, started_at) => ({ [id]: { started_at } })
const buildWith = (candidates, opts = {}) =>
    buildSessionTree(candidates, { lookup: lookupOf(candidates), ...opts })

test('idle root with an active child sorts before a newer idle root', () => {
    const t = buildWith(
        [s('old', { mtime: 10 }), s('kid', { mtime: 5, spawned_by: 'old' }), s('new', { mtime: 500 })],
        { processStates: run('kid', 50) },
    )
    assert.deepEqual(t.rootIds, ['old', 'new'])
    assert.equal(t.nodes.get('old').effective.active, true)
    assert.equal(t.nodes.get('new').effective.active, false)
})

test('a pinned descendant makes the root pinned and sorts it first', () => {
    const t = buildWith([
        s('a', { mtime: 10 }), s('k', { mtime: 5, spawned_by: 'a', pinned: 'all' }), s('b', { mtime: 500 }),
    ])
    assert.deepEqual(t.rootIds, ['a', 'b'])
    assert.equal(t.nodes.get('a').effective.pinned, 'all')
    assert.equal(t.nodes.get('b').effective.pinned, null)
})

test('activeKey is the highest started_at of the subtree; two active subtrees order by it', () => {
    const t = buildWith(
        [
            s('a'), s('a1', { spawned_by: 'a' }), s('a2', { spawned_by: 'a' }),
            s('b'), s('b1', { spawned_by: 'b' }),
        ],
        { processStates: { a1: { started_at: 10 }, a2: { started_at: 90 }, b1: { started_at: 50 } } },
    )
    assert.equal(t.nodes.get('a').effective.activeKey, 90)
    assert.equal(t.nodes.get('b').effective.activeKey, 50)
    assert.deepEqual(t.rootIds, ['a', 'b'])
    assert.deepEqual(kids(t, 'a'), ['a2', 'a1'])
    assert.equal(buildWith([s('x')]).nodes.get('x').effective.activeKey, 0)
})

test('a write to an active node (its mtime grows) does not reorder the siblings', () => {
    const make = mtime => [
        s('a'), s('a1', { spawned_by: 'a', mtime }), s('b'), s('b1', { spawned_by: 'b' }),
    ]
    const ps = { a1: { started_at: 20 }, b1: { started_at: 10 } }
    const before = buildWith(make(100), { processStates: ps })
    const after = buildWith(make(9999), { processStates: ps })
    assert.deepEqual(before.rootIds, ['a', 'b'])
    assert.deepEqual(after.rootIds, before.rootIds)
})

test('idle subtrees order by the highest mtime of the subtree', () => {
    const t = buildWith([
        s('a', { mtime: 10 }), s('a1', { spawned_by: 'a', mtime: 900 }), s('b', { mtime: 500 }),
    ])
    assert.equal(t.nodes.get('a').effective.dateKey, 900)
    assert.deepEqual(t.rootIds, ['a', 'b'])
})

test('children under one parent use the same effective ordering', () => {
    const t = buildWith(
        [
            s('p'), s('idleNew', { spawned_by: 'p', mtime: 300 }), s('idleOld', { spawned_by: 'p', mtime: 100 }),
            s('act', { spawned_by: 'p', mtime: 1 }), s('pin', { spawned_by: 'p', mtime: 2, pinned: 'project' }),
        ],
        { processStates: run('act', 5) },
    )
    assert.deepEqual(kids(t, 'p'), ['pin', 'act', 'idleNew', 'idleOld'])
})

test('triggerIds exclude the node itself and the viewed session counts as not unread', () => {
    const t = buildWith(
        [
            s('p', { pinned: 'all' }), s('u', { spawned_by: 'p' }), s('v', { spawned_by: 'p' }),
            s('act', { spawned_by: 'p' }), s('quiet', { spawned_by: 'p' }),
        ],
        {
            processStates: run('act', 5),
            viewedId: 'v',
            isUnread: session => session.id === 'u' || session.id === 'v',
        },
    )
    const node = t.nodes.get('p')
    assert.deepEqual([...node.triggerIds].sort(), ['act', 'u'])
    assert.equal(node.summary.descendants, 4)
    assert.equal(node.summary.active, 1)
    assert.equal(node.summary.unread, 1)
    assert.equal(node.summary.pinned, 0)
    assert.deepEqual(t.nodes.get('quiet').triggerIds, [])
})

test('summary.latestDate is null when the subtree date equals the node\'s own mtime', () => {
    const same = buildWith([s('a', { mtime: 100 }), s('k', { spawned_by: 'a', mtime: 50 })])
    assert.equal(same.nodes.get('a').summary.latestDate, null)
    const newer = buildWith([s('a', { mtime: 100 }), s('k', { spawned_by: 'a', mtime: 700 })])
    assert.equal(newer.nodes.get('a').summary.latestDate, 700)
})

// A(B(D), C) and a second root E(F).
const flatForest = () => build([
    s('a', { mtime: 500 }),
    s('b', { spawned_by: 'a', mtime: 400 }),
    s('c', { spawned_by: 'a', mtime: 300 }),
    s('d', { spawned_by: 'b', mtime: 200 }),
    s('e', { mtime: 100 }),
    s('f', { spawned_by: 'e', mtime: 50 }),
])

test('collapsed nodes hide their children', () => {
    const rows = flattenTree(flatForest(), () => false)
    assert.deepEqual(rows, [{ id: 'a', depth: 0 }, { id: 'e', depth: 0 }])
})

test('expanded nodes list children right after the parent with depth + 1', () => {
    const rows = flattenTree(flatForest(), id => id === 'a' || id === 'b')
    assert.deepEqual(rows, [
        { id: 'a', depth: 0 },
        { id: 'b', depth: 1 },
        { id: 'd', depth: 2 },
        { id: 'c', depth: 1 },
        { id: 'e', depth: 0 },
    ])
})

test('isExpanded = () => true lists the whole forest in display order', () => {
    const rows = flattenTree(flatForest(), () => true)
    assert.deepEqual(rows.map(r => r.id), ['a', 'b', 'd', 'c', 'e', 'f'])
    assert.deepEqual(rows.map(r => r.depth), [0, 1, 2, 1, 0, 1])
})

test('TREE_MAX_INDENT_LEVEL is 3', () => {
    assert.equal(TREE_MAX_INDENT_LEVEL, 3)
})
