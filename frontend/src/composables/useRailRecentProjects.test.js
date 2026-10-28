import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { createPinia, defineStore } from 'pinia'
import { computed, effectScope, reactive } from 'vue'
import { buildProjectActivityIndex, createProjectActivityComparator } from '../utils/projectActivity.js'
import { createWorkspaceMembership, getArchivedProjectIds, getWorktreeIdsByParent } from '../utils/projectScopes.js'
import { observeStageRuns } from '../utils/stageRuns.js'
import { useRailRecentProjects } from './useRailRecentProjects.js'

// Execute production getters in Pinia without the full stores' browser imports.
const dataSource = readFileSync(new URL('../stores/data.js', import.meta.url), 'utf8')
const dataStart = dataSource.indexOf('        // Data getters')
const dataEnd = dataSource.indexOf('        // Session scope of a project:', dataStart)
const projectGetter = dataSource.match(/^        getProject:.*$/m)[0]
const projectGetters = new Function('buildProjectActivityIndex', 'createProjectActivityComparator',
    'getArchivedProjectIds', 'getWorktreeIdsByParent',
    `return { ${dataSource.slice(dataStart, dataEnd)} ${projectGetter} }`,
)(buildProjectActivityIndex, createProjectActivityComparator, getArchivedProjectIds, getWorktreeIdsByParent)
const workspaceSource = readFileSync(new URL('../stores/workspaces.js', import.meta.url), 'utf8')
const workspaceStart = workspaceSource.indexOf('    getters: {') + '    getters: '.length
const workspaceEnd = workspaceSource.indexOf('\n    actions:', workspaceStart)
const workspaceGetters = (useDataStore, useSettingsStore) => new Function(
    'useDataStore', 'useSettingsStore', 'createWorkspaceMembership',
    `return ${workspaceSource.slice(workspaceStart, workspaceEnd).trim().replace(/,$/, '')}`,
)(useDataStore, useSettingsStore, createWorkspaceMembership)
const useProjects = defineStore('rail-recent-projects-test', { state: () => ({ projects: {} }), getters: projectGetters })
const NOW = 2_000_000
const WEEK = 604_800
const ids = items => items.map(item => item.id)

test('activity updates retain equivalent recent project and workspace lists', t => {
    const { store, api } = fixture(t, [{ id: 'p', mtime: NOW - 10 }], [{ id: 'w', projectIds: ['p'] }])
    const projects = api.recentProjects.value, workspaces = api.recentWorkspaces.value
    store.projects.p.mtime = NOW
    assert.strictEqual(api.recentProjects.value, projects)
    assert.strictEqual(api.recentWorkspaces.value, workspaces)
})

function fixture(t, projects, workspaces = [], clock = () => NOW) {
    const store = useProjects(createPinia())
    store.projects = Object.fromEntries(projects.map(project => [project.id, project]))
    const settings = reactive({ isShowArchivedProjects: false, isShowArchivedWorkspaces: false })
    const useWorkspaces = defineStore('rail-recent-workspaces-test', {
        state: () => ({ workspaces }),
        getters: workspaceGetters(() => store, () => settings),
    })
    const workspacesStore = useWorkspaces(createPinia())
    const scope = effectScope()
    const api = scope.run(() => useRailRecentProjects(store, workspacesStore, settings, clock))
    t.after(() => scope.stop())
    return { store, settings, workspacesStore, scope, api }
}

