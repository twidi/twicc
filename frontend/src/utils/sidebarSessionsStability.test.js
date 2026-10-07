import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { reactive } from 'vue'
import { getStableSessionList, retainSessionArray } from './sessionLists.js'
import { sessionSortComparator } from './sessionSort.js'
import { hasUnreadContent } from './sessions.js'

const source = readFileSync(new URL('./sidebarSessions.js', import.meta.url), 'utf8')
    .replace(/^import .*$/gm, '').replace(/export function /g, 'function ')
const computeSidebarSessionBlocks = new Function('sessionSortComparator', 'ALL_PROJECTS_ID',
    'isWorkspaceProjectId', 'extractWorkspaceId', 'hasUnreadContent', 'retainSessionArray',
    `${source}; return computeSidebarSessionBlocks`)(sessionSortComparator, '__all__',
    id => id.startsWith('workspace:'), id => id.slice(10), hasUnreadContent, retainSessionArray)

function fixture(scope) {
    const data = reactive({ sessions: {
        a: { id: 'a', project_id: 'p', mtime: 100 },
        b: { id: 'b', project_id: 'p-work', mtime: 90 },
        c: { id: 'c', project_id: 'q', mtime: 80 },
    }, processStates: {}, localState: { projects: {} }, startupProgress: {}, getProjects: [],
        getProjectScopeIds: id => id === 'p' ? ['p', 'p-work'] : [id],
        getProjectSessions(id) { return getStableSessionList(this, id, '__all__', () => false) },
        get getAllSessions() { return getStableSessionList(this, '__all__', '__all__', () => false) },
    })
    const options = { data, workspaces: { getVisibleProjectIds: () => ['p', 'p-work'], getWorkspaceById: () => null },
        effectiveProjectId: scope, activeWorkspaceId: null, sessionId: null,
        showArchived: false, showArchivedProjects: true, showActiveAcrossFilters: false }
    return { data, options }
}

test('equivalent sidebar blocks retain arrays for global project worktree and workspace scopes', () => {
    for (const scope of ['__all__', 'p', 'q', 'workspace:w']) {
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
