import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { createPinia, defineStore } from 'pinia'
import { getSessionCutoffMs } from '../utils/sessions.js'
import { jsonValuesEqual } from '../utils/jsonValuesEqual.js'

const source = readFileSync(new URL('./data.js', import.meta.url), 'utf8')
const action = name => {
    const start = source.indexOf(`        ${name}(`)
    return source.slice(start, source.indexOf('        /**', start))
}
const copy = value => JSON.parse(JSON.stringify(value))
const base = { id: 's', project_id: 'p', mtime: 10, title: 'Session', last_line: 3,
    last_started_at: null, last_stopped_at: null, parent_session_id: null,
    last_new_content_at: '2026-10-07T12:00:00Z',
    tasks: { items: [] }, goals: [{ objectives: ['Goal'] }], plan_paths: [{ path: 'docs/plan.md' }], layout: {} }

function fixture() {
    let result = copy(base), ok = true
    const response = async () => ({ ok, status: ok ? 200 : 500, json: async () => copy(result) })
    const deps = { getSessionCutoffMs, markAgentIdle: () => {}, jsonValuesEqual,
        apiFetch: response, fetch: response, sessionsLoadInFlight: new Map(),
        syncBaseline: { sessionMtime: (_, local) => local }, isWorkspaceProjectId: () => false }
    const names = ['updateSession', 'async loadSessions', 'async loadStickySessions', 'async loadSessionById',
        'async refreshSessionRecord', 'async renameSession']
    const actions = new Function(...Object.keys(deps), `return { ${names.map(action).join('')} }`)(...Object.values(deps))
    const useFixture = defineStore('session-rest-reconciliation', {
        state: () => ({ sessions: { s: copy(base) }, localState: { agentRunStates: {},
            sessions: { s: { itemsFetched: true } }, projects: { p: { sessionsFetched: true, hasMoreSessions: true } } } }),
        actions: { ...actions, _cleanStaleChildSynthetics() {}, _hydrateSessionLayoutFromPersisted() {},
            tryFinalizePendingBinding() {}, _tryLinkPeerDelivery() {},
            _ensureProjectLocalState(id) { return this.localState.projects[id] },
            _fetchSessionsPage: async () => copy(result),
        },
    })
    return { store: useFixture(createPinia()), respond(value, success = true) { result = value; ok = success } }
}

test('REST pages retain existing session references while reporting pre-update mtime changes', async () => {
    const { store, respond } = fixture(), before = store.sessions.s, goal = before.goals
    respond({ sessions: [{ ...copy(base), mtime: 20 }], has_more: false })
    const changed = await store.loadSessions('p', { force: true })
    assert.deepEqual([...changed], ['s'])
    assert.strictEqual(store.sessions.s, before)
    assert.strictEqual(store.sessions.s.goals, goal)
    assert.equal(store.sessions.s.mtime, 20)
    assert.equal(store.localState.projects.p.hasMoreSessions, false)
})

test('sticky sessions use the same reference and stale-timestamp guarantees as WebSocket updates', async () => {
    const { store, respond } = fixture(), before = store.sessions.s
    respond({ sessions: [{ ...copy(base), last_new_content_at: '2026-10-07T11:00:00Z' }] })
    await store.loadStickySessions()
    assert.strictEqual(store.sessions.s, before)
    assert.equal(before.last_new_content_at, '2026-10-07T12:00:00Z')
})

test('authoritative REST rows promote existing drafts without losing their stable references', async () => {
    for (const loader of ['page', 'sticky', 'refresh']) {
        const { store, respond } = fixture(), before = store.sessions.s
        before.draft = true
        respond(loader === 'refresh' ? copy(base) : { sessions: [copy(base)], has_more: false })
        if (loader === 'page') await store.loadSessions('p', { force: true })
        if (loader === 'sticky') await store.loadStickySessions()
        if (loader === 'refresh') await store.refreshSessionRecord('s')
        assert.equal(store.sessions.s.draft, false, loader)
        assert.strictEqual(store.sessions.s, before)
    }
})

test('a first REST load returns the stored session and subsequent refresh retains it', async () => {
    const { store } = fixture()
    delete store.sessions.s
    const loaded = await store.loadSessionById('s')
    assert.strictEqual(loaded, store.sessions.s)
    const goal = loaded.goals
    assert.equal(await store.refreshSessionRecord('s'), true)
    assert.strictEqual(store.sessions.s, loaded)
    assert.strictEqual(loaded.goals, goal)
})

test('rename responses keep unrelated references and failed renames restore the title', async () => {
    const { store, respond } = fixture(), before = store.sessions.s, goal = before.goals
    respond({ ...copy(base), title: 'Renamed' })
    await store.renameSession('p', 's', 'Renamed')
    assert.strictEqual(store.sessions.s, before)
    assert.strictEqual(before.goals, goal)
    assert.equal(before.title, 'Renamed')
    respond({ error: 'Failure' }, false)
    await assert.rejects(store.renameSession('p', 's', 'Failed'), /Failure/)
    assert.equal(store.sessions.s.title, 'Renamed')
})
