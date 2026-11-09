import { mock, test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { ARCHIVE_TREE_LOADING_DELAY_MS, createArchiveFlow } from './archiveFlow.js'

// The flow logs each failure it reports to the user.
mock.method(console, 'error', () => {})

const flush = () => new Promise((resolve) => setImmediate(resolve))

function deferred() {
    let resolve, reject
    const promise = new Promise((res, rej) => { resolve = res; reject = rej })
    return { promise, resolve, reject }
}

/** Topology of target `t`, with children `a` (project `other`, pinned) and `b`. */
const familyTopology = () => ({
    tree: { id: 't', children: [{ id: 'a', children: [] }, { id: 'b', children: [] }] },
    nodes: [
        { id: 't', session: { id: 't', project_id: 'p' }, process: null, active_cron_count: 3 },
        { id: 'a', session: { id: 'a', project_id: 'other', pinned: 'project' }, process: null, active_cron_count: 1 },
        { id: 'b', session: { id: 'b', project_id: 'p', pinned: null }, process: null, active_cron_count: 0 },
    ],
})

const aloneTopology = () => ({
    tree: { id: 't', children: [] },
    nodes: [{ id: 't', session: { id: 't', project_id: 'p' }, process: null, active_cron_count: 0 }],
})

function harness({ sessions, ...overrides } = {}) {
    const known = sessions ?? { t: { id: 't', project_id: 'p', spawn_root: 'root' } }
    const calls = { dialogs: [], failures: [], today: [], hidden: [], archived: [], reads: [] }
    const deps = {
        getSession: (id) => known[id],
        readTopology: async (projectId, id) => {
            calls.reads.push([projectId, id])
            return familyTopology()
        },
        archiveHidden: async (projectId, id, archivedIds) => {
            calls.hidden.push([projectId, id, archivedIds])
            return { archived: [], failed: [] }
        },
        archiveOne: async (item) => {
            calls.archived.push(item)
        },
        runTodayPath: (id, options) => { calls.today.push([id, options]) },
        setDialog: (state) => { calls.dialogs.push(state) },
        reportFailures: (count) => { calls.failures.push(count) },
        loadingDelayMs: 10_000,
        ...overrides,
    }
    return { flow: createArchiveFlow(deps), calls, lastDialog: () => calls.dialogs.at(-1) }
}

async function ready(options) {
    const fixture = harness(options)
    await fixture.flow.request('t')
    await flush()
    return fixture
}

test('the loading delay is 250 ms', () => {
    assert.equal(ARCHIVE_TREE_LOADING_DELAY_MS, 250)
})

test('a session without spawn_root goes to the path of today with no read', async () => {
    const { flow, calls } = harness({ sessions: { t: { id: 't', project_id: 'p', spawn_root: null } } })
    await flow.request('t')
    assert.deepEqual(calls.reads, [])
    assert.equal(calls.today.length, 1)
    assert.equal(calls.today[0][0], 't')
    assert.equal(calls.today[0][1]?.onConfirmed, undefined)
    assert.deepEqual(calls.dialogs, [])
})

test('a session unknown to the store goes to the path of today with no read', async () => {
    const { flow, calls } = harness({ sessions: {} })
    await flow.request('t')
    assert.deepEqual(calls.reads, [])
    assert.equal(calls.today.length, 1)
})

test('empty rows go to the path of today, with no dialog', async () => {
    const { calls } = await ready({ readTopology: async () => aloneTopology() })
    assert.equal(calls.today.length, 1)
    assert.equal(calls.today[0][0], 't')
    assert.deepEqual(calls.dialogs, [])
})

test('with empty rows, the hidden call waits for the confirmation of today', async () => {
    const { calls } = await ready({ readTopology: async () => aloneTopology() })
    assert.deepEqual(calls.hidden, [])
    await calls.today[0][1].onConfirmed()
    assert.deepEqual(calls.hidden, [['p', 't', ['t']]])
    assert.deepEqual(calls.failures, [])
})

test('on the path of today, each failed entry of the hidden call counts one', async () => {
    const { calls } = await ready({
        readTopology: async () => aloneTopology(),
        archiveHidden: async () => ({ archived: [], failed: ['x', 'y'] }),
    })
    await calls.today[0][1].onConfirmed()
    assert.deepEqual(calls.failures, [2])
})

test('on the path of today, a failed hidden request counts one', async () => {
    const { calls } = await ready({
        readTopology: async () => aloneTopology(),
        archiveHidden: async () => { throw new Error('boom') },
    })
    await calls.today[0][1].onConfirmed()
    assert.deepEqual(calls.failures, [1])
})

test('rows present open the dialog in ready, with the cron counts of the target and the rows', async () => {
    const { calls, lastDialog } = await ready()
    assert.deepEqual(calls.today, [])
    assert.deepEqual(calls.reads, [['p', 't']])
    assert.equal(calls.dialogs.length, 1)
    const state = lastDialog()
    assert.equal(state.sessionId, 't')
    assert.equal(state.projectId, 'p')
    assert.equal(state.status, 'ready')
    assert.equal(state.targetSession.id, 't')
    assert.deepEqual(state.rows.map((row) => row.id), ['a', 'b'])
    assert.deepEqual(state.cronCounts, { t: 3, a: 1, b: 0 })
})

test('a failed read opens the dialog in error, with empty cron counts', async () => {
    const { lastDialog } = await ready({ readTopology: async () => { throw new Error('boom') } })
    assert.equal(lastDialog().status, 'error')
    assert.deepEqual(lastDialog().rows, [])
    assert.deepEqual(lastDialog().cronCounts, {})
})

test('a malformed topology opens the dialog in error', async () => {
    const { lastDialog } = await ready({ readTopology: async () => ({}) })
    assert.equal(lastDialog().status, 'error')
})

test('confirming the error dialog archives the target only', async () => {
    const { flow, calls } = await ready({ readTopology: async () => { throw new Error('boom') } })
    await flow.confirm({ checkedIds: [], targetOnly: true })
    assert.deepEqual(calls.hidden, [])
    assert.deepEqual(calls.archived.map((item) => item.id), ['t'])
})

test('the dialog shows loading only when the read lasts more than the delay', async (t) => {
    t.mock.timers.enable({ apis: ['setTimeout'] })
    const read = deferred()
    const { flow, calls, lastDialog } = harness({ readTopology: () => read.promise, loadingDelayMs: 250 })
    flow.request('t')
    t.mock.timers.tick(249)
    assert.deepEqual(calls.dialogs, [])
    t.mock.timers.tick(1)
    assert.equal(lastDialog().status, 'loading')
    assert.equal(lastDialog().targetSession.id, 't')
    read.resolve(familyTopology())
    await flush()
    assert.equal(lastDialog().status, 'ready')
    assert.equal(calls.dialogs.length, 2)
})

test('a read faster than the delay never shows loading', async (t) => {
    t.mock.timers.enable({ apis: ['setTimeout'] })
    const { flow, calls } = harness({ loadingDelayMs: 250 })
    await flow.request('t')
    await flush()
    t.mock.timers.tick(1000)
    assert.deepEqual(calls.dialogs.map((state) => state.status), ['ready'])
})

test('the loading dialog closes before the path of today', async (t) => {
    t.mock.timers.enable({ apis: ['setTimeout'] })
    const read = deferred()
    const { flow, calls, lastDialog } = harness({ readTopology: () => read.promise, loadingDelayMs: 250 })
    flow.request('t')
    t.mock.timers.tick(250)
    read.resolve(aloneTopology())
    await flush()
    assert.equal(lastDialog(), null)
    assert.equal(calls.today.length, 1)
})

test('a result that arrives after a cancel is ignored', async (t) => {
    t.mock.timers.enable({ apis: ['setTimeout'] })
    const read = deferred()
    const { flow, calls, lastDialog } = harness({ readTopology: () => read.promise, loadingDelayMs: 250 })
    flow.request('t')
    t.mock.timers.tick(250)
    flow.cancel()
    assert.equal(lastDialog(), null)
    const count = calls.dialogs.length
    read.resolve(aloneTopology())
    await flush()
    assert.equal(calls.dialogs.length, count)
    assert.deepEqual(calls.today, [])
})

test('a read failure that arrives after a cancel is ignored', async (t) => {
    t.mock.timers.enable({ apis: ['setTimeout'] })
    const read = deferred()
    const { flow, calls } = harness({ readTopology: () => read.promise, loadingDelayMs: 250 })
    flow.request('t')
    t.mock.timers.tick(250)
    flow.cancel()
    const count = calls.dialogs.length
    read.reject(new Error('boom'))
    await flush()
    assert.equal(calls.dialogs.length, count)
})

test('cancel closes the ready dialog and archives nothing', async () => {
    const { flow, calls, lastDialog } = await ready()
    flow.cancel()
    assert.equal(lastDialog(), null)
    assert.deepEqual(calls.archived, [])
    assert.deepEqual(calls.hidden, [])
})

test('after a cancel, the same session can be requested again', async () => {
    const { flow, calls } = await ready()
    flow.cancel()
    await flow.request('t')
    await flush()
    assert.equal(calls.reads.length, 2)
})

test('a second request for the same session is ignored while one runs', async () => {
    const read = deferred()
    const reads = []
    const { flow } = harness({ readTopology: (...args) => { reads.push(args); return read.promise } })
    flow.request('t')
    flow.request('t')
    assert.equal(reads.length, 1)
    read.resolve(familyTopology())
    await flush()
    flow.request('t')
    assert.equal(reads.length, 1)
})

test('a request for another session waits for the end of the current one', async () => {
    const sessions = {
        t: { id: 't', project_id: 'p', spawn_root: 'root' },
        u: { id: 'u', project_id: 'q', spawn_root: 'root' },
    }
    const { flow, calls } = await ready({ sessions })
    flow.request('u')
    flow.request('u')
    await flush()
    assert.deepEqual(calls.reads, [['p', 't']])
    flow.cancel()
    await flush()
    assert.deepEqual(calls.reads, [['p', 't'], ['q', 'u']])
})

test('a queued session already archived at its turn is skipped', async () => {
    const sessions = {
        t: { id: 't', project_id: 'p', spawn_root: 'root' },
        u: { id: 'u', project_id: 'q', spawn_root: 'root' },
    }
    const { flow, calls } = await ready({ sessions })
    flow.request('u')
    sessions.u.archived = true
    flow.cancel()
    await flush()
    assert.deepEqual(calls.reads, [['p', 't']])
    assert.deepEqual(calls.today, [])
})

test('after a skipped queued session, the next queued session starts', async () => {
    const sessions = {
        t: { id: 't', project_id: 'p', spawn_root: 'root' },
        u: { id: 'u', project_id: 'q', spawn_root: 'root' },
        v: { id: 'v', project_id: 'r', spawn_root: 'root' },
    }
    const { flow, calls } = await ready({ sessions })
    flow.request('u')
    flow.request('v')
    sessions.u.archived = true
    flow.cancel()
    await flush()
    assert.deepEqual(calls.reads, [['p', 't'], ['r', 'v']])
})

test('rows keep their reference: no state change after the opening rebuilds them', async () => {
    const { flow, calls, lastDialog } = await ready()
    const { rows } = lastDialog()
    await flow.request('t')
    await flush()
    assert.equal(calls.dialogs.length, 1)
    assert.equal(lastDialog().rows, rows)
})

test('targetOnly makes no hidden call and archives the target only', async () => {
    const { flow, calls } = await ready()
    await flow.confirm({ checkedIds: ['a'], targetOnly: true })
    assert.deepEqual(calls.hidden, [])
    assert.deepEqual(calls.archived, [{ id: 't', projectId: 'p' }])
})

test('no checked row makes no hidden call', async () => {
    const { flow, calls } = await ready()
    await flow.confirm({ checkedIds: [], targetOnly: false })
    assert.deepEqual(calls.hidden, [])
    assert.deepEqual(calls.archived.map((item) => item.id), ['t'])
})

test('the endpoint receives the target and the checked ids', async () => {
    const { flow, calls } = await ready()
    await flow.confirm({ checkedIds: ['a', 'b'], targetOnly: false })
    assert.deepEqual(calls.hidden, [['p', 't', ['t', 'a', 'b']]])
})

test('a slow hidden call does not delay the archives of the listed sessions', async () => {
    const hidden = deferred()
    const { flow, calls } = await ready({ archiveHidden: () => hidden.promise })
    const pending = flow.confirm({ checkedIds: ['a', 'b'], targetOnly: false })
    await flush()
    assert.deepEqual(calls.archived.map((item) => item.id), ['t', 'a', 'b'])
    assert.deepEqual(calls.failures, [])
    hidden.resolve({ archived: [], failed: [] })
    await pending
    assert.deepEqual(calls.failures, [])
})

test('the failures of a slow hidden call and of the archives are summed in one report', async () => {
    const hidden = deferred()
    const { flow, calls } = await ready({
        archiveHidden: () => hidden.promise,
        archiveOne: async ({ id }) => {
            if (id === 'a') throw new Error('boom')
        },
    })
    const pending = flow.confirm({ checkedIds: ['a', 'b'], targetOnly: false })
    await flush()
    assert.deepEqual(calls.failures, [])
    hidden.resolve({ archived: [], failed: ['h1', 'h2'] })
    await pending
    assert.deepEqual(calls.failures, [3])
})

test('a failed slow hidden request counts one, next to a failed archive', async () => {
    const hidden = deferred()
    const { flow, calls } = await ready({
        archiveHidden: () => hidden.promise,
        archiveOne: async ({ id }) => {
            if (id === 'a') throw new Error('boom')
        },
    })
    const pending = flow.confirm({ checkedIds: ['a'], targetOnly: false })
    await flush()
    hidden.reject(new Error('boom'))
    await pending
    assert.deepEqual(calls.failures, [2])
})

test('a checked row uses the project and the pin of its topology node', async () => {
    const { flow, calls } = await ready()
    await flow.confirm({ checkedIds: ['a', 'b'], targetOnly: false })
    assert.deepEqual(calls.archived, [
        { id: 't', projectId: 'p' },
        { id: 'a', projectId: 'other', pinned: 'project' },
        { id: 'b', projectId: 'p', pinned: null },
    ])
})

test('an unchecked row is not archived', async () => {
    const { flow, calls } = await ready()
    await flow.confirm({ checkedIds: ['b'], targetOnly: false })
    assert.deepEqual(calls.archived.map((item) => item.id), ['t', 'b'])
})

test('a failed hidden request still archives the listed sessions and reports one failure in total', async () => {
    const { flow, calls } = await ready({ archiveHidden: async () => { throw new Error('boom') } })
    await flow.confirm({ checkedIds: ['a'], targetOnly: false })
    assert.deepEqual(calls.archived.map((item) => item.id), ['t', 'a'])
    assert.deepEqual(calls.failures, [1])
})

test('a failed target-only archive reports one failure', async () => {
    const { flow, calls } = await ready({ archiveOne: async () => { throw new Error('boom') } })
    await flow.confirm({ checkedIds: [], targetOnly: true })
    assert.deepEqual(calls.failures, [1])
})

test('failures are summed in one report and do not stop the other sessions', async () => {
    const archived = []
    const { flow, calls } = await ready({
        archiveHidden: async () => ({ archived: [], failed: ['h1', 'h2'] }),
        archiveOne: async ({ id }) => {
            archived.push(id)
            if (id === 'a') throw new Error('boom')
        },
    })
    await flow.confirm({ checkedIds: ['a', 'b'], targetOnly: false })
    assert.deepEqual(archived, ['t', 'a', 'b'])
    assert.deepEqual(calls.failures, [3])
})

test('an archive that throws synchronously counts as one failure', async () => {
    const { flow, calls } = await ready({ archiveOne: () => { throw new Error('boom') } })
    await flow.confirm({ checkedIds: [], targetOnly: true })
    assert.deepEqual(calls.failures, [1])
})

test('a confirm without failure reports nothing', async () => {
    const { flow, calls } = await ready()
    await flow.confirm({ checkedIds: ['a'], targetOnly: false })
    assert.deepEqual(calls.failures, [])
})

test('confirm closes the dialog before its first await', async () => {
    const { flow, lastDialog } = await ready()
    const pending = flow.confirm({ checkedIds: ['a'], targetOnly: false })
    assert.equal(lastDialog(), null)
    await pending
})

test('confirm is ignored when no dialog state exists', async () => {
    const { flow, calls } = harness()
    await flow.confirm({ checkedIds: [], targetOnly: true })
    assert.deepEqual(calls.archived, [])
    assert.deepEqual(calls.dialogs, [])
})

test('a double confirm archives once', async () => {
    const { flow, calls } = await ready()
    await Promise.all([
        flow.confirm({ checkedIds: ['a'], targetOnly: false }),
        flow.confirm({ checkedIds: ['a'], targetOnly: false }),
    ])
    assert.equal(calls.hidden.length, 1)
    assert.deepEqual(calls.archived.map((item) => item.id), ['t', 'a'])
})

test('a cancel during the archives changes nothing', async () => {
    const hidden = deferred()
    const { flow, calls } = await ready({ archiveHidden: () => hidden.promise })
    const pending = flow.confirm({ checkedIds: ['a'], targetOnly: false })
    const count = calls.dialogs.length
    flow.cancel()
    assert.equal(calls.dialogs.length, count)
    hidden.resolve({ archived: [], failed: [] })
    await pending
    assert.deepEqual(calls.archived.map((item) => item.id), ['t', 'a'])
})

test('a second request is ignored until the archives of the confirm end', async () => {
    const hidden = deferred()
    const { flow, calls } = await ready({ archiveHidden: () => hidden.promise })
    const pending = flow.confirm({ checkedIds: ['a'], targetOnly: false })
    flow.request('t')
    assert.equal(calls.reads.length, 1)
    hidden.resolve({ archived: [], failed: [] })
    await pending
})

// `setSessionArchived` cannot load under plain `node --test`: run its source.
function storeAction({ session, autoUnpin = true, serverOk = true }) {
    const source = readFileSync(new URL('../stores/data.js', import.meta.url), 'utf8')
    const start = source.indexOf('        async setSessionArchived(')
    const end = source.indexOf('        /**', start)
    const patches = []
    const deps = {
        useSettingsStore: () => ({ isAutoUnpinOnArchive: autoUnpin }),
        apiFetch: async (_, { body }) => {
            patches.push(JSON.parse(body))
            return { ok: serverOk, json: async () => ({ error: 'nope' }) }
        },
        remoteUiError: (_, message) => new Error(message),
        i18n: { global: { t: (key) => key } },
    }
    const actions = new Function(...Object.keys(deps), `return { ${source.slice(start, end)} }`)(...Object.values(deps))
    const store = {
        ...actions,
        sessions: session ? { s: session } : {},
        updateSessionFromServer() {},
    }
    return { store, patches }
}

test('knownPinned decides the unpin of a session that is not in the store', async () => {
    const { store, patches } = storeAction({})
    await store.setSessionArchived('p', 's', true, { knownPinned: 'project' })
    assert.deepEqual(patches, [{ archived: true, pinned: null }])
})

test('a null knownPinned does not unpin', async () => {
    const { store, patches } = storeAction({})
    await store.setSessionArchived('p', 's', true, { knownPinned: null })
    assert.deepEqual(patches, [{ archived: true }])
})

test('the store record wins over knownPinned', async () => {
    const { store, patches } = storeAction({ session: { pinned: null, archived: false } })
    await store.setSessionArchived('p', 's', true, { knownPinned: 'project' })
    assert.deepEqual(patches, [{ archived: true }])
})

test('without knownPinned, a pinned store record still unpins', async () => {
    const session = { pinned: 'project', archived: false }
    const { store, patches } = storeAction({ session })
    await store.setSessionArchived('p', 's', true)
    assert.deepEqual(patches, [{ archived: true, pinned: null }])
    assert.equal(session.pinned, null)
})

test('knownPinned follows the auto-unpin setting', async () => {
    const { store, patches } = storeAction({ autoUnpin: false })
    await store.setSessionArchived('p', 's', true, { knownPinned: 'project' })
    assert.deepEqual(patches, [{ archived: true }])
})

test('the archive of a session that is not in the store rejects on a server error', async () => {
    const { store } = storeAction({ serverOk: false })
    await assert.rejects(store.setSessionArchived('p', 's', true, { knownPinned: 'project' }))
})