// The store glue of design 5.6 and 7.1 #3: the real getters of both stores,
// over Pinia, resolve workspace membership without project activity.
test('workspace membership getters follow a new worktree and ignore project activity', t => {
    const { store, workspacesStore } = fixture(t,
        [{ id: 'p', mtime: NOW - 10 }, { id: 'q', mtime: NOW - 20 }], [{ id: 'w', projectIds: ['p', 'q'] }])
    let evaluations = 0
    const membership = computed(() => {
        evaluations++
        return [workspacesStore.getVisibleProjectIds('w'), workspacesStore.getAllProjectIds('w')]
    })
    assert.deepEqual(membership.value, [['p', 'q'], ['p', 'q']])
    const resolver = workspacesStore.workspaceMembership

    store.projects.wt = { id: 'wt', worktree_of: 'p' }
    const [visible, all] = membership.value
    assert.deepEqual(visible, ['p', 'wt', 'q'])
    assert.deepEqual(all, ['p', 'wt', 'q'])
    assert.equal(evaluations, 2)

    const runs = {}
    const stop = observeStageRuns(name => { runs[name] = (runs[name] || 0) + 1 })
    t.after(stop)
    store.projects.p.mtime = NOW
    store.projects.wt.mtime = NOW + 1
    assert.strictEqual(membership.value[0], visible)
    assert.strictEqual(membership.value[1], all)
    assert.equal(evaluations, 2)
    assert.equal(runs['workspace:visible'] || 0, 0)
    assert.equal(runs['workspace:all'] || 0, 0)

    // Positive control: archiving a member reruns the visible stage and the computed.
    store.projects.q.archived = true
    assert.deepEqual(membership.value[0], ['p', 'wt'])
    assert.strictEqual(membership.value[1], all)
    assert.equal(evaluations, 3)
    assert.equal(runs['workspace:visible'], 1)
    assert.equal(runs['workspace:all'] || 0, 0)
    // One membership resolver per store: none of these changes rebuilt it.
    assert.strictEqual(workspacesStore.workspaceMembership, resolver)
})

// A strict cutoff or zero-date inclusion breaks the rolling-window contract.
test('includes seven-day boundary and excludes expired, zero, and missing activity', t => {
    const { api } = fixture(t, [
        { id: 'expired', mtime: NOW - WEEK - 1 },
        { id: 'boundary', mtime: NOW - WEEK },
        { id: 'zero', mtime: 0 },
        { id: 'missing' },
        { id: 'recent', mtime: NOW - 1 },
    ])
    assert.deepEqual(ids(api.recentProjects.value), ['recent', 'boundary'])
})

test('uses effective worktree activity and global order without worktree entries or an item cap', t => {
    const projects = Array.from({ length: 25 }, (_, i) => ({ id: `p${i}`, mtime: NOW - i }))
    const { api, store } = fixture(t, [
        { id: 'parent', name: 'Zebra', mtime: NOW - WEEK - 1 },
        { id: 'child', mtime: NOW + 1, worktree_of: 'parent', archived: true },
        ...projects,
    ])
    assert.deepEqual(ids(api.recentProjects.value), ['parent', ...projects.map(p => p.id)])
    assert.equal(api.recentProjects.value[0], store.projects.parent)
    assert.equal(store.projects.parent.mtime, NOW - WEEK - 1)
})

test('preserves manual workspace order and includes each associated workspace once', t => {
    const { api } = fixture(t, [{ id: 'p', mtime: NOW }, { id: 'q', mtime: NOW - 1 }], [
        { id: 'z', name: 'Zebra', projectIds: ['p', 'q', 'p'] },
        { id: 'a', name: 'Alpha', projectIds: ['q'] },
        { id: 'empty', projectIds: [] },
        { id: 'unknown', projectIds: ['absent'] },
    ])
    assert.deepEqual(ids(api.recentWorkspaces.value), ['z', 'a'])
})

test('associates main members without promoting child-only workspace membership', t => {
    const { api } = fixture(t, [
        { id: 'parent', mtime: NOW - WEEK - 1 },
        { id: 'child', mtime: NOW, worktree_of: 'parent' },
        { id: 'old', mtime: NOW - WEEK - 1 },
    ], [
        { id: 'child-only', projectIds: ['child'] },
        { id: 'main', projectIds: ['parent'] },
        { id: 'old-only', projectIds: ['old'] },
    ])
    assert.deepEqual(ids(api.recentWorkspaces.value), ['main'])
})

