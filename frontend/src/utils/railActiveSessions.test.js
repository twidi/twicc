import assert from 'node:assert/strict'
import test from 'node:test'
import { selectRailActiveSessions } from './railActiveSessions.js'

const idle = { state: 'user_turn', project_id: 'outside', provider: 'codex', started_at: 10 }
const cron = { ...idle, active_crons: [{ id: 'cron' }] }
const unread = { last_new_content_at: '2026-10-05T10:00:00Z' }
const ids = rows => rows.map(row => row.session.id)

test('selects global real processes and ignores unrelated stored sessions', () => {
    const processes = {
        idle, starting: { ...idle, state: 'starting' }, working: { ...idle, state: 'assistant_turn' },
        hidden: idle, synthetic: { ...idle, synthetic: true }, dead: { ...idle, state: 'dead' },
        draft: idle, ephemeral: idle,
    }
    const sessions = { hidden: { hidden: true }, unrelated: { id: 'unrelated' }, draft: { id: 'draft', draft: true }, ephemeral: { id: 'ephemeral', ephemeral: true } }
    assert.deepEqual(ids(selectRailActiveSessions(processes, sessions)), ['ephemeral', 'draft', 'idle', 'starting', 'working'])
})

test('excludes only the visible cron-only idle indicator', () => {
    const processes = {
        read: cron, unread: cron, muted: cron, archived: cron, child: cron,
        working: { ...cron, state: 'assistant_turn' }, starting: { ...cron, state: 'starting' },
        shell: { ...cron, background_work_in_progress: { shells: 1 } },
        pending: { ...cron, pending_requests: [{ id: 'approval' }] },
    }
    const sessions = {
        unread: { id: 'unread', ...unread }, muted: { ...unread, mute_on_user_turn: true }, archived: { ...unread, archived: true },
        child: { ...unread, parent_session_id: 'parent' },
    }
    assert.deepEqual(ids(selectRailActiveSessions(processes, sessions)), ['unread', 'working', 'starting', 'shell', 'pending'])
    assert.deepEqual(ids(selectRailActiveSessions({ unread: cron }, { unread }, 'unread')), [])
})

test('represents fallback metadata and indicator priority without inventing unread', () => {
    const process = { ...idle, session_title: 'Snapshot title', pending_requests: [{ id: 'approval' }] }
    const [fallback] = selectRailActiveSessions({ s: process }, {})
    assert.deepEqual(fallback.session, { id: 's', title: 'Snapshot title', project_id: 'outside', provider: 'codex' })
    assert.equal(fallback.indicatorKind, 'pending')
    assert.equal(fallback.unread, false)
    const session = { id: 's', project_id: 'real-project', provider: 'claude', ...unread }
    const [row] = selectRailActiveSessions({ s: process }, { s: session })
    assert.equal(row.session, session)
    assert.equal(row.indicatorKind, 'unread')
    assert.equal(row.pendingRequest, true)
    const [active] = selectRailActiveSessions({ s: process }, { s: session }, 's')
    assert.equal(active.unread, true)
    assert.equal(active.hasUnread, false)
    assert.equal(active.indicatorKind, 'pending')
})

test('matches All Projects priority for ephemeral, pinned and newest-started sessions', () => {
    const sessions = {
        ordinary: { id: 'ordinary' },
        pinnedOld: { id: 'pinnedOld', pinned: 'project' },
        ephemeralOld: { id: 'ephemeralOld', ephemeral: true, ephemeralStartedAt: '2026-10-04T10:00:00Z' },
        pinnedNew: { id: 'pinnedNew', pinned: 'all' },
        ephemeralNew: { id: 'ephemeralNew', ephemeral: true, ephemeralStartedAt: '2026-10-05T10:00:00Z' },
        newest: { id: 'newest' },
    }
    const processes = {
        newest: { ...idle, started_at: 100 },
        ephemeralNew: { ...idle, started_at: 1 },
        pinnedNew: { ...idle, started_at: 30 },
        ordinary: { ...idle, started_at: 40 },
        ephemeralOld: { ...idle, started_at: 90 },
        pinnedOld: { ...idle, started_at: 10 },
    }
    const expected = ['ephemeralNew', 'ephemeralOld', 'pinnedNew', 'pinnedOld', 'newest', 'ordinary']
    assert.deepEqual(ids(selectRailActiveSessions(processes, sessions)), expected)
    processes.pinnedOld.state = 'assistant_turn'
    processes.newest.state = 'starting'
    sessions.ordinary.title = 'Changed title'
    assert.deepEqual(ids(selectRailActiveSessions(processes, sessions)), expected)
})

test('preserves All Projects session collection order for equal starts after filtering', () => {
    const sessions = {
        z: { id: 'z' }, hidden: { id: 'hidden', hidden: true }, a: { id: 'a' },
        cron: { id: 'cron' }, unrelated: { id: 'unrelated' }, m: { id: 'm' },
    }
    const processes = { m: idle, a: idle, cron, hidden: idle, z: idle }
    assert.deepEqual(ids(selectRailActiveSessions(processes, sessions)), ['z', 'a', 'm'])
    assert.deepEqual(ids(selectRailActiveSessions({ z: idle, m: idle, a: idle }, sessions)), ['z', 'a', 'm'])
})
