import assert from 'node:assert/strict'
import test from 'node:test'
import { buildChildrenIndex, sameChildrenIndex, selectRailTreeRows } from './railActiveSessionsTree.js'
import { selectRailActiveSessions } from './railActiveSessions.js'

const unreadSession = { last_new_content_at: '2026-10-05T10:00:00Z' }
const proc = (startedAt, extra = {}) => ({ state: 'user_turn', project_id: 'p', provider: 'claude', started_at: startedAt, ...extra })
const s = (id, mtime, extra = {}) => ({ id, project_id: 'p', mtime, ...extra })

function select({ sessions, processStates, collapsed = [], viewed = null }) {
    const flat = selectRailActiveSessions(processStates, sessions, viewed)
    return selectRailTreeRows(flat, {
        processStates, sessions, childrenIndex: buildChildrenIndex(sessions),
        isCollapsed: id => collapsed.includes(id), viewedId: viewed,
    })
}
const ids = rows => rows.map(row => row.session.id)

test('buildChildrenIndex groups ids by spawned_by and sameChildrenIndex compares content', () => {
    const sessions = { a: s('a', 1), b: s('b', 2, { spawned_by: 'a' }), c: s('c', 3, { spawned_by: 'a' }) }
    const index = buildChildrenIndex(sessions)
    assert.deepEqual([...index.get('a')], ['b', 'c'])
    assert.equal(sameChildrenIndex(index, buildChildrenIndex({ ...sessions })), true)
    assert.equal(sameChildrenIndex(index, buildChildrenIndex({ a: s('a', 1) })), false)
})

test('with no spawn links the rows equal the flat selection', () => {
    const sessions = { a: s('a', 1), b: s('b', 2) }
    const processStates = { a: proc(5), b: proc(9) }
    const flat = selectRailActiveSessions(processStates, sessions)
    const rows = select({ sessions, processStates })
    assert.deepEqual(ids(rows), ids(flat))
    assert.deepEqual(rows, flat.map(row => ({ ...row, groupId: row.session.id })))
})

test('rows carry the root node id of their tree as groupId, collapsed rows included', () => {
    const sessions = {
        idleRoot: s('idleRoot', 1), kid: s('kid', 1, { spawned_by: 'idleRoot' }),
        grand: s('grand', 1, { spawned_by: 'kid' }), solo: s('solo', 1),
        top: s('top', 1), mid: s('mid', 1, { spawned_by: 'top' }), leaf: s('leaf', 1, { spawned_by: 'mid' }),
    }
    const processStates = { kid: proc(70), grand: proc(65), solo: proc(60), leaf: proc(10) }
    const groups = rows => Object.fromEntries(rows.map(row => [row.session.id, row.groupId]))
    assert.deepEqual(groups(select({ sessions, processStates })),
        { kid: 'idleRoot', grand: 'idleRoot', solo: 'solo', leaf: 'top' })
    assert.deepEqual(groups(select({ sessions, processStates, collapsed: ['kid', 'top'] })),
        { kid: 'idleRoot', solo: 'solo', top: 'top' })
})

test('child follows its parent; roots follow the effective state', () => {
    const sessions = {
        root1: s('root1', 1), kid1: s('kid1', 1, { spawned_by: 'root1' }),
        root2: s('root2', 1), kid2: s('kid2', 1, { spawned_by: 'root2' }),
    }
    // root2 subtree started more recently (kid2 at 50), root1 subtree at 20.
    const processStates = { root1: proc(10), kid1: proc(20), root2: proc(5), kid2: proc(50) }
    assert.deepEqual(ids(select({ sessions, processStates })), ['root2', 'kid2', 'root1', 'kid1'])
})

test('an idle ancestor lifts its subtree by effective state but gets no button', () => {
    const sessions = {
        idleRoot: s('idleRoot', 1), kid: s('kid', 1, { spawned_by: 'idleRoot' }),
        other: s('other', 1),
    }
    const processStates = { kid: proc(70), other: proc(60) }
    const rows = select({ sessions, processStates })
    assert.deepEqual(ids(rows), ['kid', 'other'])
    assert.equal(rows.every(row => !row.cumulative), true)
})

