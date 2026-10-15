import assert from 'node:assert/strict'
import test from 'node:test'

import { resolveTimelineScope } from './scope.js'

const ALL = '__all__'
const base = {
    allProjectsId: ALL,
    projectsLoaded: true,
    workspaceOf: (id) => ({ w1: { allProjectIds: ['a', 'a-wt', 'b'] }, empty: { allProjectIds: [] } }[id] ?? null),
    projectOf: (id) => ({ a: { worktree_of: null }, 'a-wt': { worktree_of: 'a' }, b: { worktree_of: null } }[id] ?? null),
    projectScopeIds: (id) => (id === 'a' ? ['a', 'a-wt'] : [id]),
}
const resolve = (pageId, overrides = {}) => resolveTimelineScope({ ...base, pageId, ...overrides })

test('the global page sends no project ids, even before the projects are loaded', () => {
    assert.deepEqual(resolve(ALL), { state: 'ready', kind: 'global', projectId: null, projectIds: [] })
    assert.equal(resolve(ALL, { projectsLoaded: false }).state, 'ready')
})

test('a workspace sends every member and worktree, archived ones included', () => {
    assert.deepEqual(resolve('ws:w1'), { state: 'ready', kind: 'workspace', projectId: null, projectIds: ['a', 'a-wt', 'b'] })
})

test('a workspace with no project is empty and sends nothing, never an empty list', () => {
    const scope = resolve('ws:empty')
    assert.deepEqual([scope.state, scope.projectIds], ['empty', null])
})

test('an unresolved workspace, or projects not loaded yet, is loading and sends nothing', () => {
    assert.deepEqual([resolve('ws:unknown').state, resolve('ws:unknown').projectIds], ['loading', null])
    assert.deepEqual([resolve('ws:w1', { projectsLoaded: false }).state, resolve('ws:w1', { projectsLoaded: false }).projectIds], ['loading', null])
})

test('a project sends itself and its worktrees', () => {
    assert.deepEqual(resolve('a'), { state: 'ready', kind: 'project', projectId: 'a', projectIds: ['a', 'a-wt'] })
})

test('a worktree is a project of its own: it sends only itself and keeps its kind', () => {
    assert.deepEqual(resolve('a-wt'), { state: 'ready', kind: 'worktree', projectId: 'a-wt', projectIds: ['a-wt'] })
})

test('a project page waits for the projects, then reports an unknown project', () => {
    assert.deepEqual([resolve('a', { projectsLoaded: false }).state, resolve('a', { projectsLoaded: false }).projectIds], ['loading', null])
    const missing = resolve('ghost')
    assert.deepEqual([missing.state, missing.projectId, missing.projectIds], ['not-found', 'ghost', null])
})

test('a project page never resolves to an empty list', () => {
    const scope = resolve('b', { projectScopeIds: () => [] })
    assert.deepEqual(scope.projectIds, ['b'])
})
