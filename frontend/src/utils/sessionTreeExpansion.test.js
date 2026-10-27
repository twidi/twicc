import { test } from 'node:test'
import assert from 'node:assert/strict'
import { buildSessionTree } from './sessionTree.js'
import {
    createExpansionState,
    isExpanded,
    reconcileExpansion,
    setExpanded,
    revealPath,
} from './sessionTreeExpansion.js'

const s = (id, o = {}) => ({ id, project_id: 'p', mtime: 100, spawned_by: null, ...o })
const build = (candidates, processStates = {}) =>
    buildSessionTree(candidates, { lookup: id => candidates.find(x => x.id === id) ?? null, processStates })
const ACTIVE = { state: 'assistant_turn' }
// Tree: root -> w
const pair = (processStates = {}, extra = {}) =>
    build([s('root'), s('w', { spawned_by: 'root', ...extra })], processStates)
const rec = (state, tree, scopeKey = 'A', seed = () => [], settled = true) =>
    reconcileExpansion(state, tree, { settled, scopeKey, seedTriggerIds: seed })

test('a node with an active child auto-expands at first render', () => {
    const st = createExpansionState()
    rec(st, pair({ w: ACTIVE }), 'A', () => ['w'])
    assert.equal(isExpanded(st, 'root'), true)
})

test('auto-expansion is sticky after the child stops', () => {
    const st = createExpansionState()
    rec(st, pair({ w: ACTIVE }), 'A', () => ['w'])
    rec(st, pair({}))
    assert.equal(isExpanded(st, 'root'), true)
})

test('a node whose subtree has no trigger starts collapsed', () => {
    const st = createExpansionState()
    rec(st, pair())
    assert.equal(isExpanded(st, 'root'), false)
})

test('user collapse is kept when the same child changes state', () => {
    const st = createExpansionState()
    const t1 = pair({ w: ACTIVE })
    rec(st, t1, 'A', () => ['w'])
    setExpanded(st, 'root', false, t1)
    assert.equal(isExpanded(st, 'root'), false)
    rec(st, pair({}, { pinned: true }))
    assert.equal(isExpanded(st, 'root'), false)
})

test('a different descendant that becomes active re-expands a user-collapsed node', () => {
    const st = createExpansionState()
    const t1 = build([s('root'), s('w', { spawned_by: 'root' }), s('x', { spawned_by: 'root' })], { w: ACTIVE })
    rec(st, t1, 'A', () => ['w'])
    setExpanded(st, 'root', false, t1)
    const t2 = build([s('root'), s('w', { spawned_by: 'root' }), s('x', { spawned_by: 'root' })], { w: ACTIVE, x: ACTIVE })
    rec(st, t2)
    assert.equal(isExpanded(st, 'root'), true)
    assert.equal(st.explicit.root, undefined)
})

test('after a reload a persisted collapse with an already-active child stays collapsed', () => {
    const st = createExpansionState({ root: false })
    rec(st, pair({ w: ACTIVE }), 'A', () => ['w'])
    assert.equal(isExpanded(st, 'root'), false)
})

test('a session first loaded after the initial pass (a later page) that is already pinned or active re-expands a persisted collapse', () => {
    const st = createExpansionState({ root: false })
    rec(st, build([s('root'), s('w', { spawned_by: 'root' })]), 'A', () => [])
    assert.equal(isExpanded(st, 'root'), false)
    rec(st, build([s('root'), s('w', { spawned_by: 'root' }), s('p', { spawned_by: 'root', pinned: true })]))
    assert.equal(isExpanded(st, 'root'), true)
})

test('an ancestor fetched after the initial pass keeps its persisted collapse when its active child was already loaded at the initial pass', () => {
    const st = createExpansionState({ root: false })
    const ps = { w: ACTIVE }
    // initial pass: only w is loaded (root not yet fetched)
    rec(st, build([s('w', { spawned_by: 'root' })], ps), 'A', () => ['w'])
    // root arrives later
    rec(st, pair(ps))
    assert.equal(isExpanded(st, 'root'), false)
})

