import assert from 'node:assert/strict'
import { it } from 'node:test'
import * as storage from './draftStorage.js'
import { createAsyncQuestionActions } from './asyncQuestionState.js'

const batch = { item_id: 'q1', status: 'ready', questions: [{ index: 3, title: 'Original title', options: [] }] }
const saved = () => ({ choices: { q1: { 3: { kind: 'other', value: 'Old answer' } } }, sourceBatches: { q1: batch }, recoveredIds: [] })
const snapshot = (revision = 2, request_id = 'external') => ({ revision, batches: [], resolutions: { q1: { status: 'dismissed', request_id } }, widget_enabled: true })
const deferred = () => { let resolve, reject; const promise = new Promise((a, b) => { resolve = a; reject = b }); return { promise, resolve, reject } }

function harness(overrides = {}) {
    const writes = [], frames = []
    const deps = { saveMessage: async (...args) => writes.push(args), getAll: async () => ({}), getAllMessages: async () => ({}),
        recover: async (...args) => writes.push(args), fetch: async () => ({ ok: true, json: async () => snapshot() }),
        send: async frame => { frames.push(frame); return true }, uuid: () => 'local', pendingSends: () => ({}),
        cancelDraftSave: () => {}, ...overrides }
    const state = { sessions: { s: { provider: 'codex', project_id: 'p', type: 'session' } },
        localState: { asyncQuestionSnapshots: {}, asyncQuestionDrafts: {}, asyncQuestionNotices: {}, draftMessages: {}, draftAppendSignals: {} } }
    Object.assign(state, createAsyncQuestionActions(deps))
    return { state, writes, frames }
}

it('atomic recovery resolves on commit and rejects aborted writes', async () => {
    const staged = [], committed = {}
    let tx
    const db = { transaction(names, mode) {
        assert.deepEqual(names, ['draftMessages', 'asyncQuestionDrafts'])
        assert.equal(mode, 'readwrite')
        tx = { objectStore: name => ({ put: (value, key) => { staged.push([name, key, value]); return {} } }) }
        return tx
    } }
    let done = false
    const result = storage.saveAsyncQuestionRecovery('s', { message: 'Recovered' }, { ...saved(), choices: {}, recoveredIds: ['q1'] }, async () => db).then(() => { done = true })
    await Promise.resolve(); await Promise.resolve()
    assert.equal(done, false)
    assert.equal(staged.length, 2)
    assert.deepEqual(committed, {})
    staged.forEach(([name, key, value]) => { committed[name] = { [key]: value } })
    tx.oncomplete()
    await result
    assert.equal(done, true)
    assert.equal(committed.draftMessages.s.message, 'Recovered')
    assert.deepEqual(committed.asyncQuestionDrafts.s.choices, {})
    const aborted = storage.saveAsyncQuestionRecovery('s', {}, saved(), async () => db)
    await Promise.resolve()
    tx.error = new Error('Reload abort')
    tx.onabort()
    await assert.rejects(aborted, /Reload abort/)
})

it('question draft writes resolve only after transaction completion', async () => {
    let tx, request
    const db = { transaction(name) { assert.equal(name, 'asyncQuestionDrafts'); tx = { objectStore: () => ({ put: value => { request = {}; assert.deepEqual(value, saved()); return request } }) }; return tx } }
    let done = false
    const p = storage.saveAsyncQuestionDraft('s', saved(), async () => db).then(() => { done = true })
    await Promise.resolve(); await Promise.resolve()
    request.onsuccess?.()
    assert.equal(done, false)
    tx.oncomplete()
    await p
})

it('hydration preserves text and choices typed while storage waits', async () => {
    const wait = deferred()
    const { state } = harness({ getAll: () => wait.promise })
    const p = state.hydrateAsyncQuestionDrafts()
    state.localState.draftMessages.s = { message: 'Typed during hydrate' }
    state.setAsyncQuestionDraft('s', { ...saved(), choices: { q1: { 3: { kind: 'other', value: 'New answer' } } } })
    wait.resolve({ s: saved() })
    await p
    assert.equal(state.localState.asyncQuestionDrafts.s.choices.q1[3].value, 'New answer')
    assert.equal(state.localState.draftMessages.s.message, 'Typed during hydrate')
})

it('lower fetched revisions cannot overwrite newer WS or widget state', async () => {
    const wait = deferred()
    const { state } = harness({ fetch: () => wait.promise })
    const p = state.loadAsyncQuestions('p', 's')
    state.applyAsyncQuestionSnapshot('s', { revision: 8, batches: [batch], resolutions: {}, widget_enabled: false })
    state.setAsyncQuestionDraft('s', saved())
    state.localState.draftMessages.s = { message: 'Typed during fetch' }
    wait.resolve({ ok: true, json: async () => snapshot(2) })
    await p
    assert.equal(state.localState.asyncQuestionSnapshots.s.revision, 8)
    assert.equal(state.localState.asyncQuestionSnapshots.s.widget_enabled, false)
    assert.equal(state.localState.asyncQuestionDrafts.s.choices.q1[3].value, 'Old answer')
    assert.equal(state.localState.draftMessages.s.message, 'Typed during fetch')
})

