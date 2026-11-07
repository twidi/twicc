import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { createPinia, defineStore } from 'pinia'
import { getSessionCutoffMs } from '../utils/sessions.js'
import { jsonValuesEqual } from '../utils/jsonValuesEqual.js'
import { remoteUiError } from '../i18n/remoteMessages.js'
import { applyUnavailableRefusal, captureRefusalTarget, noteUnavailableReasonUpdate } from '../utils/sessionAvailability.js'

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

function fixture({ projectState = { sessionsFetched: true, hasMoreSessions: true }, fetchPage = null } = {}) {
    let result = copy(base), ok = true
    const response = async () => ({ ok, status: ok ? 200 : 500, json: async () => copy(result) })
    const deps = { getSessionCutoffMs, markAgentIdle: () => {}, jsonValuesEqual, noteUnavailableReasonUpdate, remoteUiError,
        apiFetch: response, fetch: response, sessionsLoadInFlight: new Map(),
        syncBaseline: { sessionMtime: (_, local) => local }, isWorkspaceProjectId: () => false,
        FIRST_SESSIONS_PAGE_SIZE: 30 }
    const names = ['updateSession', 'async loadSessions', 'async loadStickySessions', 'async loadSessionById',
        'async refreshSessionRecord', 'async renameSession']
    const actions = new Function(...Object.keys(deps), `return { ${names.map(action).join('')} }`)(...Object.values(deps))
    const useFixture = defineStore('session-rest-reconciliation', {
        state: () => ({ sessions: { s: copy(base) }, localState: { agentRunStates: {},
            sessions: { s: { itemsFetched: true } }, projects: { p: copy(projectState) } } }),
        actions: { ...actions, _cleanStaleChildSynthetics() {}, _hydrateSessionLayoutFromPersisted() {},
            tryFinalizePendingBinding() {}, _tryLinkPeerDelivery() {},
            _ensureProjectLocalState(id) { return this.localState.projects[id] },
            _fetchSessionsPage: fetchPage ?? (async () => copy(result)),
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

// A scope never fetched yet: the store loads a small page first, then the rest.
const coldState = { sessionsFetched: false, sessionsLoading: false, sessionsLoadingError: false,
    hasMoreSessions: true, oldestSessionMtime: null }
const row = (id, mtime) => ({ ...copy(base), id, mtime })

function pagedFixture(pages, projectState = coldState) {
    const calls = []
    const { store } = fixture({ projectState, fetchPage: async (projectId, options) => {
        calls.push({ projectId, ...options })
        const page = pages.shift()
        if (page instanceof Error) throw page
        return typeof page === 'function' ? page() : copy(page)
    } })
    return { store, calls, state: store.localState.projects.p }
}

test('a first load fetches a small page, then the rest of a normal page after its cursor', async () => {
    const { store, calls, state } = pagedFixture([
        { sessions: [row('s', 20), row('b', 15)], has_more: true },
        { sessions: [row('c', 12), row('d', 11)], has_more: true },
    ])
    const changed = await store.loadSessions('p', { isInitialLoading: true })
    assert.deepEqual(calls, [
        { projectId: 'p', beforeMtime: null, limit: 30 },
        { projectId: 'p', beforeMtime: 15 },
    ])
    assert.deepEqual(Object.keys(store.sessions).sort(), ['b', 'c', 'd', 's'])
    assert.deepEqual([...changed], ['s'])
    assert.equal(state.sessionsFetched, true)
    assert.equal(state.hasMoreSessions, true)
    assert.equal(state.oldestSessionMtime, 11)
    assert.equal(state.sessionsLoading, false)
})

test('the cursor stays unpublished while the second request runs', async () => {
    let release
    const second = new Promise(resolve => { release = resolve })
    const { store, state } = pagedFixture([
        { sessions: [row('a', 20)], has_more: true },
        () => second,
    ])
    const loading = store.loadSessions('p')
    await new Promise(resolve => setTimeout(resolve, 0))
    assert.ok(store.sessions.a, 'the small page is merged before the second request settles')
    assert.equal(state.sessionsFetched, true)
    assert.equal(state.sessionsLoading, true)
    assert.equal(state.oldestSessionMtime, null)
    assert.equal(state.hasMoreSessions, true)
    release({ sessions: [row('b', 10)], has_more: false })
    await loading
    assert.equal(state.oldestSessionMtime, 10)
    assert.equal(state.hasMoreSessions, false)
})

test('a small first page that is complete makes a single request', async () => {
    const { store, calls, state } = pagedFixture([{ sessions: [row('a', 20)], has_more: false }])
    await store.loadSessions('p')
    assert.equal(calls.length, 1)
    assert.equal(state.hasMoreSessions, false)
    assert.equal(state.oldestSessionMtime, 20)
})

test('a fetched scope keeps a single request of the default size, forced or not', async () => {
    for (const force of [false, true]) {
        const { store, calls, state } = pagedFixture(
            [{ sessions: [row('a', 5)], has_more: true }],
            { ...coldState, sessionsFetched: true, oldestSessionMtime: 8 },
        )
        await store.loadSessions('p', { force })
        assert.deepEqual(calls, [{ projectId: 'p', beforeMtime: force ? null : 8, limit: null }], `force=${force}`)
        assert.equal(state.oldestSessionMtime, 5)
    }
})

test('a failed second request keeps the small page and resumes after it', async () => {
    const { store, calls, state } = pagedFixture([
        { sessions: [row('a', 20), row('b', 15)], has_more: true },
        new Error('network'),
        { sessions: [row('c', 10)], has_more: false },
    ])
    const originalError = console.error
    console.error = () => {}
    try {
        await assert.rejects(store.loadSessions('p', { isInitialLoading: true }), /network/)
    } finally {
        console.error = originalError
    }
    assert.ok(store.sessions.a && store.sessions.b)
    assert.equal(state.sessionsLoadingError, false)
    assert.equal(state.sessionsFetched, true)
    assert.equal(state.hasMoreSessions, true)
    assert.equal(state.oldestSessionMtime, 15)
    await store.loadSessions('p')
    assert.deepEqual(calls.at(-1), { projectId: 'p', beforeMtime: 15, limit: null })
    assert.equal(state.oldestSessionMtime, 10)
    assert.equal(state.hasMoreSessions, false)
})

test('a failed small first page is a failed initial load', async () => {
    const { store, state } = pagedFixture([new Error('network')])
    const originalError = console.error
    console.error = () => {}
    try {
        await assert.rejects(store.loadSessions('p', { isInitialLoading: true }), /network/)
    } finally {
        console.error = originalError
    }
    assert.equal(state.sessionsLoadingError, true)
    assert.equal(state.sessionsFetched, false)
})

test('a server row that carries the reason supersedes a refusal read in flight; a partial local update does not', async () => {
    const { store } = fixture()
    const getSession = id => store.sessions[id]
    const refused = () => new Response(JSON.stringify({ code: 'session_unavailable', unavailable_reason: 'rollout_missing' }),
        { status: 409 })
    const local = captureRefusalTarget(getSession, 's')
    store.updateSession({ id: 's', title: 'Renamed' })
    assert.equal(await applyUnavailableRefusal(refused(), local), 'rollout_missing')
    store.sessions.s.unavailable_reason = null
    const server = captureRefusalTarget(getSession, 's')
    store.updateSession({ ...copy(base), unavailable_reason: null })
    assert.equal(await applyUnavailableRefusal(refused(), server), null)
    assert.equal(store.sessions.s.unavailable_reason, null)
})
