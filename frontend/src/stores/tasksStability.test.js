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
assert.ok(start >= 0 && end > start)
const deps = { getSessionCutoffMs, markAgentIdle: () => {}, jsonValuesEqual }
const actions = new Function(...Object.keys(deps), `return { ${source.slice(start, end)} }`)(...Object.values(deps))
const tasks = { provider: 'claude_code', source: 'TodoWrite', line: 8, updated_at: '2026-10-07T12:00:00Z',
    explanation: null, items: [{ status: 'in_progress', content: 'First', activeForm: 'Doing first' }, { status: 'pending', content: 'Second' }] }
const copy = value => JSON.parse(JSON.stringify(value))
const useTasks = defineStore('tasks-stability-test', {
    state: () => ({ sessions: { main: { id: 'main', tasks: copy(tasks), total_cost: 0 } },
        localState: { agentRunStates: {}, sessions: {} } }),
    actions: { ...actions, _cleanStaleChildSynthetics() {}, _hydrateSessionLayoutFromPersisted() {},
        tryFinalizePendingBinding() {}, _tryLinkPeerDelivery() {} },
})
const fixture = () => useTasks(createPinia())

test('equivalent full snapshots preserve task references and subscribers', async () => {
    const store = fixture(), before = store.sessions.main.tasks, beforeItems = before.items
    const progress = computed(() => store.sessions.main.tasks.items)
    let updates = 0
    const stop = watch(progress, () => updates++)
    try {
        store.updateSession({ ...copy(store.sessions.main), total_cost: 0.85 })
        await nextTick()
        assert.strictEqual(store.sessions.main.tasks, before)
        assert.strictEqual(store.sessions.main.tasks.items, beforeItems)
        assert.equal(updates, 0)
        assert.equal(store.sessions.main.total_cost, 0.85)
    } finally { stop() }
})

test('task content status order explanation and metadata changes remain visible', () => {
    for (const change of [
        t => { t.items[0].content = 'Changed' },
        t => { t.items[0].status = 'completed' },
        t => { t.items.reverse() },
        t => { t.explanation = 'New explanation' },
        t => { t.updated_at = '2026-10-07T13:00:00Z' },
        t => { t.source = 'turn/plan/updated'; t.line = null; t.turn_id = 'turn-2' },
    ]) {
        const store = fixture(), next = copy(tasks)
        change(next)
        store.updateSession({ id: 'main', tasks: next })
        assert.deepEqual(store.sessions.main.tasks, next)
    }
})

test('an empty task snapshot removes all previous task fields', () => {
    const store = fixture()
    store.updateSession({ id: 'main', tasks: {} })
    assert.deepEqual(store.sessions.main.tasks, {})
})

test('omitted tasks preserve the existing task snapshot', () => {
    const store = fixture(), before = store.sessions.main.tasks
    store.updateSession({ id: 'main', total_cost: 0.85 })
    assert.strictEqual(store.sessions.main.tasks, before)
})

test('object key order does not replace an equivalent task snapshot', () => {
    const store = fixture(), before = store.sessions.main.tasks
    store.updateSession({ id: 'main', tasks: Object.fromEntries(Object.entries(copy(tasks)).reverse()) })
    assert.strictEqual(store.sessions.main.tasks, before)
})
