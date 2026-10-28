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
const rec = (state, tree) => reconcileExpansion(state, tree)
const siblings = (processStates = {}, extra = {}) =>
    build([s('root'), s('w', { spawned_by: 'root' }), s('x', { spawned_by: 'root', ...extra })], processStates)

test('an untouched node with an active child auto-expands', () => {
    const st = createExpansionState()
    rec(st, pair({ w: ACTIVE }))
    assert.equal(isExpanded(st, 'root'), true)
})

test('an untouched node with an unread or pinned child auto-expands', () => {
    const pinned = createExpansionState()
    rec(pinned, pair({}, { pinned: true }))
    assert.equal(isExpanded(pinned, 'root'), true)
})

test('auto-expansion is sticky after the child stops', () => {
    const st = createExpansionState()
    rec(st, pair({ w: ACTIVE }))
    rec(st, pair({}))
    assert.equal(isExpanded(st, 'root'), true)
})

test('a node whose subtree has no trigger starts collapsed', () => {
    const st = createExpansionState()
    rec(st, pair())
    assert.equal(isExpanded(st, 'root'), false)
})

test('a user collapse is final: the same child changing state does not reopen it', () => {
    const st = createExpansionState()
    const t1 = pair({ w: ACTIVE })
    rec(st, t1)
    setExpanded(st, 'root', false)
    assert.equal(isExpanded(st, 'root'), false)
    rec(st, pair({}, { pinned: true }))
    assert.equal(isExpanded(st, 'root'), false)
})

test('a user-collapsed node stays collapsed when a NEW descendant becomes active, unread or pinned', () => {
    const st = createExpansionState()
    const t1 = siblings({ w: ACTIVE })
    rec(st, t1)
    setExpanded(st, 'root', false)
    rec(st, siblings({ w: ACTIVE, x: ACTIVE }))
    assert.equal(isExpanded(st, 'root'), false)
    rec(st, siblings({ w: ACTIVE }, { pinned: true }))
    assert.equal(isExpanded(st, 'root'), false)
    rec(st, siblings({ w: ACTIVE }, { unread: true }))
    assert.equal(isExpanded(st, 'root'), false)
    assert.equal(st.explicit.root, false)
})

test('a persisted collapse stays collapsed with an active child at load and after later triggers', () => {
    const st = createExpansionState({ root: false })
    rec(st, pair({ w: ACTIVE }))
    assert.equal(isExpanded(st, 'root'), false)
    rec(st, siblings({ w: ACTIVE, x: ACTIVE }, { pinned: true }))
    assert.equal(isExpanded(st, 'root'), false)
    assert.deepEqual(st.explicit, { root: false })
})

test('a user expand is kept until the user collapses', () => {
    const st = createExpansionState()
    const t = pair()
    rec(st, t)
    setExpanded(st, 'root', true)
    rec(st, pair({ w: ACTIVE }))
    rec(st, pair())
    assert.equal(isExpanded(st, 'root'), true)
    setExpanded(st, 'root', false)
    assert.equal(isExpanded(st, 'root'), false)
})

test('the module keeps no trace of the removed "seen" mechanism', () => {
    const st = createExpansionState()
    assert.deepEqual(Object.keys(st).sort(), ['auto', 'explicit', 'revealed'])
})

test('revealPath expands every collapsed ancestor, also over a persisted collapse; a later user collapse sticks; the reveal never changes explicit', () => {
    const st = createExpansionState({ a: false, b: false })
    const t = build([s('a'), s('b', { spawned_by: 'a' }), s('c', { spawned_by: 'b' })])
    const path = revealPath(st, t, 'c')
    assert.deepEqual(path, ['a', 'b'])
    assert.equal(isExpanded(st, 'a'), true)
    assert.equal(isExpanded(st, 'b'), true)
    assert.deepEqual(st.explicit, { a: false, b: false })
    setExpanded(st, 'b', false)
    assert.equal(isExpanded(st, 'b'), false)
    assert.equal(isExpanded(st, 'a'), true)
})

test('revealPath with the previous path reveals only the new ancestor: an ancestor the user collapsed after the first reveal stays collapsed when a higher ancestor arrives', () => {
    const st = createExpansionState()
    const t1 = build([s('b'), s('c', { spawned_by: 'b' })])
    const p1 = revealPath(st, t1, 'c')
    assert.deepEqual(p1, ['b'])
    setExpanded(st, 'b', false)
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