it('disconnected resolutions recover once with retained source', async () => {
    const { state } = harness({ getAll: async () => ({ s: saved() }) })
    await state.hydrateAsyncQuestionDrafts()
    state.localState.draftMessages.s = { message: 'Existing text' }
    await state.loadAsyncQuestions('p', 's')
    assert.ok(state.localState.draftMessages.s.message.endsWith('Existing text'))
    assert.ok(state.localState.draftMessages.s.message.includes('Original title'))
    const once = state.localState.draftMessages.s.message
    await state.loadAsyncQuestions('p', 's')
    assert.equal(state.localState.draftMessages.s.message, once)
})

for (const first of ['broadcast', 'ack']) {
    for (const origin of ['local', 'external']) {
        it(`${origin} dismissal ${first} first preserves resolution identity`, async () => {
            const { state, frames, writes } = harness()
            await state.setAsyncQuestionDraft('s', saved())
            await state.dismissAsyncQuestion('p', 's', 'q1')
            assert.equal(writes[1][2].pendingDismissals.q1, 'local')
            assert.equal(frames[0].type, 'codex_dismiss_async_question')
            const update = snapshot(3, origin)
            const ack = { session_id: 's', item_id: 'q1', request_id: 'local', snapshot: update }
            if (first === 'broadcast') { await state.applyAsyncQuestionSnapshot('s', update); await state.handleAsyncQuestionDismissed(ack) }
            else { await state.handleAsyncQuestionDismissed(ack); await state.applyAsyncQuestionSnapshot('s', update) }
            assert.deepEqual(state.localState.asyncQuestionDrafts.s.choices, {})
            if (origin === 'local') assert.equal(state.localState.draftMessages.s?.message, undefined)
            else assert.ok(state.localState.draftMessages.s.message.includes('Old answer'))
            assert.deepEqual(state.localState.asyncQuestionDrafts.s.pendingDismissals, {})
        })
    }
}

it('dismiss error releases pending identity and preserves choices', async () => {
    const { state } = harness()
    await state.setAsyncQuestionDraft('s', saved())
    await state.dismissAsyncQuestion('p', 's', 'q1')
    assert.equal(await state.failAsyncQuestionDismissal('local'), true)
    assert.equal(state.localState.asyncQuestionDrafts.s.choices.q1[3].value, 'Old answer')
    assert.deepEqual(state.localState.asyncQuestionDrafts.s.pendingDismissals, {})
})

it('dismiss frame waits for durable identity and never sends after abort', async () => {
    const wait = deferred()
    const { state, frames } = harness({ recover: () => wait.promise })
    state.localState.asyncQuestionDrafts.s = saved()
    const p = state.dismissAsyncQuestion('p', 's', 'q1')
    assert.equal(frames.length, 0)
    wait.reject(new Error('disk failure'))
    await assert.rejects(p, /disk failure/)
    assert.equal(frames.length, 0)
    assert.equal(state.localState.asyncQuestionDrafts.s.choices.q1[3].value, 'Old answer')
})

it('main hydration does not overwrite typing or explicit clear while reads wait', async () => {
    const wait = deferred()
    const { state } = harness({ getAllMessages: () => wait.promise })
    const p = state.hydrateDraftMessages()
    state.localState.draftMessageEdits = { s: true, cleared: true }
    state.localState.draftMessages.s = { message: 'Typed text' }
    wait.resolve({ s: { message: 'Old' }, cleared: { message: 'Old cleared' }, untouched: { message: 'Keep' } })
    await p
    assert.equal(state.localState.draftMessages.s.message, 'Typed text')
    assert.equal(state.localState.draftMessages.cleared, undefined)
    assert.equal(state.localState.draftMessages.untouched.message, 'Keep')
})

// This fake models commit visibility and abort. It executes the production storage helper.
function delayedDatabase(initial = {}) {
    const disk = structuredClone(initial), transactions = []
    const db = { transaction(names) {
        const changes = []
        const tx = { objectStore: name => ({ put(value, key) { changes.push([name, key, structuredClone(value)]); return {} } }),
            commit() {
                for (const [name, key, value] of changes) { disk[name] ||= {}; disk[name][key] = value }
                tx.oncomplete()
            },
            abort() { tx.error = new Error('Reload abort'); tx.onabort() },
        }
        transactions.push(tx)
        return tx
    } }
    return { disk, db, transactions }
}
const nextTurn = () => new Promise(resolve => setImmediate(resolve))