test('reacts to archived visibility and retains stale projects with sessions', t => {
    const { api, settings, store, workspacesStore } = fixture(t, [
        { id: 'stale', mtime: NOW, stale: true, sessions_count: 1 },
        { id: 'archived', mtime: NOW - 1, archived: true },
    ], [
        { id: 'visible', projectIds: ['stale'] },
        { id: 'hidden-member', projectIds: ['archived'] },
        { id: 'archived-ws', projectIds: ['stale'], archived: true },
    ])
    assert.deepEqual(ids(api.recentProjects.value), ['stale'])
    assert.deepEqual(ids(api.recentWorkspaces.value), ['visible'])
    settings.isShowArchivedProjects = true
    assert.deepEqual(ids(api.recentProjects.value), ['stale', 'archived'])
    assert.deepEqual(ids(api.recentWorkspaces.value), ['visible', 'hidden-member'])
    settings.isShowArchivedWorkspaces = true
    assert.deepEqual(ids(api.recentWorkspaces.value), ['visible', 'hidden-member', 'archived-ws'])
    store.projects.stale.archived = true
    settings.isShowArchivedProjects = false
    assert.deepEqual(ids(api.recentProjects.value), [])
    assert.deepEqual(ids(api.recentWorkspaces.value), [])
    settings.isShowArchivedProjects = true
    workspacesStore.workspaces[0].projectIds = ['absent']
    assert.deepEqual(ids(api.recentWorkspaces.value), ['hidden-member', 'archived-ws'])
})

test('reacts to child dates, cached additions, and workspace membership and stored order', t => {
    const { api, store, workspacesStore } = fixture(t, [
        { id: 'parent', mtime: NOW - WEEK - 1 },
        { id: 'other', mtime: NOW - 1 },
        { id: 'child', mtime: NOW - WEEK - 1, worktree_of: 'parent' },
    ], [{ id: 'main', projectIds: ['parent'] }, { id: 'other-ws', projectIds: ['other'] }])
    assert.deepEqual(ids(api.recentProjects.value), ['other'])
    assert.deepEqual(ids(api.recentWorkspaces.value), ['other-ws'])
    store.projects.child.mtime = NOW
    assert.deepEqual(ids(api.recentProjects.value), ['parent', 'other'])
    assert.deepEqual(ids(api.recentWorkspaces.value), ['main', 'other-ws'])
    workspacesStore.workspaces.reverse()
    assert.deepEqual(ids(api.recentWorkspaces.value), ['other-ws', 'main'])
    store.projects.new = { id: 'new', mtime: NOW + 1 }
    workspacesStore.workspaces.push({ id: 'new-ws', projectIds: ['new'] })
    assert.deepEqual(ids(api.recentProjects.value), ['new', 'parent', 'other'])
    assert.deepEqual(ids(api.recentWorkspaces.value), ['other-ws', 'main', 'new-ws'])
    store.projects.child.mtime = NOW - WEEK - 1
    assert.deepEqual(ids(api.recentProjects.value), ['new', 'other'])
    assert.deepEqual(ids(api.recentWorkspaces.value), ['other-ws', 'new-ws'])
})

test('ages projects and workspaces each minute and clears the timer when its scope stops', t => {
    t.mock.timers.enable({ apis: ['setInterval'] })
    let now = NOW
    const { api, scope, store } = fixture(t, [{ id: 'p', mtime: NOW - WEEK }],
        [{ id: 'ws', projectIds: ['p'] }], () => now)
    assert.deepEqual(ids(api.recentProjects.value), ['p'])
    assert.deepEqual(ids(api.recentWorkspaces.value), ['ws'])
    now += 60
    t.mock.timers.tick(60_000)
    assert.deepEqual(ids(api.recentProjects.value), [])
    assert.deepEqual(ids(api.recentWorkspaces.value), [])
    store.projects.p.mtime = now - WEEK
    assert.deepEqual(ids(api.recentProjects.value), ['p'])
    scope.stop()
    now += 60
    t.mock.timers.tick(60_000)
    assert.deepEqual(ids(api.recentProjects.value), ['p'])
    assert.deepEqual(ids(api.recentWorkspaces.value), ['ws'])
})

test('defaults to current Unix seconds and expires projects without an injected clock', t => {
    t.mock.timers.enable({ apis: ['Date', 'setInterval'], now: NOW * 1000 })
    const store = useProjects(createPinia())
    store.projects.p = { id: 'p', mtime: NOW - WEEK }
    const scope = effectScope()
    t.after(() => scope.stop())
    const api = scope.run(() => useRailRecentProjects(store, reactive({ getSelectableWorkspaces: [] }),
        reactive({ isShowArchivedProjects: false })))
    assert.deepEqual(ids(api.recentProjects.value), ['p'])
    t.mock.timers.tick(60_000)
    assert.deepEqual(ids(api.recentProjects.value), [])
})
