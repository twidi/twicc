import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { createPinia, defineStore } from 'pinia'
import { computed, toRaw } from 'vue'
import { buildProjectActivityIndex, createProjectActivityComparator } from '../utils/projectActivity.js'

// Execute the real project getters with Pinia. The full store has browser-only
// dependencies and extensionless imports that Node cannot load.
const source = readFileSync(new URL('./data.js', import.meta.url), 'utf8')
const start = source.indexOf('        // Data getters')
const end = source.indexOf('        // Session scope of a project:', start)
assert.ok(start >= 0 && end > start)
const getters = new Function(
    'buildProjectActivityIndex', 'createProjectActivityComparator',
    `return { ${source.slice(start, end)} }`,
)(buildProjectActivityIndex, createProjectActivityComparator)
const useProjectStore = defineStore('project-activity-test', {
    state: () => ({ projects: {} }),
    getters,
})
const makeStore = projects => {
    const store = useProjectStore(createPinia())
    store.projects = Object.fromEntries(projects.map(project => [project.id, project]))
    return store
}
const ids = projects => projects.map(project => project.id)

// Ignoring worktree activity must fail this test, including archived children.
test('recent cached worktree lifts its older parent in global ordering', () => {
    const store = makeStore([
        { id: 'parent', mtime: 10 },
        { id: 'other', mtime: 20 },
        { id: 'child', mtime: 30, worktree_of: 'parent', archived: true },
    ])
    assert.deepEqual(ids(store.getListableProjects), ['parent', 'other'])
    assert.deepEqual(ids(store.getProjects), ['child', 'parent', 'other'])
})

test('parent own activity wins over older worktrees and unrelated worktrees', () => {
    const store = makeStore([
        { id: 'parent', mtime: 50 },
        { id: 'other', mtime: 20 },
        { id: 'child', mtime: 30, worktree_of: 'parent' },
        { id: 'other-child', mtime: 40, worktree_of: 'other' },
    ])
    assert.equal(store.getProjectActivity('parent'), 50)
    assert.equal(store.getProjectActivity('other'), 40)
    assert.equal(store.getProjectActivity('child'), 30)
    assert.deepEqual(ids(store.getListableProjects), ['parent', 'other'])
})

test('sorting preserves raw project objects and timestamps', () => {
    const parent = Object.freeze({ id: 'parent', mtime: 10 })
    const child = Object.freeze({ id: 'child', mtime: 30, worktree_of: 'parent' })
    const projects = Object.freeze([parent, child])
    const store = makeStore(projects)
    assert.equal(store.getProjectActivity('parent'), 30)
    assert.equal(toRaw(store.getListableProjects[0]), parent)
    assert.equal(toRaw(store.getWorktreesOf('parent')[0]), child)
    assert.deepEqual(ids(store.getProjects), ['child', 'parent'])
    assert.equal(parent.mtime, 10)
    assert.equal(child.mtime, 30)
    assert.deepEqual(projects, [parent, child])
})

test('missing parents and missing activity have deterministic ID ties', () => {
    const store = makeStore([
        { id: 'z', mtime: null },
        { id: 'b', mtime: 10 },
        { id: 'a', mtime: 10 },
        { id: 'orphan', mtime: 20, worktree_of: 'missing' },
        { id: 'c' },
        { id: 'invalid', mtime: NaN },
    ])
    assert.deepEqual(ids(store.getListableProjects), ['a', 'b', 'c', 'invalid', 'z'])
    assert.deepEqual(ids(store.getProjects), ['orphan', 'a', 'b', 'c', 'invalid', 'z'])
    assert.equal(store.getProjectActivity('missing'), 0)
    assert.deepEqual(store.getWorktreesOf('absent'), [])
})

test('worktree rows retain their own activity and recent-first child ordering', () => {
    const store = makeStore([
        { id: 'parent', mtime: 10 },
        { id: 'older', mtime: 20, worktree_of: 'parent' },
        { id: 'newer', mtime: 40, worktree_of: 'parent' },
        { id: 'nested', mtime: 60, worktree_of: 'older' },
    ])
    assert.equal(store.getProjectActivity('older'), 20)
    assert.equal(store.getProjectActivity('parent'), 40)
    assert.deepEqual(ids(store.getWorktreesOf('parent')), ['newer', 'older'])
})

test('reactive child updates recalculate parent activity and global order', () => {
    const store = makeStore([
        { id: 'parent', mtime: 10 },
        { id: 'other', mtime: 20 },
        { id: 'child', mtime: 15, worktree_of: 'parent' },
    ])
    const order = computed(() => ids(store.getListableProjects))
    const activity = computed(() => store.getProjectActivity('parent'))
    assert.deepEqual(order.value, ['other', 'parent'])
    assert.equal(activity.value, 15)
    store.projects.child.mtime = 30
    assert.equal(activity.value, 30)
    assert.deepEqual(order.value, ['parent', 'other'])
    assert.equal(store.projects.parent.mtime, 10)
    store.projects.child.mtime = 5
    assert.equal(activity.value, 10)
    assert.deepEqual(order.value, ['other', 'parent'])
})