for (const edit of ['append', 'remove']) {
    it(`atomic commit honors ${edit} edits and serializes interleaved main saves`, async () => {
        const { disk, db, transactions } = delayedDatabase({ draftMessages: { s: { message: 'Original' } }, asyncQuestionDrafts: { s: saved() } })
        const { state } = harness({ recover: (...args) => storage.saveAsyncQuestionRecovery(...args, async () => db) })
        state.localState.asyncQuestionDrafts.s = saved()
        state.localState.draftMessages.s = { message: 'Original' }
        const p = state.applyAsyncQuestionSnapshot('s', snapshot())
        await nextTurn()
        assert.equal(transactions.length, 1)
        const recovered = state.localState.draftMessages.s.message
        state.localState.draftMessages.s = { message: edit === 'append' ? `${recovered}\nTyped during commit` : 'User removes recovered section' }
        const normalSave = state.persistComposerDraft('s')
        transactions[0].commit()
        await nextTurn()
        assert.equal(transactions.length, 2)
        transactions[1].commit()
        await p
        await nextTurn()
        transactions[2].commit()
        await normalSave
        assert.equal(disk.draftMessages.s.message, state.localState.draftMessages.s.message)
        assert.deepEqual(disk.asyncQuestionDrafts.s.choices, {})
        assert.deepEqual(disk.asyncQuestionDrafts.s.recoveredIds, ['q1'])
        if (edit === 'append') assert.ok(disk.draftMessages.s.message.includes('Old answer'))
        const { state: reload } = harness({ getAll: async () => disk.asyncQuestionDrafts })
        reload.localState.draftMessages.s = disk.draftMessages.s
        await reload.hydrateAsyncQuestionDrafts()
        await reload.applyAsyncQuestionSnapshot('s', snapshot())
        assert.equal(reload.localState.draftMessages.s.message, disk.draftMessages.s.message)
    })
}

it('reload after recovery abort retains choices, source, and dismissal IDs without duplicated text', async () => {
    const source = { ...saved(), pendingDismissals: { q1: 'local' } }
    const { disk, db, transactions } = delayedDatabase({ draftMessages: { s: { message: 'Original' } }, asyncQuestionDrafts: { s: source } })
    const { state } = harness({ recover: (...args) => storage.saveAsyncQuestionRecovery(...args, async () => db) })
    state.localState.asyncQuestionDrafts.s = structuredClone(source)
    state.localState.draftMessages.s = { message: 'Original' }
    const p = state.applyAsyncQuestionSnapshot('s', snapshot())
    await nextTurn()
    transactions[0].abort()
    await assert.rejects(p, /Reload abort/)
    assert.deepEqual(disk.asyncQuestionDrafts.s, source)
    assert.equal(disk.draftMessages.s.message, 'Original')
    const { state: reload } = harness({ getAll: async () => disk.asyncQuestionDrafts })
    reload.localState.draftMessages.s = disk.draftMessages.s
    await reload.hydrateAsyncQuestionDrafts()
    await reload.applyAsyncQuestionSnapshot('s', snapshot())
    assert.equal(reload.localState.draftMessages.s.message.match(/Old answer/g).length, 1)
})

it('keeps uncertain send answers until native acceptance identity resolves them', async () => {
    const pending = { local: { async_questions: { batch_ids: ['q1'] }, status: 'uncertain' } }
    const { state } = harness({ pendingSends: () => pending })
    await state.setAsyncQuestionDraft('s', saved())
    await state.applyAsyncQuestionSnapshot('s', snapshot())
    assert.equal(state.localState.asyncQuestionDrafts.s.choices.q1[3].value, 'Old answer')
    assert.equal(state.localState.draftMessages.s, undefined)
    await state.applyAsyncQuestionSnapshot('s', { ...snapshot(4, 'local'), resolutions: { q1: { status: 'sent', request_id: 'local' } } })
    assert.deepEqual(state.localState.asyncQuestionDrafts.s.choices, {})
    assert.equal(state.localState.draftMessages.s, undefined)
})

it('new batches and source content survive local draft updates without server source', async () => {
    const { state } = harness()
    await state.setAsyncQuestionDraft('s', saved())
    await state.setAsyncQuestionDraft('s', { choices: { q1: { 3: { kind: 'other', value: 'New answer' } } } })
    assert.equal(state.localState.asyncQuestionDrafts.s.sourceBatches.q1.questions[0].title, 'Original title')
    await state.applyAsyncQuestionSnapshot('s', { ...snapshot(), batches: [{ ...batch, item_id: 'q2' }] })
    assert.equal(state.localState.asyncQuestionSnapshots.s.batches[0].item_id, 'q2')
    assert.ok(state.localState.draftMessages.s.message.includes('New answer'))
})

