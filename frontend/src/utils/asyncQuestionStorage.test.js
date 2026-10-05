import assert from 'node:assert/strict'
import { it } from 'node:test'
import { readFileSync } from 'node:fs'
import * as storage from './draftStorage.js'
import { createAsyncQuestionActions } from './asyncQuestionState.js'
import { createSendFailureActions } from './ephemeralSessions.js'
import { retainsAsyncQuestionSend } from './asyncQuestions.js'

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

function acknowledgementHarness(recover) {
    const disk = { questions: { s: saved() }, inflight: { local: {
        sessionId: 's', text: 'Already delivered', async_questions: { batch_ids: ['q1'] }, sentAt: 1,
    } } }
    const pending = new Map(Object.entries(structuredClone(disk.inflight)))
    const { state } = harness({ recover: async (...args) => {
        await recover(...args)
        disk.questions[args[0]] = structuredClone(args[2])
    }, pendingSends: () => Object.fromEntries(pending) })
    state.localState.asyncQuestionDrafts.s = saved()
    state.localState.failedSends = {}
    Object.assign(state, createSendFailureActions(pending, {
        deleteInflight: async requestId => { delete disk.inflight[requestId] },
    }))
    // These are existing store boundaries. The acknowledgement orchestration is the production action.
    state.removeFailedSend = () => {}
    state.recomputeVisualItems = () => {}
    state.isEphemeralDiscarded = () => false
    state._applySendFailure = () => assert.fail('An accepted send must not become a provider failure')
    return { state, disk, pending }
}

it('ack keeps durable original send identity until delayed acceptance commits', async () => {
    const wait = deferred()
    const { state, disk, pending } = acknowledgementHarness(() => wait.promise)
    const ack = state.acknowledgeInflightSend('s', 'local')
    await nextTurn()
    assert.ok(disk.inflight.local, 'Reload must retain the original request until acceptance commits')
    assert.ok(pending.has('local'))
    const { state: reload } = harness({ getAll: async () => disk.questions, pendingSends: () => disk.inflight })
    await reload.hydrateAsyncQuestionDrafts()
    await reload.applyAsyncQuestionSnapshot('s', { ...snapshot(4), resolutions: { q1: { status: 'sent', request_id: 'local' } } })
    assert.equal(reload.localState.draftMessages.s, undefined)
    wait.resolve()
    await ack
    assert.ok(disk.questions.s.acceptedSendIds.includes('local'))
    assert.equal(disk.inflight.local, undefined)
})

it('failed acceptance persistence retains delivered identity and a repeated ack retries safely', async () => {
    let attempts = 0
    const { state, disk, pending } = acknowledgementHarness(async () => {
        if (++attempts === 1) throw new Error('Acceptance transaction aborted')
    })
    await assert.rejects(state.acknowledgeInflightSend('s', 'local'), /Acceptance transaction aborted/)
    assert.ok(disk.inflight.local)
    assert.equal(pending.get('local').status, 'accepted')
    assert.equal(state.failInflightSend('local', { code: 'delivery_unconfirmed' }), true)
    const { state: reload } = harness({ getAll: async () => disk.questions, pendingSends: () => disk.inflight })
    await reload.hydrateAsyncQuestionDrafts()
    await reload.applyAsyncQuestionSnapshot('s', { ...snapshot(4), resolutions: { q1: { status: 'sent', request_id: 'local' } } })
    assert.equal(reload.localState.draftMessages.s, undefined)
    await state.acknowledgeInflightSend('s', 'local')
    await state.acknowledgeInflightSend('s', 'local')
    assert.equal(disk.inflight.local, undefined)
    assert.deepEqual(disk.questions.s.acceptedSendIds, ['local'])
})

// Execute actual store actions. Node cannot load the store's extensionless browser imports.
const storeSource = readFileSync(new URL('../stores/data.js', import.meta.url), 'utf8')
function actualStoreActions(dependencies) {
    const names = ['registerInflightSend', 'resolveInflightSends', 'hydrateInflightSends', 'auditInflightSends', 'removeFailedSend']
    const methods = names.map(name => {
        const start = storeSource.indexOf(`        ${name}(`) >= 0
            ? storeSource.indexOf(`        ${name}(`) : storeSource.indexOf(`        async ${name}(`)
        assert.ok(start >= 0, `Missing production action ${name}`)
        const end = storeSource.indexOf('        /**', start)
        assert.ok(end > start, `Missing production action boundary ${name}`)
        return storeSource.slice(start, end)
    })
    return new Function(...Object.keys(dependencies), `return {${methods.join('\n')}}`)(...Object.values(dependencies))
}

