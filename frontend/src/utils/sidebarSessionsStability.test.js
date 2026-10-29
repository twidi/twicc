import test from 'node:test'
import assert from 'node:assert/strict'
import { reactive } from 'vue'
import { computeSidebarSessionBlocks } from './sidebarSessions.js'
import { createFixtureStores } from '../../tests/helpers/sidebarFixture.js'

function fixture(scope) {
    const state = reactive({ sessions: {
        a: { id: 'a', project_id: 'p', mtime: 100 },
        b: { id: 'b', project_id: 'p-work', mtime: 90 },
        c: { id: 'c', project_id: 'q', mtime: 80 },
    }, projects: {}, processStates: {}, localState: { projects: {} }, startupProgress: {},
        settings: { isShowArchivedProjects: true } })
    // The shipped root index, scope lists, archived set, unread memo and startup probe.
    const shipped = createFixtureStores(state).data
    const data = {
        get sessions() { return state.sessions },
        get processStates() { return state.processStates },
        get localState() { return state.localState },
        getProjectScopeIds: id => id === 'p' ? ['p', 'p-work'] : [id],
        get rootSessions() { return shipped.rootSessions },
        get getScopeSessionList() { return shipped.getScopeSessionList },
        get archivedProjectIds() { return shipped.archivedProjectIds },
        get hasUnreadContentMemo() { return shipped.hasUnreadContentMemo },
        get inStartupPhase() { return shipped.inStartupPhase },
    }
    const options = { data, workspaces: { getVisibleProjectIds: () => ['p', 'p-work'], getWorkspaceById: () => null },
        effectiveProjectId: scope, activeWorkspaceId: null, sessionId: null,
        showArchived: false, showArchivedProjects: true, showActiveAcrossFilters: false }
    return { data, options }
}

test('equivalent sidebar blocks retain arrays for global project worktree and workspace scopes', () => {
    for (const scope of ['__all__', 'p', 'q', 'ws:w']) {
        const { options } = fixture(scope)
        const before = computeSidebarSessionBlocks(options)
        const next = computeSidebarSessionBlocks(options, before)
        assert.strictEqual(next, before, scope)
    }
})

test('real scope visibility pin and archive changes still update sidebar blocks', () => {
    const { data, options } = fixture('p')
    const before = computeSidebarSessionBlocks(options)
    assert.deepEqual(before.natural.map(s => s.id), ['a', 'b'])
    data.sessions.b.archived = true
    data.sessions.c.pinned = 'all'
    const next = computeSidebarSessionBlocks(options, before)
    assert.deepEqual(next.natural.map(s => s.id), ['a'])
    assert.deepEqual(next.crossFilterPinned.map(s => s.id), ['c'])
    assert.notStrictEqual(next, before)
    data.sessions.c.pinned = null
    data.processStates.c = { started_at: 1 }
    options.showActiveAcrossFilters = true
    const active = computeSidebarSessionBlocks(options, next)
    assert.deepEqual(active.crossFilterActive.map(s => s.id), ['c'])
})

function paginatedAncestorFixture() {
    const { data, options } = fixture('p')
    data.sessions.old = { id: 'old', project_id: 'p', mtime: 1, pinned: 'all', spawned_by: null }
    data.sessions.a.spawned_by = 'old'
    // The pagination window: only the sessions at or above mtime 50 are naturally listed.
    data.localState.projects.__all__ = { hasMoreSessions: true, oldestSessionMtime: 50 }
    options.effectiveProjectId = '__all__'
    return { data, options }
}

test('with showAsTree an in-scope pinned ancestor beyond the pagination window is in natural and not in crossFilterPinned', () => {
    const { options } = paginatedAncestorFixture()
    const blocks = computeSidebarSessionBlocks({ ...options, showAsTree: true })
    assert.ok(blocks.natural.some(s => s.id === 'old'))
    assert.deepEqual(blocks.crossFilterPinned.map(s => s.id), [])
})

test('without showAsTree the same data leaves it in crossFilterPinned', () => {
    const { options } = paginatedAncestorFixture()
    const blocks = computeSidebarSessionBlocks(options)
    assert.ok(!blocks.natural.some(s => s.id === 'old'))
    assert.deepEqual(blocks.crossFilterPinned.map(s => s.id), ['old'])
})