test('a persisted collapse in another scope survives the first view of that scope', () => {
    const st = createExpansionState({ root: false })
    rec(st, build([s('other')]), 'A', () => [])
    // scope B sessions load afterwards; absent from A's seed
    rec(st, pair({ w: ACTIVE }), 'B', () => ['w'])
    assert.equal(isExpanded(st, 'root'), false)
    assert.ok(st.seededScopes.has('B'))
})

test('reconcile with settled false changes nothing, and a persisted collapse survives an active child that appears only after the first unsettled call', () => {
    const st = createExpansionState({ root: false })
    rec(st, pair(), 'A', () => [], false)
    assert.equal(st.seededScopes.size, 0)
    assert.equal(st.seen.size, 0)
    rec(st, pair({ w: ACTIVE }), 'A', () => ['w'], true)
    assert.equal(isExpanded(st, 'root'), false)
})

test('a user expand is kept until the user collapses', () => {
    const st = createExpansionState()
    const t = pair()
    rec(st, t)
    setExpanded(st, 'root', true, t)
    rec(st, pair({ w: ACTIVE }))
    rec(st, pair())
    assert.equal(isExpanded(st, 'root'), true)
    setExpanded(st, 'root', false, t)
    assert.equal(isExpanded(st, 'root'), false)
})

test('revealPath expands every collapsed ancestor, also over a persisted collapse; a later user collapse sticks; the reveal never changes explicit', () => {
    const st = createExpansionState({ a: false, b: false })
    const t = build([s('a'), s('b', { spawned_by: 'a' }), s('c', { spawned_by: 'b' })])
    const path = revealPath(st, t, 'c')
    assert.deepEqual(path, ['a', 'b'])
    assert.equal(isExpanded(st, 'a'), true)
    assert.equal(isExpanded(st, 'b'), true)
    assert.deepEqual(st.explicit, { a: false, b: false })
    setExpanded(st, 'b', false, t)
    assert.equal(isExpanded(st, 'b'), false)
    assert.equal(isExpanded(st, 'a'), true)
})

test('revealPath with the previous path reveals only the new ancestor: an ancestor the user collapsed after the first reveal stays collapsed when a higher ancestor arrives', () => {
    const st = createExpansionState()
    const t1 = build([s('b'), s('c', { spawned_by: 'b' })])
    const p1 = revealPath(st, t1, 'c')
    assert.deepEqual(p1, ['b'])
    setExpanded(st, 'b', false, t1)
    const t2 = build([s('a'), s('b', { spawned_by: 'a' }), s('c', { spawned_by: 'b' })])
    const p2 = revealPath(st, t2, 'c', p1)
    assert.deepEqual(p2, ['a', 'b'])
    assert.equal(isExpanded(st, 'a'), true)
    assert.equal(isExpanded(st, 'b'), false)
})

test('revealPath on a tree where the node was later re-parented under a collapsed parent expands the new ancestor', () => {
    const st = createExpansionState()
    const t1 = build([s('c')])
    const p1 = revealPath(st, t1, 'c')
    assert.deepEqual(p1, [])
    const t2 = build([s('n'), s('c', { spawned_by: 'n' })])
    const p2 = revealPath(st, t2, 'c', p1)
    assert.deepEqual(p2, ['n'])
    assert.equal(isExpanded(st, 'n'), true)
})

test('a 3-level chain loaded top-down: calling revealPath after each arrival ends with parent and grandparent both expanded', () => {
    const st = createExpansionState()
    let path = []
    path = revealPath(st, build([s('c')]), 'c', path)
    path = revealPath(st, build([s('p'), s('c', { spawned_by: 'p' })]), 'c', path)
    path = revealPath(st, build([s('g'), s('p', { spawned_by: 'g' }), s('c', { spawned_by: 'p' })]), 'c', path)
    assert.deepEqual(path, ['g', 'p'])
    assert.equal(isExpanded(st, 'p'), true)
    assert.equal(isExpanded(st, 'g'), true)
})