test('a collapsed idle node is one row with the cumulative summary of node and descendants', () => {
    const sessions = {
        top: s('top', 1), mid: s('mid', 1, { spawned_by: 'top' }), leaf: s('leaf', 1, { spawned_by: 'mid' }),
        sib: s('sib', 1, { spawned_by: 'top' }),
    }
    const processStates = {
        leaf: proc(10, { state: 'assistant_turn' }), sib: proc(5, { pending_requests: [{ id: 'r' }] }),
    }
    const rows = select({ sessions, processStates, collapsed: ['top'] })
    assert.deepEqual(ids(rows), ['top'])
    assert.equal(rows[0].session, sessions.top)
    assert.equal(rows[0].cumulative.processCount, 2)
    assert.equal(rows[0].cumulative.pendingRequestCount, 1)
    assert.equal(rows[0].cumulative.hasAssistantTurn, true)
    assert.equal(rows[0].processState, undefined)
})

test('a collapsed node with an active descendant hides the descendant row', () => {
    const sessions = { p: s('p', 1), c: s('c', 1, { spawned_by: 'p' }), other: s('other', 1) }
    const processStates = { p: proc(10), c: proc(20), other: proc(1) }
    const rows = select({ sessions, processStates, collapsed: ['p'] })
    assert.deepEqual(ids(rows), ['p', 'other'])
    assert.equal(rows[0].cumulative.processCount, 2)
    assert.equal(rows[1].cumulative, undefined)
})

test('a node collapsed inside an expanded node keeps its siblings and ancestors as normal rows', () => {
    const sessions = {
        root: s('root', 1), a: s('a', 1, { spawned_by: 'root' }), a1: s('a1', 1, { spawned_by: 'a' }),
        b: s('b', 1, { spawned_by: 'root' }),
    }
    const processStates = { root: proc(1), a1: proc(30), b: proc(10) }
    const rows = select({ sessions, processStates, collapsed: ['a'] })
    assert.deepEqual(ids(rows), ['root', 'a', 'b'])
    assert.equal(rows[0].cumulative, undefined)
    assert.equal(rows[1].cumulative.processCount, 1)
    assert.equal(rows[2].cumulative, undefined)
})

test('an idle collapsed descendant of an active node counts its own subtree', () => {
    const sessions = {
        root: s('root', 1), a: s('a', 1, { spawned_by: 'root' }), a1: s('a1', 1, { spawned_by: 'a' }),
    }
    const processStates = { root: proc(1), a1: proc(30) }
    const rows = select({ sessions, processStates, collapsed: ['a'] })
    assert.deepEqual(ids(rows), ['root', 'a'])
    assert.equal(rows[1].cumulative.processCount, 1)
})

test('the viewed session never counts as unread in a cumulative summary', () => {
    const sessions = { p: s('p', 1), c: s('c', 1, { spawned_by: 'p', ...unreadSession }) }
    const processStates = { p: proc(1), c: proc(2) }
    assert.equal(select({ sessions, processStates, collapsed: ['p'] })[0].cumulative.unreadCount, 1)
    assert.equal(select({ sessions, processStates, collapsed: ['p'], viewed: 'c' })[0].cumulative.unreadCount, 0)
})

test('a missing parent makes the child a root', () => {
    const sessions = { c: s('c', 1, { spawned_by: 'ghost' }), other: s('other', 1) }
    const processStates = { c: proc(5), other: proc(9) }
    assert.deepEqual(ids(select({ sessions, processStates })), ['other', 'c'])
})

test('hidden and synthetic sessions never count in a cumulative summary', () => {
    const sessions = {
        p: s('p', 1), h: s('h', 1, { spawned_by: 'p', hidden: true }),
        syn: s('syn', 1, { spawned_by: 'p' }), real: s('real', 1, { spawned_by: 'p' }),
    }
    const processStates = {
        p: proc(1), h: proc(2, { state: 'assistant_turn' }),
        syn: proc(3, { synthetic: true, state: 'assistant_turn' }), real: proc(4),
    }
    const rows = select({ sessions, processStates, collapsed: ['p'] })
    assert.deepEqual(ids(rows), ['p'])
    assert.equal(rows[0].cumulative.processCount, 2)
    assert.equal(rows[0].cumulative.hasAssistantTurn, false)
})

test('ephemeral sessions stay flat roots and never lift an ancestor', () => {
    const sessions = { p: s('p', 1), e: s('e', 1, { spawned_by: 'p', ephemeral: true }) }
    const processStates = { p: proc(1), e: proc(2) }
    const rows = select({ sessions, processStates, collapsed: ['p'] })
    assert.deepEqual(ids(rows).sort(), ['e', 'p'])
})

test('a cron-excluded active session gives no row of its own', () => {
    const sessions = { p: s('p', 1), c: s('c', 1, { spawned_by: 'p' }) }
    const processStates = { p: proc(1), c: proc(2, { active_crons: [{ id: 'x' }] }) }
    assert.deepEqual(ids(select({ sessions, processStates })), ['p'])
})
