import test from 'node:test'
import assert from 'node:assert/strict'
import { createEphemeralActions, createSendFailureActions, ephemeralFields, serializeDraftSession, isLaunchedEphemeral } from './ephemeralSessions.js'

function fixture(overrides = {}) {
    const saved = new Map(), controls = new Map(), stopped = []
    const store = {
        sessions: { draft: { id: 'draft', project_id: 'p', provider: 'codex', draft: true, ephemeral: true } },
        processStates: {}, sessionItems: {},
        localState: { ephemeralControls: {}, draftAliases: {}, optimisticMessages: {}, failedSends: {} },
        _saveDraftToIndexedDB(id) { saved.set(id, ephemeralFields(this.sessions[id])) },
        recomputeVisualItems() {}, rekeyMruSession() {}, removeMruSession() {},
        _rekeyEphemeralInflight() {}, _clearEphemeralInflight() {},
        ...createEphemeralActions({
            uuid: () => "retry-draft",
            saveControl: async (id, value) => controls.set(id, value),
            deleteControl: async id => controls.delete(id),
            deleteSession: async id => saved.delete(id),
            clearContent: async () => {},
            stop: async id => stopped.push(id),
            navigate: async () => {},
            buildPrompt: (session, prompt) => ({ prompt }),
            ...overrides,
        }),
    }
    return { store, saved, controls, stopped }
}

test('unsent mode stays a draft in serialized state', () => {
    const { store } = fixture()
    assert.equal(isLaunchedEphemeral(store.sessions.draft), false)
    assert.equal(ephemeralFields(store.sessions.draft).draft, true)
})

test('promotion, binding and result retain prompt and ignore late result after discard', async () => {
    const { store, saved } = fixture()
    store.promoteEphemeralSession('draft', { text: 'hello', medias: [{ name: 'a.png', mimeType: 'image/png', type: 'image', data: 'secret' }] })
    assert.equal(store.sessions.draft.ephemeralPrompt.attachments[0].data, undefined)
    await store.bindEphemeralSession('draft', 'canonical')
    assert.equal(store.sessions.draft, undefined)
    assert.equal(saved.get('canonical').draft, false)
    store.receiveEphemeralResult({ session_id: 'canonical', status: 'done', text: 'answer' })
    assert.equal(store.sessions.canonical.ephemeralResult.text, 'answer')
    await store.discardEphemeralSession('canonical')
    store.receiveEphemeralResult({ session_id: 'canonical', status: 'done', text: 'late' })
    assert.equal(store.sessions.canonical, undefined)
    assert.equal(store.isEphemeralSessionId('canonical'), true)
})

test('discard before bind stops canonical without resurrecting content', async () => {
    const { store, stopped } = fixture()
    store.promoteEphemeralSession('draft', { text: 'hello' })
    await store.discardEphemeralSession('draft')
    await store.bindEphemeralSession('draft', 'canonical')
    assert.deepEqual(stopped, ['canonical'])
    assert.deepEqual(store.sessions, {})
})

test('pending admissions survive snapshot and failure settles replacement socket', async () => {
    const { store } = fixture()
    store.promoteEphemeralSession('draft', { text: 'hello' })
    await store.reconcileEphemeralProcesses([], [{ draft_session_id: 'draft' }])
    assert.equal(store.sessions.draft.ephemeralPhase, 'running')
    store.failEphemeralAdmission({ draft_session_id: 'draft', error: 'could not start' })
    assert.equal(store.sessions.draft.ephemeralPhase, 'error')
})

test('snapshot binds before marking loss; received answers survive empty snapshots', async () => {
    const { store } = fixture()
    store.promoteEphemeralSession('draft', { text: 'hello' })
    await store.reconcileEphemeralProcesses([{ session_id: 'canonical', extra: { ephemeral: true, ephemeral_draft_id: 'draft' } }], [])
    assert.equal(store.sessions.canonical.ephemeralPhase, 'running')
    store.receiveEphemeralResult({ session_id: 'canonical', status: 'done', text: 'answer' })
    await store.reconcileEphemeralProcesses([], [])
    assert.equal(store.sessions.canonical.ephemeralPhase, 'done')
})

test('preparation preserves draft when socket sends nothing and excludes layout', () => {
    const { store } = fixture()
    const payload = store.applyCreationSendMode({ session_id: 'draft', layout: { tabs: [] } })
    assert.equal(payload.ephemeral, true)
    assert.equal('layout' in payload, false)
    assert.equal(store.sessions.draft.draft, true)
})

test('Stop before binding survives serialization and resumes against canonical id', async () => {
    const { store, controls, stopped } = fixture()
    store.promoteEphemeralSession('draft', { text: 'hello' })
    await store.stopEphemeralSession('draft')
    assert.equal(stopped.length, 0)
    const reloaded = fixture()
    reloaded.store.sessions.draft = { id: 'draft', ...structuredClone(ephemeralFields(store.sessions.draft)) }
    reloaded.store.localState.ephemeralControls = Object.fromEntries(controls)
    await reloaded.store.reconcileEphemeralProcesses([{ session_id: 'canonical', extra: { ephemeral: true, ephemeral_draft_id: 'draft' } }])
    assert.deepEqual(reloaded.stopped, ['canonical'])
})

test('settled absence loses a running run and cleans its pending controls', async () => {
    const { store, controls } = fixture()
    store.promoteEphemeralSession('draft', { text: 'hello' })
    await store.stopEphemeralSession('draft')
    await store.reconcileEphemeralProcesses([], [])
    assert.equal(store.sessions.draft.ephemeralPhase, 'lost')
    assert.equal(controls.size, 0)
})