it('hydration merges untouched saved answers with different answers typed during storage wait', async () => {
    const wait = deferred()
    const { state } = harness({ getAll: () => wait.promise })
    const p = state.hydrateAsyncQuestionDrafts()
    await state.setAsyncQuestionDraft('s', { choices: { q1: { 4: { kind: 'other', value: 'Typed new answer' } } } })
    wait.resolve({ s: saved() })
    await p
    assert.equal(state.localState.asyncQuestionDrafts.s.choices.q1[3].value, 'Old answer')
    assert.equal(state.localState.asyncQuestionDrafts.s.choices.q1[4].value, 'Typed new answer')
    assert.equal(state.localState.asyncQuestionDrafts.s.sourceBatches.q1.questions[0].title, 'Original title')
})

it('initializes snapshot without a session-list entry or transcript range', async () => {
    const urls = []
    const { state } = harness({ fetch: async url => { urls.push(url); return { ok: true, json: async () => ({ ...snapshot(), batches: [batch], resolutions: {} }) } } })
    state.sessions = {}
    await state.loadAsyncQuestions('p', 's')
    assert.deepEqual(urls, ['/api/projects/p/sessions/s/async-questions/'])
    assert.equal(state.localState.asyncQuestionSnapshots.s.batches[0].item_id, 'q1')
})

it('refreshes loaded root Codex snapshots on reconnect independently of transcript fetching', async () => {
    const urls = []
    const { state } = harness({ fetch: async url => { urls.push(url); return { ok: true, json: async () => snapshot() } } })
    state.localState.sessions = { s: { itemsFetched: false }, claude: {}, child: {} }
    state.sessions.claude = { provider: 'claude', project_id: 'p' }
    state.sessions.child = { provider: 'codex', project_id: 'p', type: 'subagent' }
    await state.refreshActiveAsyncQuestions()
    assert.deepEqual(urls, ['/api/projects/p/sessions/s/async-questions/'])
})

it('reads and deletes question draft records at transaction completion', async () => {
    let tx, cursorRequest, deleted
    const db = { transaction(name, mode) {
        assert.equal(name, 'asyncQuestionDrafts')
        tx = { objectStore: () => ({ openCursor: () => { cursorRequest = {}; return cursorRequest }, delete: key => { deleted = key; return {} } }) }
        return tx
    } }
    let complete = false
    const p = storage.getAllAsyncQuestionDrafts(async () => db).then(result => { complete = true; return result })
    await Promise.resolve()
    cursorRequest.result = { key: 's', value: saved(), continue() { cursorRequest.result = null; cursorRequest.onsuccess() } }
    cursorRequest.onsuccess()
    assert.equal(complete, false)
    tx.oncomplete()
    assert.deepEqual(await p, { s: saved() })
    const removed = storage.deleteAsyncQuestionDraft('s', async () => db)
    await Promise.resolve()
    assert.equal(deleted, 's')
    tx.oncomplete()
    await removed
})

it('upgrades IndexedDB v8 to v9 without deleting existing stores', async () => {
    const previous = globalThis.indexedDB
    const existing = new Set(['ephemeralControls', 'draftMessages', 'draftSessions', 'draftMedias', 'codeComments', 'inflightSends', 'pendingRequestDrafts'])
    const created = []
    const db = { objectStoreNames: { contains: name => existing.has(name) },
        createObjectStore(name) { created.push(name); return { createIndex() {} } },
        deleteObjectStore() { assert.fail('Existing records must survive the upgrade') },
    }
    globalThis.indexedDB = { open(name, version) {
        assert.equal(name, 'twicc'); assert.equal(version, 9)
        const request = { result: db }
        queueMicrotask(() => { request.onupgradeneeded({ oldVersion: 8, target: request }); request.onsuccess() })
        return request
    } }
    try {
        assert.equal(await storage.getDb(), db)
        assert.deepEqual(created, ['asyncQuestionDrafts'])
    } finally { globalThis.indexedDB = previous }
})

it('hydrated local dismissal identity suppresses external recovery after reload', async () => {
    const { state } = harness({ getAll: async () => ({ s: { ...saved(), pendingDismissals: { q1: 'local' } } }) })
    await state.hydrateAsyncQuestionDrafts()
    await state.applyAsyncQuestionSnapshot('s', snapshot(4, 'local'))
    assert.deepEqual(state.localState.asyncQuestionDrafts.s.choices, {})
    assert.equal(state.localState.draftMessages.s, undefined)
    assert.deepEqual(state.localState.asyncQuestionDrafts.s.pendingDismissals, {})
})
