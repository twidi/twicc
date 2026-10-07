import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { createPinia, defineStore } from 'pinia'
import { computed, nextTick, watch } from 'vue'
import { getSessionCutoffMs } from '../utils/sessions.js'
import { jsonValuesEqual } from '../utils/jsonValuesEqual.js'

const source = readFileSync(new URL('./data.js', import.meta.url), 'utf8')
const start = source.indexOf('        updateSession(session)')
const end = source.indexOf('        /**', start)
const deps = { getSessionCutoffMs, markAgentIdle: () => {}, jsonValuesEqual }
const actions = new Function(...Object.keys(deps), `return { ${source.slice(start, end)} }`)(...Object.values(deps))
const copy = value => JSON.parse(JSON.stringify(value))
const session = {
    id: 'main', project_id: 'p', parent_session_id: null, provider: 'codex',
    last_started_at: '2026-10-07T12:00:00Z', last_stopped_at: null,
    last_new_content_at: '2026-10-07T12:00:00Z', compute_version_up_to_date: true,
    total_cost: 0, mtime: 100, last_line: 1,
    tasks: { items: [{ content: 'Task', status: 'pending' }] },
    goals: [{ objectives: ['Goal'], state: 'active' }],
    plan_paths: [{ path: 'docs/plan.md', exists: true, updated_at: '2026-10-07' }],
    layout: { assignment: { plan: 'left' }, collapsed: [], tabOrder: ['chat', 'plan'] },
    model: { name: 'Model', tags: ['reasoning'] }, annotations: { tags: ['label'], obsolete: true },
}
const useFixture = defineStore('session-snapshot-stability', {
    state: () => ({ sessions: { main: copy(session) }, localState: { agentRunStates: {}, sessions: {} },
        cleanups: 0, refreshes: 0, hydrations: 0 }),
    actions: { ...actions, _cleanStaleChildSynthetics() { this.cleanups++ },
        refreshSessionToolStates() { this.refreshes++; return Promise.resolve() },
        _hydrateSessionLayoutFromPersisted() { this.hydrations++ },
        tryFinalizePendingBinding() {}, _tryLinkPeerDelivery() {} },
})
const fixture = () => useFixture(createPinia())

test('cost and activity snapshots do not update unchanged goal plan and task consumers', async () => {
    const store = fixture(), before = store.sessions.main
    const refs = Object.fromEntries(['tasks', 'goals', 'plan_paths', 'layout', 'model', 'annotations']
        .map(key => [key, before[key]]))
    let updates = 0
    const stop = watch([
        computed(() => store.sessions.main.goals.at(-1)),
        computed(() => store.sessions.main.plan_paths),
        computed(() => store.sessions.main.tasks),
    ], () => updates++)
    try {
        for (let i = 1; i <= 100; i++) {
            store.updateSession({ ...copy(store.sessions.main), total_cost: i, mtime: 100 + i, last_line: i + 1 })
            await nextTick()
        }
        assert.equal(updates, 0)
        assert.strictEqual(store.sessions.main, before)
        for (const [key, value] of Object.entries(refs)) assert.strictEqual(store.sessions.main[key], value, key)
        assert.equal(store.sessions.main.total_cost, 100)
    } finally { stop() }
})

test('changed nested snapshots remove keys instead of retaining stale merged values', () => {
    const store = fixture()
    store.updateSession({ id: 'main', annotations: { tags: ['new'] }, layout: {}, model: null })
    assert.deepEqual(store.sessions.main.annotations, { tags: ['new'] })
    assert.deepEqual(store.sessions.main.layout, {})
    assert.equal(store.sessions.main.model, null)
})

test('omitted fields survive partial snapshots while explicit empty values apply', () => {
    const store = fixture(), goal = store.sessions.main.goals
    store.updateSession({ id: 'main', total_cost: 2 })
    assert.strictEqual(store.sessions.main.goals, goal)
    store.updateSession({ id: 'main', goals: [], plan_paths: [], annotations: null })
    assert.deepEqual(store.sessions.main.goals, [])
    assert.deepEqual(store.sessions.main.plan_paths, [])
    assert.equal(store.sessions.main.annotations, null)
    assert.equal(store.cleanups, 0)
})

test('goal dismissal plan reordering and nested array changes remain visible', async () => {
    const store = fixture()
    const update = copy(store.sessions.main)
    update.goals[0].dismissed = true
    update.plan_paths.push({ path: 'docs/new.md', exists: true })
    update.plan_paths.reverse()
    update.layout.collapsed.push('left')
    update.model.tags.push('updated')
    store.updateSession(update)
    await nextTick()
    assert.equal(store.sessions.main.goals[0].dismissed, true)
    assert.equal(store.sessions.main.plan_paths[0].path, 'docs/new.md')
    assert.deepEqual(store.sessions.main.layout.collapsed, ['left'])
    assert.deepEqual(store.sessions.main.model.tags, ['reasoning', 'updated'])
})

test('compute-ready transition refreshes loaded links once using pre-update state', () => {
    const store = fixture()
    store.sessions.main.compute_version_up_to_date = false
    store.localState.sessions.main = { itemsFetched: true }
    store.updateSession({ ...copy(store.sessions.main), compute_version_up_to_date: true })
    store.updateSession(copy(store.sessions.main))
    assert.equal(store.refreshes, 1)
})

test('stale snapshots cannot regress the optimistic unread timestamp', () => {
    const store = fixture()
    store.updateSession({ id: 'main', last_new_content_at: '2026-10-07T11:00:00Z' })
    assert.equal(store.sessions.main.last_new_content_at, '2026-10-07T12:00:00Z')
})