test('mode toggling leaves ordinary draft settings available', () => {
    const { store } = fixture()
    store.sessions.draft.hybrid = true
    store.setDraftEphemeral('draft', true)
    assert.equal(store.sessions.draft.hybrid, false)
    store.setDraftEphemeral('draft', false)
    assert.equal(store.sessions.draft.draft, true)
})


test('failure recovery creates a fresh ephemeral draft and leaves sent ids readonly', () => {
    const { store } = fixture()
    store.sessions.draft.title = 'Original title'
    store.sessions.draft.selected_model = 'model'
    store.promoteEphemeralSession('draft', { text: 'hello' })
    const id = store.recoverEphemeralDraft('draft')
    assert.equal(id, 'retry-draft')
    assert.equal(store.sessions.draft, undefined)
    assert.equal(store.sessions[id].ephemeral, true)
    assert.equal(store.sessions[id].draft, true)
    assert.equal(store.sessions[id].ephemeralPhase, undefined)
    assert.equal(store.sessions[id].selected_model, 'model')
    assert.equal(store.sessions[id].title, 'Original title')
    const payload = store.applyCreationSendMode({ session_id: id })
    assert.equal(payload.ephemeral, true)
})

test('production writer field list survives JSON and restores explicit launch state', () => {
    const { store } = fixture()
    assert.equal(serializeDraftSession(store.sessions.draft).draft, true)
    store.promoteEphemeralSession('draft', { text: 'prompt' })
    store.receiveEphemeralResult({ session_id: 'draft', status: 'done', text: 'answer', cost_usd: 0.1 })
    const stored = JSON.parse(JSON.stringify(serializeDraftSession(store.sessions.draft)))
    const restored = ephemeralFields(stored)
    assert.equal(restored.draft, false)
    assert.deepEqual(restored.ephemeralPrompt, { text: 'prompt', attachments: [] })
    assert.equal(restored.ephemeralResult.text, 'answer')
    assert.equal(restored.ephemeralResult.cost_usd, 0.1)
    assert.equal(stored.projectId, 'p')
    assert.equal(stored.provider, 'codex')
    assert.equal(ephemeralFields({}).draft, true)
})


test('Stop retries on STARTING after an early binding before registration', async () => {
    const { store, stopped } = fixture()
    store.promoteEphemeralSession('draft', { text: 'hello' })
    await store.discardEphemeralSession('draft')
    await store.bindEphemeralSession('draft', 'canonical')
    // process_state uses this same production action after agent registration.
    await store.bindEphemeralSession('draft', 'canonical')
    assert.deepEqual(stopped, ['canonical', 'canonical'])
    assert.equal(store.localState.ephemeralControls.draft.discard, true)
    store.clearEphemeralControl('canonical')
    assert.deepEqual(store.localState.ephemeralControls, {})
})


test('bound Discard removes content before deferred Stop and drops a late failed-send snapshot', async () => {
    let releaseStop
    const stopWait = new Promise(resolve => { releaseStop = resolve })
    const { store } = fixture({ stop: () => stopWait })
    const inflight = new Map(), disk = new Map()
    Object.assign(store, createSendFailureActions(inflight))
    store._dropInflightSend = id => { inflight.delete(id); disk.delete(id) }
    store._applySendFailure = () => assert.fail('Discard cannot recover a draft')
    store.promoteEphemeralSession('draft', { text: 'private' })
    await store.bindEphemeralSession('draft', 'canonical')
    const discarded = store.discardEphemeralSession('canonical')
    assert.equal(store.sessions.canonical, undefined)
    // An already-consumed or hydrated snapshot may arrive under the canonical id.
    const snapshot = { sessionId: 'canonical', ephemeral: true, text: 'private' }
    inflight.set('late', snapshot)
    disk.set('late', snapshot)
    assert.equal(store.failInflightSend('late', { message: 'late rejection' }), true)
    assert.equal(disk.size, 0)
    assert.equal(inflight.size, 0)
    assert.equal(store.sessions['retry-draft'], undefined)
    releaseStop()
    await discarded
})

test('reload purges both ids after a crash following Discard tombstone persistence', async () => {
    const content = new Map([['draft', 'draft media'], ['canonical', 'prompt and media']])
    const { store } = fixture({ clearContent: async id => content.delete(id) })
    store.localState.ephemeralControls.draft = { draftId: 'draft', canonicalId: 'canonical', discard: true }
    store.sessions.canonical = { id: 'canonical', ephemeral: true, ephemeralDraftId: 'draft', draft: false }
    assert.equal(store.isEphemeralDiscarded('draft'), true)
    assert.equal(store.isEphemeralDiscarded('canonical'), true)
    await store.purgeEphemeralContent(['draft', 'canonical'])
    assert.equal(content.size, 0)
    assert.equal(store.sessions.canonical, undefined)
    assert.equal(store.localState.ephemeralControls.draft.discard, true)
})

test('a pre-binding Discard does not consume an unrelated normal send failure', () => {
    const { store } = fixture()
    const inflight = new Map([['normal-request', { sessionId: 'normal', text: 'normal prompt' }]])
    Object.assign(store, createSendFailureActions(inflight))
    store.localState.ephemeralControls.draft = { draftId: 'draft', discard: true }
    store.sessions.normal = { id: 'normal', draft: false }
    let applied = false
    store._dropInflightSend = () => assert.fail('Unrelated snapshot must not be discarded')
    store._applySendFailure = (id, entry) => { applied = id === 'normal-request' && entry.sessionId === 'normal' }
    assert.equal(store.isEphemeralDiscarded('normal'), false)
    assert.equal(store.isEphemeralDiscarded('unrelated-ephemeral'), false)
    assert.equal(store.failInflightSend('normal-request', { message: 'rejected' }), true)
    assert.equal(applied, true)
})