function attachActualStoreActions(state, pending, disk) {
    const actions = actualStoreActions({ inflightSends: pending, retainsAsyncQuestionSend,
        INFLIGHT_SEND_TTL_MS: 10, INFLIGHT_AUDIT_MIN_AGE_MS: 0, INFLIGHT_AUDIT_FETCH_CAP: 10,
        PROCESS_STATE: { ASSISTANT_TURN: 'assistant_turn', STARTING: 'starting' },
        getProviderHelpers: () => ({}), getParsedContent: item => item.parsed,
        userMessageMatchKey: (provider, parsed) => parsed.text, inflightSendMatchKey: entry => entry.text,
        hasContent: () => true,
        saveInflightSend: async (id, entry) => { disk.inflight[id] = structuredClone(entry) },
        deleteInflightSend: async id => { delete disk.inflight[id] },
        getAllInflightSends: async () => structuredClone(disk.inflight),
    })
    Object.assign(state, actions)
    state.localState.sessions = { s: { itemsFetched: true } }
    state.localState.draftAliases = {}
    state.sessionItems = { s: [{ kind: 'user_message', parsed: { text: 'Already delivered' } }] }
    state.processStates = {}
    state.getSession = id => state.sessions[id]
    state.auditAllLoadedInflightSends = () => state.auditInflightSends('s')
}

it('production native-source, expiry, audit, and late-failure paths retain identity during delayed ack', async () => {
    const wait = deferred()
    const { state, disk, pending } = acknowledgementHarness(() => wait.promise)
    attachActualStoreActions(state, pending, disk)
    const ack = state.acknowledgeInflightSend('s', 'local')
    await nextTurn()
    state.resolveInflightSends('s', state.sessionItems.s)
    state.registerInflightSend('different', { sessionId: 'other', text: 'Other send' })
    await state.auditInflightSends('s')
    assert.equal(state.failPendingSendsForSession('s', { code: 'delivery_unconfirmed' }), false)
    assert.equal(state.failInflightSend('local', { code: 'send_failed' }), true)
    assert.ok(pending.has('local'))
    assert.ok(disk.inflight.local)
    wait.resolve()
    await ack
    assert.ok(disk.questions.s.acceptedSendIds.includes('local'))
    assert.equal(disk.inflight.local, undefined)
})

it('reload retains original identity through production expiry and audit until native resolution', async () => {
    const { state, disk } = acknowledgementHarness(async () => { throw new Error('Storage unavailable') })
    await assert.rejects(state.acknowledgeInflightSend('s', 'local'), /Storage unavailable/)
    const pending = new Map()
    const { state: reload } = harness({ getAll: async () => disk.questions,
        pendingSends: () => Object.fromEntries(pending),
        recover: async (id, draft, record) => { disk.questions[id] = structuredClone(record) },
    })
    Object.assign(reload, createSendFailureActions(pending, { deleteInflight: async id => { delete disk.inflight[id] } }))
    reload.localState.failedSends = {}
    reload.recomputeVisualItems = () => {}
    reload.isEphemeralDiscarded = () => false
    reload._applySendFailure = () => assert.fail('A retained delivered send must not become a provider failure')
    attachActualStoreActions(reload, pending, disk)
    await reload.hydrateInflightSends()
    await reload.auditInflightSends('s')
    assert.equal(reload.failPendingSendsForSession('s', { code: 'delivery_unconfirmed' }), false)
    assert.ok(pending.has('local'))
    assert.ok(disk.inflight.local)
    await reload.hydrateAsyncQuestionDrafts()
    await reload.applyAsyncQuestionSnapshot('s', { ...snapshot(4), resolutions: { q1: { status: 'sent', request_id: 'local' } } })
    assert.equal(reload.localState.draftMessages.s, undefined)
    assert.deepEqual(disk.questions.s.choices, {})
    assert.deepEqual(disk.questions.s.acceptedSendIds, ['local'])
    assert.equal(disk.inflight.local, undefined)
})

it('production ack coalesces concurrent persistence and leaves legacy sends synchronous', async () => {
    let commits = 0
    const wait = deferred()
    const { state, disk } = acknowledgementHarness(async () => { commits++; await wait.promise })
    const first = state.acknowledgeInflightSend('s', 'local')
    const second = state.acknowledgeInflightSend('s', 'local')
    assert.equal(first, second)
    await nextTurn()
    assert.equal(commits, 1)
    wait.resolve()
    await first
    await state.acknowledgeInflightSend('s', 'local')
    assert.equal(commits, 1)
    const legacy = { sessionId: 'legacy', text: 'Ordinary message' }
    disk.inflight.legacy = legacy
    // No question draft or structured payload enters the legacy path.
    state.confirmInflightSend = (sessionId, requestId) => { delete disk.inflight[requestId] }
    const ack = state.acknowledgeInflightSend('legacy', 'legacy')
    assert.equal(disk.inflight.legacy, undefined)
    await ack
    assert.equal(commits, 1)
})
