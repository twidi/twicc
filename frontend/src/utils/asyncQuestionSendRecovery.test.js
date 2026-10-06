import assert from 'node:assert/strict'
import { it } from 'node:test'
import { readFileSync } from 'node:fs'
import { createSendFailureActions } from './ephemeralSessions.js'
import * as questions from './asyncQuestions.js'
import * as storage from './draftStorage.js'
import { createAsyncQuestionActions } from './asyncQuestionState.js'
import { setAttachmentPayloadFields, sendComposerMessage, snapshotAttachments, snapshotAttachmentRefs } from './composerAttachments.js'

const record = (id, bucket = 'b1') => ({ id, bucket, sessionId: 's', position: 0, name: `${id}.png`, size: 3, mimeType: 'image/png', kind: 'image' })
const attachmentDeps = (records = []) => ({
    composerRecords: { value: records }, legacyAttachmentCount: { value: 0 }, attachmentsReady: { value: true },
    composerAttachmentsReady: () => true, setAttachmentPayloadFields, sendComposerMessage, snapshotAttachments,
})

const batch = (id = 'q1') => ({ item_id: id, status: 'ready', questions: [{ index: 3, title: 'Choose?', options: ['Yes'] }] })
const ready = () => ({ revision: 1, widget_enabled: true, batches: [batch(), batch('unanswered')], resolutions: {} })
const choices = () => ({ choices: { q1: { 3: { kind: 'other', value: 'Original answer' } } }, sourceBatches: { q1: batch() }, recoveredIds: [] })
const outgoing = () => questions.prepareAsyncQuestionSend({ snapshot: ready(), questionDraft: choices(), rawText: '  Extra text  ' })
const resolution = (id = 'external', status = 'sent') => ({ ...ready(), revision: 2, batches: [], resolutions: { q1: { status, request_id: id } } })

it('snapshots raw text, selected answers and every ready batch once', () => {
    const send = outgoing()
    assert.equal(send.rawText, '  Extra text  ')
    assert.deepEqual(send.asyncQuestions.batch_ids, ['q1', 'unanswered'])
    assert.equal(send.text, '::: Answers to your questions\n\n**Question:** Choose?\n\n**Answer:** Original answer\n\n:::\n\n  Extra text  ')
    assert.equal(questions.asyncQuestionRetryState(send, { ...ready(), revision: 99 }).canRetry, true)
    assert.equal(questions.asyncQuestionRetryState(send, ready()).text, send.rawText)
})
it('filters empty Other, disabled controls and pending batches', () => {
    const draft = choices(); draft.choices.q1[3].value = ' \n '
    assert.deepEqual(questions.prepareAsyncQuestionSend({ snapshot: ready(), questionDraft: draft, rawText: 'Hi' }).asyncQuestions.answers, [])
    assert.equal(questions.prepareAsyncQuestionSend({ snapshot: { ...ready(), widget_enabled: false }, questionDraft: choices(), rawText: 'Hi' }).asyncQuestions, undefined)
    assert.deepEqual(questions.prepareAsyncQuestionSend({ snapshot: ready(), questionDraft: choices(), rawText: 'Hi', pendingQuestionIds: ['q1'] }).asyncQuestions.batch_ids, ['unanswered'])
})
it('does not recover its own accepted answers as an external resolution', () => {
    const result = questions.reconcileSend({ draft: {}, choices: choices(), snapshot: resolution('send-1'), pendingSends: { 'send-1': outgoing() } })
    assert.equal(result.recoveredText, '')
    assert.equal(result.acceptedRequestId, 'send-1')
    assert.deepEqual(result.pendingQuestionIds, [])
})
for (const status of ['sent', 'dismissed']) it(`recovers Other before external ${status} without a send`, () => {
    const result = questions.reconcileSend({ draft: { message: 'Later text', mediaIds: ['m'] }, choices: choices(), snapshot: resolution('browser-2', status) })
    assert.match(result.recoveredText, /Original answer/)
    assert.ok(result.draft.message.endsWith('Later text'))
    assert.deepEqual(result.draft.mediaIds, ['m'])
})
it('keeps uncertain sends locked until native evidence resolves them', () => {
    const send = { ...outgoing(), status: 'uncertain', code: 'send_uncertain' }
    const result = questions.reconcileSend({ choices: choices(), snapshot: resolution(), pendingSends: { local: send } })
    assert.equal(result.recoveredText, '')
    assert.deepEqual(result.pendingQuestionIds, ['q1', 'unanswered'])
    assert.equal(questions.asyncQuestionRetryState(send, ready()).canRetry, false)
})
it('consumes only the captured text and choices, preserving later edits and new batches', () => {
    const send = outgoing(), later = choices()
    later.choices.new = { 0: { kind: 'other', value: 'New answer' } }
    later.sourceBatches.new = batch('new')
    const result = questions.consumeAsyncQuestionSend(send, { message: send.rawText + 'New text', mediaIds: ['m'] }, later)
    assert.equal(result.draft.message, 'New text')
    assert.equal(result.questionDraft.choices.q1, undefined)
    assert.equal(result.questionDraft.choices.new[0].value, 'New answer')
    const changed = choices(); changed.choices.q1[3].value = 'Edited during commit'
    assert.equal(questions.consumeAsyncQuestionSend(send, { message: 'Replaced' }, changed).questionDraft.choices.q1[3].value, 'Edited during commit')
})
it('stale Edit produces ordinary text and Retry cannot send stale references', () => {
    const send = outgoing()
    assert.equal(questions.asyncQuestionRetryState(send, resolution()).canRetry, false)
    const result = questions.restoreAsyncQuestionSend(send, { message: 'Later' }, { choices: {} }, resolution())
    assert.equal(result.draft.message, send.text + '\n\nLater')
    assert.deepEqual(result.questionDraft.choices, {})
})
it('ready Edit restores raw text and choices without double formatting; legacy stays ordinary', () => {
    const send = outgoing()
    const result = questions.restoreAsyncQuestionSend(send, {}, { choices: {} }, ready())
    assert.equal(result.draft.message, send.rawText)
    assert.equal(result.questionDraft.choices.q1[3].value, 'Original answer')
    assert.deepEqual(questions.asyncQuestionRetryState({ text: 'Legacy' }, null), { canRetry: true, text: 'Legacy', asyncQuestions: undefined })
})

function database() {
    const data = { draftMessages: { s: { message: 'before' } }, asyncQuestionDrafts: { s: choices() }, inflightSends: {} }
    const transactions = []
    const db = { transaction(names) {
        const next = structuredClone(data)
        const tx = { names, objectStore: name => ({
            put(value, key) { next[name][key] = structuredClone(value); return {} },
            delete(key) { delete next[name][key]; return {} },
            openCursor() {
                const request = {}, keys = Object.keys(next[name]); let index = 0
                const advance = () => queueMicrotask(() => {
                    const key = keys[index++]
                    request.result = key === undefined ? null : { key, value: next[name][key],
                        delete: () => { delete next[name][key] }, continue: advance }
                    request.onsuccess?.()
                })
                advance(); return request
            },
            get(key) { const request = {}; queueMicrotask(() => { request.result = next[name][key]; request.onsuccess?.() }); return request },
        }), commit() { for (const name of Object.keys(data)) data[name] = next[name]; tx.oncomplete() },
        abort() { tx.onabort() }, }
        transactions.push(tx); return tx
    } }
    return { data, transactions, open: async () => db }
}
const tick = async () => { for (let i = 0; i < 8; i++) await Promise.resolve() }
it('staging waits for commit, includes all three stores, abort preserves reload state', async () => {
    const db = database(); const send = { ...outgoing(), sessionId: 's' }
    let done = false
    const p = storage.stageAsyncQuestionSend('r', send, { message: '' }, { choices: {} }, db.open).then(() => { done = true })
    await tick()
    assert.equal(done, false)
    assert.deepEqual(new Set(db.transactions[0].names), new Set(['draftMessages', 'asyncQuestionDrafts', 'inflightSends']))
    assert.equal(db.data.draftMessages.s.message, 'before')
    db.transactions[0].abort()
    await assert.rejects(p, /abort/)
    assert.equal(db.data.draftMessages.s.message, 'before')
    assert.deepEqual(db.data.inflightSends, {})
    const staged = storage.stageAsyncQuestionSend('r', send, { message: '' }, { choices: {} }, db.open)
    await tick(); db.transactions[1].commit(); await staged
    assert.equal(db.data.inflightSends.r.status, 'staged')
    assert.equal(db.data.draftMessages.s.message, '')
    assert.equal(db.data.inflightSends.r.rawText, send.rawText)
})
it('dispatched status cannot recreate a snapshot deleted by an early ack', async () => {
    const db = database()
    const p = storage.markAsyncQuestionSendDispatched('accepted', db.open)
    await tick(); db.transactions[0].commit(); await p
    assert.deepEqual(db.data.inflightSends, {})
})
it('socket rollback commits restored draft and snapshot deletion together', async () => {
    const db = database(); db.data.inflightSends.r = { ...outgoing(), status: 'staged' }
    const p = storage.restoreStagedAsyncQuestionSend('r', 's', { message: 'restored' }, choices(), db.open)
    await tick()
    assert.ok(db.data.inflightSends.r)
    db.transactions[0].commit(); await p
    assert.equal(db.data.draftMessages.s.message, 'restored')
    assert.deepEqual(db.data.inflightSends, {})
})

function production(overrides = {}) {
    const pending = new Map(), frames = [], disk = { questions: choices(), draft: { message: '  Extra text  ' }, sends: {} }
    const state = { sessions: { s: { provider: 'codex' } }, localState: { draftMessages: { s: structuredClone(disk.draft) },
        asyncQuestionDrafts: { s: choices() }, asyncQuestionSnapshots: { s: ready() }, asyncQuestionNotices: {}, draftAppendSignals: {}, failedSends: {} } }
    const deps = {
        pendingSends: () => Object.fromEntries([...pending, ...Object.entries(state.localState.failedSends.s || {}).map(([id, send]) =>
            [id, { ...send, status: send.code === 'send_uncertain' ? 'uncertain' : 'rejected' }])]),
        cancelDraftSave() {}, recover: async (id, draft, q) => { disk.draft = structuredClone(draft); disk.questions = structuredClone(q) },
        stageSend: async (id, entry, draft, q) => { disk.sends[id] = structuredClone(entry); disk.draft = structuredClone(draft); disk.questions = structuredClone(q) },
        restoreStaged: async (id, sessionId, draft, q) => { delete disk.sends[id]; disk.draft = structuredClone(draft); disk.questions = structuredClone(q) },
        markDispatched: async id => { if (disk.sends[id]) disk.sends[id].status = 'dispatched' },
        send: async frame => { assert.ok(pending.has(frame.request_id)); frames.push(frame); return true }, ...overrides,
    }
    Object.assign(state, createAsyncQuestionActions(deps))
    state.registerOutgoingSend = (sessionId, projectId, id, entry) => { assert.ok(disk.sends[id]); pending.set(id, entry) }
    state.cancelStagedOutgoingSend = (sessionId, id) => pending.delete(id)
    state.removeFailedSend = (sessionId, id) => { if (state.localState.failedSends[sessionId]) delete state.localState.failedSends[sessionId][id] }
    state.getFailedSend = (sessionId, id) => state.localState.failedSends[sessionId]?.[id]
    state.markInflightSendAccepted = (sessionId, id) => { const send = pending.get(id); if (send) send.status = 'accepted' }
    state.confirmInflightSend = (sessionId, id) => { pending.delete(id); delete disk.sends[id] }
    return { state, disk, frames, pending, deps }
}
const deferred = () => { let resolve, reject; const promise = new Promise((a, b) => { resolve = a; reject = b }); return { promise, resolve, reject } }
const dispatch = (h, id = 'local', send = outgoing()) => h.state.sendAsyncQuestionMessage('s', 'p', id, { request_id: id, text: send.rawText, async_questions: send.asyncQuestions }, send)
it('production abort before dispatch retains text and choices and releases only its locks', async () => {
    const h = production({ stageSend: async () => { throw new Error('Abort before dispatch') } })
    await assert.rejects(dispatch(h), /Abort before dispatch/)
    assert.equal(h.frames.length, 0)
    assert.equal(h.state.localState.draftMessages.s.message, '  Extra text  ')
    assert.deepEqual(h.state.localState.asyncQuestionDrafts.s, choices())
    assert.deepEqual(h.state.getPendingAsyncQuestionIds('s'), [])
})
it('production staging reserves batches, waits for commit, then preserves concurrent typing and new choices', async () => {
    const wait = deferred()
    const original = production({ stageSend: async (id, entry, draft, q) => {
        await wait.promise
        original.disk.sends[id] = structuredClone(entry)
        original.disk.draft = structuredClone(draft)
        original.disk.questions = structuredClone(q)
    } })
    const sending = dispatch(original)
    await tick()
    assert.equal(original.frames.length, 0)
    assert.deepEqual(original.state.getPendingAsyncQuestionIds('s'), ['q1', 'unanswered'])
    assert.equal(await dispatch(original, 'duplicate'), false)
    original.state.localState.draftMessages.s.message += 'Later'
    original.state.localState.asyncQuestionDrafts.s.choices.new = { 0: { kind: 'other', value: 'Later answer' } }
    wait.resolve(); await sending
    assert.equal(original.frames.length, 1)
    assert.equal(original.state.localState.draftMessages.s.message, 'Later')
    assert.equal(original.state.localState.asyncQuestionDrafts.s.choices.new[0].value, 'Later answer')
})
it('production socket failure restores the staged text, choices and leaves attachments in their store', async () => {
    const h = production({ send: async () => false })
    assert.equal(await dispatch(h), false)
    assert.deepEqual(h.state.localState.asyncQuestionDrafts.s.choices, choices().choices)
    assert.equal(h.state.localState.draftMessages.s.message, '  Extra text  ')
    assert.deepEqual(h.disk.sends, {})
    assert.deepEqual(h.state.getPendingAsyncQuestionIds('s'), [])
})
for (const first of ['ack', 'snapshot']) it(`production ${first} first settles acceptance and preserves a new batch`, async () => {
    const h = production(); await dispatch(h)
    h.state.localState.draftMessages.s.message = 'Later'
    h.state.localState.asyncQuestionDrafts.s.choices.new = { 0: { kind: 'other', value: 'New' } }
    const update = { ...resolution('local'), batches: [batch('new')] }
    if (first === 'ack') { await h.state.acknowledgeInflightSend('s', 'local'); await h.state.applyAsyncQuestionSnapshot('s', update) }
    else { await h.state.applyAsyncQuestionSnapshot('s', update); await h.state.acknowledgeInflightSend('s', 'local') }
    assert.equal(h.state.localState.draftMessages.s.message, 'Later')
    assert.equal(h.state.localState.asyncQuestionDrafts.s.choices.new[0].value, 'New')
    assert.deepEqual(h.disk.sends, {})
    assert.deepEqual(h.state.getPendingAsyncQuestionIds('s'), [])
})
it('production early acknowledgement cannot be undone by dispatch completion', async () => {
    let live
    live = production({ send: async frame => { await live.state.acknowledgeInflightSend('s', frame.request_id); return true } })
    await dispatch(live)
    assert.deepEqual(live.disk.sends, {})
    assert.deepEqual(live.state.localState.asyncQuestionDrafts.s.acceptedSendIds, ['local'])
})
it('production failed Edit restores raw choices if ready and ordinary text if stale', async () => {
    for (const stale of [false, true]) {
        const h = production(); h.state.localState.draftMessages.s = { message: 'Later' }; h.state.localState.asyncQuestionDrafts.s = { choices: {} }
        const entry = { ...outgoing(), requestId: 'failed', code: 'send_failed' }
        h.state.localState.failedSends.s = { failed: entry }; h.disk.sends.failed = entry
        if (stale) h.state.localState.asyncQuestionSnapshots.s = resolution()
        await h.state.editAsyncQuestionFailure('s', 'failed')
        assert.equal(h.state.localState.draftMessages.s.message, (stale ? entry.text : entry.rawText) + '\n\nLater')
        assert.equal(!!h.state.localState.asyncQuestionDrafts.s.choices.q1, !stale)
        assert.deepEqual(h.disk.sends, {})
    }
})

it('a snapshot during staging cannot recover locked outgoing choices as an external send', async () => {
    const wait = deferred()
    const h = production({ stageSend: async (id, entry) => { await wait.promise; h.disk.sends[id] = entry } })
    const sending = dispatch(h)
    await tick()
    await h.state.applyAsyncQuestionSnapshot('s', resolution('another-browser'))
    assert.equal(h.state.localState.draftMessages.s.message, '  Extra text  ')
    assert.equal(h.state.localState.asyncQuestionDrafts.s.choices.q1[3].value, 'Original answer')
    wait.resolve(); await sending
    assert.equal(h.state.localState.draftMessages.s.message, '')
})
it('retry staging atomically replaces the old failed request and keeps new draft content', async () => {
    const db = database(); db.data.inflightSends.failed = outgoing()
    const p = storage.stageAsyncQuestionSend('fresh', { ...outgoing(), sessionId: 's', retryRequestId: 'failed' }, { message: 'Later' }, { choices: {} }, db.open)
    await tick(); db.transactions[0].commit(); await p
    assert.equal(db.data.inflightSends.failed, undefined)
    assert.equal(db.data.inflightSends.fresh.rawText, '  Extra text  ')
    assert.equal(db.data.draftMessages.s.message, 'Later')
})
it('staged media copies retain the existing cap without duplicate SDK blobs', () => {
    const huge = 'x'.repeat(8 * 1024 * 1024 + 1)
    const result = storage.inflightSnapshotRecord({ ...outgoing(), medias: [{ id: 'm', data: huge }], images: [{ data: huge }], documents: [] })
    assert.equal(result.mediasDropped, true)
    assert.equal(result.mediaCount, 1)
    assert.deepEqual(result.medias, [])
    assert.equal(result.images, undefined)
    assert.equal(result.documents, undefined)
    assert.equal(storage.inflightSnapshotRecord(result).mediaCount, 1)
})

const dataSource = readFileSync(new URL('../stores/data.js', import.meta.url), 'utf8')
function storeActions(deps, names) {
    const source = names.map(name => {
        let start = dataSource.indexOf(`        ${name}(`)
        if (start < 0) start = dataSource.indexOf(`        async ${name}(`)
        assert.ok(start >= 0)
        let end = dataSource.indexOf('\n        /**', start)
        if (end < 0) end = dataSource.length
        return dataSource.slice(start, end)
    }).join('\n')
    return new Function(...Object.keys(deps), `return {${source}}`)(...Object.values(deps))
}
for (const status of ['staged', 'dispatched']) it(`reload after ${status} retains an uncertain identity until matching native evidence`, async () => {
    const h = production()
    h.disk.sends.local = { ...outgoing(), sessionId: 's', status, sentAt: 1 }
    h.state.localState.draftAliases = {}
    h.state.isEphemeralDiscarded = () => false
    h.state.auditAllLoadedInflightSends = () => {}
    Object.assign(h.state, createSendFailureActions(h.pending, { deleteInflight: async id => { delete h.disk.sends[id] } }))
    h.state._applySendFailure = (id, entry, info) => {
        h.state.localState.failedSends.s ||= {}
        h.state.localState.failedSends.s[id] = { ...entry, ...info, requestId: id }
    }
    Object.assign(h.state, storeActions({
        getAllInflightSends: async () => structuredClone(h.disk.sends), inflightSends: h.pending,
        deleteInflightSend: async id => { delete h.disk.sends[id] }, retainsAsyncQuestionSend: questions.retainsAsyncQuestionSend,
        INFLIGHT_SEND_TTL_MS: 10,
    }, ['hydrateInflightSends']))
    await h.state.hydrateInflightSends()
    const failed = h.state.getFailedSend('s', 'local')
    assert.equal(failed.code, 'send_uncertain')
    assert.equal(questions.asyncQuestionRetryState(failed, ready()).canRetry, false)
    assert.deepEqual(h.state.getPendingAsyncQuestionIds('s'), ['q1', 'unanswered'])
    await h.state.applyAsyncQuestionSnapshot('s', resolution('local'))
    assert.equal(h.disk.sends.local, undefined)
    assert.deepEqual(h.state.getPendingAsyncQuestionIds('s'), [])
    assert.equal(h.state.localState.draftMessages.s.message, '  Extra text  ')
})
it('correlated uncertain failure stays locked while a definite failure releases only its batches', () => {
    const h = production()
    const deps = { isLaunchedEphemeral: () => false, PROCESS_STATE: { STARTING: 'starting' },
        saveInflightSend: async () => {} }
    h.state.localState.draftAliases = {}; h.state.processStates = {}
    h.state.dropDiscardedSendFailure = () => false
    h.state._materializeFailedSendItem = send => ({ failedSend: send })
    h.state.recomputeVisualItems = () => {}
    Object.assign(h.state, storeActions(deps, ['_applySendFailure']))
    h.state.lockAsyncQuestionSend('s', 'new', { batch_ids: ['new-batch'] })
    h.state.lockAsyncQuestionSend('s', 'old', outgoing().asyncQuestions)
    h.state._applySendFailure('old', { ...outgoing(), sessionId: 's' }, { code: 'send_uncertain' })
    assert.deepEqual(new Set(h.state.getPendingAsyncQuestionIds('s')), new Set(['new-batch', 'q1', 'unanswered']))
    h.state._applySendFailure('old', { ...outgoing(), sessionId: 's' }, { code: 'async_questions_stale' })
    assert.deepEqual(h.state.getPendingAsyncQuestionIds('s'), ['new-batch'])
    assert.equal(h.state.getFailedSend('s', 'old').rawText, outgoing().rawText)
})

it('production composer sends answer-only after a staged hybrid change and keeps post-send typing and new attachments', async () => {
    const source = readFileSync(new URL('../components/message/MessageInput.vue', import.meta.url), 'utf8')
    const start = source.indexOf('async function handleSend()')
    const end = source.indexOf('\n/**', start)
    const frames = [], forgotten = [], records = [record('original')]
    const ref = value => ({ value })
    const messageText = ref('')
    const store = {
        localState: { attachmentRuntime: {} }, setStagedHybrid() {}, getPendingAsyncQuestionIds: () => [], getAttachmentPreviewUrl: () => null,
        reserveAsyncQuestionSend: () => true,
        applyCreationSendMode() {},
        async sendAsyncQuestionMessage(s, p, id, payload, send) {
            frames.push(payload)
            assert.equal(payload.text, '')
            assert.equal(send.text, questions.formatAsyncQuestionMessage(send.sourceBatches, send.asyncQuestions.answers, ''))
            assert.deepEqual(payload.async_questions.batch_ids, ['q1', 'unanswered'])
            // Answers AND attachment refs travel in one frame; refs never ride as legacy payloads.
            assert.deepEqual(payload.attachments, [{ bucket: 'b1', id: 'original' }])
            assert.equal(payload.images, undefined)
            assert.equal(payload.documents, undefined)
            assert.deepEqual(send.attachments.map(item => item.id), ['original'])
            assert.deepEqual(forgotten, [], 'Nothing is forgotten before the dispatch succeeds')
            messageText.value = 'New typing'
            records.push(record('new'))
            return true
        },
        forgetAttachments: async (s, { ids }) => forgotten.push(ids), getDraftMessage: () => ({ message: 'New typing' }),
        clearDraftMessage() { assert.fail('A question send must not globally clear the draft') },
        releaseAttachments() { assert.fail('A successful send never releases its refs') },
        clearAttachmentsForSession() { assert.fail('A question send must not globally clear attachments') },
    }
    const deps = { props: { sessionId: 's', projectId: 'p', sendingLocked: false }, messageText,
        asyncQuestionSendClassification: ref({ hasAnswers: true, canSend: true, commandBlocked: false, settingsOnly: false }),
        canSendAttachmentsOnly: ref(true), isHybridStaged: ref(true), isDisabled: ref(false), isDraft: ref(false),
        isComposerCommand: ref(false), asyncQuestionSnapshot: ref(ready()), asyncQuestionDraft: ref(choices()),
        prepareAsyncQuestionSend: questions.prepareAsyncQuestionSend, store, session: ref({ provider: 'codex' }),
        isContextMaxForced: ref(false), attachmentCount: ref(1), settings: { providerStore: ref({ defaultModel: 'model' }) },
        generateUUID: () => 'fresh', ...attachmentDeps(records),
        sendWsMessage: payload => { frames.push(payload); return true }, textareaRef: ref(null),
        toast: { error: message => assert.fail(message), warning: message => assert.fail(message) },
    }
    for (const setting of ['Model', 'PermissionMode', 'Effort', 'Thinking', 'ClaudeInChrome', 'FastMode', 'ContextMax']) {
        deps['selected' + setting] = ref(null); deps['active' + setting] = ref(null)
    }
    const send = new Function(...Object.keys(deps), `${source.slice(start, end)}; return handleSend`)(...Object.values(deps))
    await send()
    assert.equal(frames[0].type, 'set_session_hybrid')
    assert.equal(frames[1].type, 'send_message')
    assert.equal(messageText.value, 'New typing')
    assert.deepEqual(forgotten, [['original']], 'Only the sent record is forgotten; the attachment added meanwhile stays')
})

it('a failed question dispatch keeps the attachment records and releases nothing', async () => {
    const source = readFileSync(new URL('../components/message/MessageInput.vue', import.meta.url), 'utf8')
    const start = source.indexOf('async function handleSend()'), end = source.indexOf('\n/**', start)
    const ref = value => ({ value }), forgotten = []
    const store = {
        localState: { attachmentRuntime: {} }, setStagedHybrid() {}, getPendingAsyncQuestionIds: () => [], getAttachmentPreviewUrl: () => null,
        reserveAsyncQuestionSend: () => true, applyCreationSendMode() {},
        sendAsyncQuestionMessage: async () => false,
        forgetAttachments: async (s, { ids }) => forgotten.push(ids),
        releaseAttachments() { assert.fail('A failed send never releases its refs') },
        getDraftMessage: () => ({ message: '  Extra text  ' }), clearDraftMessage() { assert.fail('The draft stays') },
    }
    const deps = { props: { sessionId: 's', projectId: 'p', sendingLocked: false }, messageText: ref('  Extra text  '),
        asyncQuestionSendClassification: ref({ hasAnswers: true, canSend: true, commandBlocked: false, settingsOnly: false }),
        canSendAttachmentsOnly: ref(true), isHybridStaged: ref(false), isDisabled: ref(false), isDraft: ref(false),
        isComposerCommand: ref(false), asyncQuestionSnapshot: ref(ready()), asyncQuestionDraft: ref(choices()),
        prepareAsyncQuestionSend: questions.prepareAsyncQuestionSend, store, session: ref({ provider: 'codex' }),
        isContextMaxForced: ref(false), attachmentCount: ref(1), settings: { providerStore: ref({ defaultModel: 'model' }) },
        generateUUID: () => 'fresh', ...attachmentDeps([record('kept')]),
        sendWsMessage: () => assert.fail('A question send uses the staged action'), textareaRef: ref(null),
        toast: { error: message => assert.fail(message), warning: message => assert.fail(message) },
    }
    for (const setting of ['Model', 'PermissionMode', 'Effort', 'Thinking', 'ClaudeInChrome', 'FastMode', 'ContextMax']) {
        deps['selected' + setting] = ref(null); deps['active' + setting] = ref(null)
    }
    await new Function(...Object.keys(deps), `${source.slice(start, end)}; return handleSend`)(...Object.values(deps))()
    assert.deepEqual(forgotten, [])
    assert.equal(deps.messageText.value, '  Extra text  ')
})

it('production Retry sends raw text and structured answers under a fresh identity', async () => {
    const source = readFileSync(new URL('../components/session/detail/items/FailedSendBanner.vue', import.meta.url), 'utf8')
    const start = source.indexOf('async function retry()'), end = source.indexOf('\n/** Put', start)
    const entry = { ...outgoing(), requestId: 'failed', medias: [], code: 'send_failed' }
    let sent
    const deps = {
        getEntry: () => entry, snapshotAttachmentRefs, retryState: { value: questions.asyncQuestionRetryState(entry, ready()) },
        props: { sessionId: 's', projectId: 'p' }, generateUUID: () => 'fresh',
        resizeMediasForSend: async values => values, getProviderHelpers: () => ({}), getProviderStore: () => ({}),
        mediasToSdkFormat: () => ({ images: [], documents: [] }),
        toast: { error: message => assert.fail(message) },
        store: { getSession: () => ({ provider: 'codex' }), applyCreationSendMode() {},
            sendAsyncQuestionMessage: async (...args) => { sent = args } },
    }
    await new Function(...Object.keys(deps), `${source.slice(start, end)}; return retry`)(...Object.values(deps))()
    assert.equal(sent[2], 'fresh')
    assert.equal(sent[3].text, entry.rawText)
    assert.deepEqual(sent[3].async_questions, entry.asyncQuestions)
    assert.equal(sent[4].text, entry.text)
    assert.equal(sent[5].retryRequestId, 'failed')
})

it('production socket wrapper reports a closed socket without buffering structured sends', () => {
    const source = readFileSync(new URL('../composables/useWebSocket.js', import.meta.url), 'utf8')
    const start = source.indexOf('export function sendWsMessage('), end = source.indexOf('\n/**', start)
    const calls = [], state = { wsSendFn: (...args) => { calls.push(args); return false } }
    const send = new Function('__hmrState', `${source.slice(start, end).replace('export ', '')}; return sendWsMessage`)(state)
    assert.equal(send({ async_questions: { batch_ids: ['q1'] } }, { buffer: false }), false)
    assert.equal(calls[0][1], false)
    assert.equal(send({ text: 'ordinary' }), true, 'Legacy callers keep their existing behavior')
    state.wsSendFn = (...args) => { calls.push(args); return true }
    assert.equal(send({ async_questions: { batch_ids: ['q1'] } }, { buffer: false }), true)
    assert.equal(calls[2][1], false)
})

it('an explicit stale rejection disables Retry before a fresh lifecycle snapshot arrives', () => {
    const send = { ...outgoing(), code: 'async_questions_stale' }
    assert.equal(questions.asyncQuestionRetryState(send, ready()).canRetry, false)
    const recovered = questions.restoreAsyncQuestionSend(send, {}, { choices: {} }, ready())
    assert.equal(recovered.draft.message, send.text)
    assert.deepEqual(recovered.questionDraft.choices, {})
})

function delayedComposer() {
    // No preparation step is left in the composer (attachments are refs, nothing is resized): the
    // asynchronous window after the reservation is the durable staging commit, gated here.
    const wait = deferred(), errors = [], records = [record('original')], forgotten = []
    const h = production({ stageSend: async (id, entry, draft, q) => {
        await wait.promise
        h.disk.sends[id] = structuredClone(entry); h.disk.draft = structuredClone(draft); h.disk.questions = structuredClone(q)
    } })
    const ref = value => ({ value })
    const computed = fn => ({ get value() { return fn() } })
    const messageText = computed(() => h.state.localState.draftMessages.s?.message || '')
    const source = readFileSync(new URL('../components/message/MessageInput.vue', import.meta.url), 'utf8')
    const start = source.indexOf('async function handleSend()'), end = source.indexOf('\n/**', start)
    Object.assign(h.state, createSendFailureActions(h.pending, { deleteInflight: async id => { delete h.disk.sends[id] } }))
    Object.assign(h.state, {
        applyCreationSendMode() {}, getAttachmentPreviewUrl: () => null,
        getDraftMessage: () => h.state.localState.draftMessages.s,
        forgetAttachments: async (s, { ids }) => { forgotten.push(...ids) },
    })
    const deps = { props: { sessionId: 's', projectId: 'p', sendingLocked: false },
        messageText: { get value() { return messageText.value }, set value(value) { h.state.localState.draftMessages.s = { message: value } } },
        asyncQuestionSendClassification: ref({ hasAnswers: true, canSend: true, commandBlocked: false, settingsOnly: false }),
        canSendAttachmentsOnly: ref(true), isHybridStaged: ref(false), isDisabled: ref(false), isDraft: ref(false),
        isComposerCommand: ref(false), asyncQuestionSnapshot: computed(() => h.state.localState.asyncQuestionSnapshots.s),
        asyncQuestionDraft: computed(() => h.state.localState.asyncQuestionDrafts.s),
        prepareAsyncQuestionSend: questions.prepareAsyncQuestionSend, store: h.state, session: ref({ provider: 'codex' }),
        isContextMaxForced: ref(false), attachmentCount: ref(1), settings: { providerStore: ref({ defaultModel: 'model' }) },
        generateUUID: () => 'prepared', ...attachmentDeps(records),
        sendWsMessage: () => assert.fail('Question sends use the staged action'), textareaRef: ref(null),
        toast: { error: message => errors.push(message), warning: message => assert.fail(message) },
    }
    const widget = readFileSync(new URL('../components/message/AsyncQuestions.vue', import.meta.url), 'utf8')
        .split('<script setup>')[1].split('</script>')[0].replace(/^import .*$/gm, '')
    h.state.getAsyncQuestionDraft = () => h.state.localState.asyncQuestionDrafts.s
    const updateChoices = new Function('computed', 'defineProps', 'defineEmits', 'useDataStore', 'toast',
        `${widget}; return updateChoices`)(computed, () => ({ sessionId: 's', snapshot: ready() }), () => () => {}, () => h.state, deps.toast)
    for (const setting of ['Model', 'PermissionMode', 'Effort', 'Thinking', 'ClaudeInChrome', 'FastMode', 'ContextMax']) {
        deps['selected' + setting] = ref(null); deps['active' + setting] = ref(null)
    }
    const send = new Function(...Object.keys(deps), `${source.slice(start, end)}; return handleSend`)(...Object.values(deps))
    return { ...h, wait, send, updateChoices, errors, records, forgotten }
}

it('composer reserves captured answers before the delayed staging commit and rejects edits until own acceptance', async () => {
    const h = delayedComposer()
    const sending = h.send()
    await tick()
    h.updateChoices(batch(), { 3: { kind: 'other', value: 'Unsent edit during staging' } })
    assert.equal(h.state.localState.asyncQuestionDrafts.s.choices.q1[3].value, 'Original answer')
    assert.deepEqual(h.state.getPendingAsyncQuestionIds('s'), ['q1', 'unanswered'])
    h.state.localState.draftMessages.s.message += 'Later text'
    h.updateChoices(batch('new'), { 3: { kind: 'other', value: 'Unrelated new answer' } })
    h.wait.resolve()
    await sending
    assert.equal(h.frames[0].request_id, 'prepared')
    assert.equal(h.frames[0].async_questions.answers[0].value, 'Original answer')
    await h.state.applyAsyncQuestionSnapshot('s', { ...resolution('prepared'), batches: [batch('new')] })
    assert.equal(h.state.localState.draftMessages.s.message, 'Later text')
    assert.equal(h.state.localState.asyncQuestionDrafts.s.choices.new[3].value, 'Unrelated new answer')
    assert.equal(h.state.localState.asyncQuestionDrafts.s.choices.q1, undefined)
    assert.deepEqual(h.disk.sends, {})
    assert.deepEqual(h.forgotten, ['original'], 'The sent attachment record is forgotten after the dispatch')
    assert.deepEqual(h.frames[0].attachments, [{ bucket: 'b1', id: 'original' }])
})
it('composer staging failure releases its reservation without changing drafts or sending a frame', async () => {
    const h = delayedComposer(), before = structuredClone(h.state.localState.asyncQuestionDrafts.s)
    const sending = h.send()
    await tick()
    assert.deepEqual(h.state.getPendingAsyncQuestionIds('s'), ['q1', 'unanswered'])
    h.wait.reject(new Error('Staging failed'))
    await sending
    assert.deepEqual(h.state.getPendingAsyncQuestionIds('s'), [])
    assert.deepEqual(h.state.localState.asyncQuestionDrafts.s, before)
    assert.equal(h.state.localState.draftMessages.s.message, '  Extra text  ')
    assert.deepEqual(h.forgotten, [], 'The attachment records stay in the composer')
    assert.equal(h.records.length, 1)
    assert.equal(h.frames.length, 0)
    assert.deepEqual(h.disk.sends, {})
    assert.equal(h.errors.length, 1)
    h.updateChoices(batch(), { 3: { kind: 'other', value: 'Editable after failure' } })
    assert.equal(h.state.localState.asyncQuestionDrafts.s.choices.q1[3].value, 'Editable after failure')
})
it('external resolution during composer staging waits for its original request boundary', async () => {
    const h = delayedComposer(), sending = h.send()
    await tick()
    await h.state.applyAsyncQuestionSnapshot('s', resolution('another-browser'))
    assert.equal(h.state.localState.draftMessages.s.message, '  Extra text  ')
    assert.equal(h.state.localState.asyncQuestionDrafts.s.choices.q1[3].value, 'Original answer')
    h.wait.reject(new Error('Staging cancelled'))
    await sending
    await h.state.reconcileAsyncQuestionDraft('s')
    assert.match(h.state.localState.draftMessages.s.message, /Original answer/)
    assert.deepEqual(h.state.getPendingAsyncQuestionIds('s'), [])
    assert.equal(h.frames.length, 0)
})

async function retryCaptured(h, captured = outgoing()) {
    const source = readFileSync(new URL('../components/session/detail/items/FailedSendBanner.vue', import.meta.url), 'utf8')
    const start = source.indexOf('async function retry()'), end = source.indexOf('\n/** Put', start)
    const entry = { ...captured, requestId: 'failed', medias: [], code: 'send_failed' }
    h.state.localState.failedSends.s = { failed: entry }
    h.disk.sends.failed = structuredClone(entry)
    h.state.getSession = () => ({ provider: 'codex' })
    h.state.applyCreationSendMode = () => {}
    const deps = { getEntry: () => entry, snapshotAttachmentRefs,
        retryState: { value: questions.asyncQuestionRetryState(entry, ready()) }, props: { sessionId: 's', projectId: 'p' },
        generateUUID: () => 'retry', resizeMediasForSend: async values => values, getProviderHelpers: () => ({}),
        getProviderStore: () => ({}), mediasToSdkFormat: () => ({ images: [], documents: [] }),
        toast: { error: message => assert.fail(message) }, store: h.state }
    await new Function(...Object.keys(deps), `${source.slice(start, end)}; return retry`)(...Object.values(deps))()
}

for (const order of ['ack', 'snapshot', 'reload-before-ack', 'reload-after-ack']) {
    it(`Retry acceptance preserves changed and newly answered questions: ${order}`, async () => {
        let h = production()
        h.state.localState.draftMessages.s.message = 'New draft text'
        h.state.localState.asyncQuestionDrafts.s.choices.q1[3].value = 'New unsent answer'
        h.state.localState.asyncQuestionDrafts.s.choices.unanswered = { 3: { kind: 'other', value: 'Newly answered' } }
        h.state.localState.asyncQuestionDrafts.s.sourceBatches.unanswered = batch('unanswered')
        await retryCaptured(h)
        assert.equal(h.frames[0].async_questions.answers[0].value, 'Original answer')
        const update = { ...resolution('retry'), resolutions: {
            q1: { status: 'sent', request_id: 'retry' }, unanswered: { status: 'sent', request_id: 'retry' },
        } }
        if (order === 'ack' || order === 'reload-after-ack') await h.state.acknowledgeInflightSend('s', 'retry')
        if (order.startsWith('reload')) {
            const disk = structuredClone(h.disk)
            h = production()
            h.state.localState.draftMessages.s = disk.draft
            h.state.localState.asyncQuestionDrafts.s = disk.questions
            h.disk.sends = disk.sends
            for (const [id, entry] of Object.entries(disk.sends)) if (id === 'retry') h.pending.set(id, entry)
        }
        await h.state.applyAsyncQuestionSnapshot('s', update)
        await h.state.acknowledgeInflightSend('s', 'retry')
        await h.state.applyAsyncQuestionSnapshot('s', update)
        const text = h.state.localState.draftMessages.s.message
        assert.match(text, /New unsent answer/)
        assert.match(text, /Newly answered/)
        assert.ok(text.endsWith('New draft text'))
        assert.doesNotMatch(text, /Original answer/)
        assert.equal(text.split('Answers to your questions').length - 1, 1)
        assert.deepEqual(h.state.localState.asyncQuestionDrafts.s.choices, {})
    })
}

function recoveryBrowser(status, storedStatus = 'dispatched') {
    const reads = []
    const h = production({ fetch: async (url, options) => {
        reads.push([url, options])
        return { ok: true, json: async () => options?.method === 'POST'
            ? { requests: { local: status }, snapshot: status === 'accepted' ? resolution('local') : ready() }
            : ready() }
    } })
    h.state.sessions.s.project_id = 'p'
    h.state.localState.draftAliases = {}
    h.state.processStates = {}
    h.state.isEphemeralDiscarded = () => false
    h.state.dropDiscardedSendFailure = () => false
    h.state._materializeFailedSendItem = send => ({ failedSend: send })
    h.state.recomputeVisualItems = () => {}
    h.state.auditAllLoadedInflightSends = () => {}
    Object.assign(h.state, createSendFailureActions(h.pending, { deleteInflight: async id => { delete h.disk.sends[id] } }))
    Object.assign(h.state, storeActions({
        getAllInflightSends: async () => structuredClone(h.disk.sends), inflightSends: h.pending,
        deleteInflightSend: async id => { delete h.disk.sends[id] }, retainsAsyncQuestionSend: questions.retainsAsyncQuestionSend,
        INFLIGHT_SEND_TTL_MS: 10, isLaunchedEphemeral: () => false, PROCESS_STATE: { STARTING: 'starting' },
        saveInflightSend: async (id, entry) => { h.disk.sends[id] = structuredClone(entry) },
    }, ['hydrateInflightSends', '_applySendFailure']))
    h.disk.sends.local = { ...outgoing(), sessionId: 's', status: storedStatus, sentAt: 1 }
    return { ...h, reads }
}

for (const status of ['rejected', 'not_admitted', 'accepted', 'uncertain']) {
    it(`reload and reconnect reconcile durable ${status} without automatic resend`, async () => {
        const h = recoveryBrowser(status, status === 'not_admitted' ? 'staged' : 'dispatched')
        await h.state.hydrateInflightSends()
        await h.state.loadAsyncQuestions('p', 's')
        assert.ok(h.reads.some(([url, options]) => url.endsWith('/reconcile/') && options.method === 'POST'))
        assert.equal(h.frames.length, 0)
        if (status === 'accepted') {
            assert.equal(h.state.getFailedSend('s', 'local'), undefined)
            assert.equal(h.disk.sends.local, undefined)
        } else {
            const failed = h.state.getFailedSend('s', 'local')
            assert.equal(questions.asyncQuestionRetryState(failed, ready()).canRetry, status !== 'uncertain')
            assert.equal(await h.state.editAsyncQuestionFailure('s', 'local'), status !== 'uncertain')
            if (status !== 'uncertain') assert.equal(h.state.localState.asyncQuestionDrafts.s.choices.q1[3].value, 'Original answer')
        }
        assert.deepEqual(h.state.getPendingAsyncQuestionIds('s'), status === 'uncertain' ? ['q1', 'unanswered'] : [])
        await h.state.refreshActiveAsyncQuestions()
        assert.equal(h.frames.length, 0)
    })
}

for (const status of ['present', 'deleted', 'unknown']) {
    it(`offline session existence ${status} preserves hidden drafts or cleans authoritative deletion`, async () => {
        const h = production({
            fetch: async () => ({ ok: true, json: async () => ({ sessions: { s: status } }) }),
            remove: async () => { h.disk.questions = undefined; h.disk.sends = {} },
        })
        h.state.sessions = {} // Legacy saved records need no project identity.
        h.pending.set('local', { ...outgoing(), sessionId: 's', status: 'uncertain' })
        h.disk.sends.local = structuredClone(h.pending.get('local'))
        h.state.lockAsyncQuestionSend('s', 'local', outgoing().asyncQuestions)
        Object.assign(h.state, createSendFailureActions(h.pending, { deleteInflight: async () => {} }))
        await h.state.reconcileAsyncQuestionExistence()
        if (status === 'deleted') {
            assert.equal(h.state.localState.asyncQuestionDrafts.s, undefined)
            assert.equal(h.state.localState.asyncQuestionSnapshots.s, undefined)
            assert.equal(h.disk.questions, undefined)
            assert.deepEqual(h.disk.sends, {})
            assert.deepEqual(h.state.getPendingAsyncQuestionIds('s'), [])
            assert.equal(h.pending.size, 0)
            // A snapshot already in transit cannot recreate deleted state.
            await h.state.applyAsyncQuestionSnapshot('s', ready())
            assert.equal(h.state.localState.asyncQuestionSnapshots.s, undefined)
        } else {
            assert.equal(h.state.localState.asyncQuestionDrafts.s.choices.q1[3].value, 'Original answer')
            assert.deepEqual(h.state.getPendingAsyncQuestionIds('s'), ['q1', 'unanswered'])
            assert.equal(h.pending.size, 1)
        }
    })
}

it('deleted-session cleanup commits draft and retained sends together, preserving unrelated sends', async () => {
    const db = database()
    db.data.inflightSends = { local: { ...outgoing(), sessionId: 's' },
        other: { ...outgoing(), sessionId: 'other' }, ordinary: { sessionId: 's', text: 'Ordinary' } }
    const aborted = storage.deleteAsyncQuestionRecovery('s', db.open)
    await tick(); db.transactions[0].abort()
    await assert.rejects(aborted, /abort/)
    assert.ok(db.data.asyncQuestionDrafts.s)
    assert.ok(db.data.inflightSends.local)
    const deleted = storage.deleteAsyncQuestionRecovery('s', db.open)
    await tick()
    assert.ok(db.data.asyncQuestionDrafts.s)
    db.transactions[1].commit(); await deleted
    assert.equal(db.data.asyncQuestionDrafts.s, undefined)
    assert.equal(db.data.inflightSends.local, undefined)
    assert.ok(db.data.inflightSends.other)
    assert.ok(db.data.inflightSends.ordinary)
})

it('session_removed production action probes existence and preserves hidden session question drafts', async () => {
    const h = production({ fetch: async () => ({ ok: true, json: async () => ({ sessions: { s: 'present' } }) }) })
    let checked
    const probe = h.state.reconcileAsyncQuestionExistence.bind(h.state)
    h.state.reconcileAsyncQuestionExistence = ids => { checked = probe(ids); return checked }
    h.state.unloadSession = () => {}
    h.state.removeMruSession = () => {}
    Object.assign(h.state, storeActions({ dropsProcessStateOnRemoval: () => false }, ['removeSession']))
    h.state.removeSession('s')
    await checked
    assert.equal(h.state.sessions.s, undefined)
    assert.deepEqual(h.state.localState.asyncQuestionDrafts.s.choices, choices().choices)
})

for (const order of ['ack', 'snapshot']) it(`Retry consumes unchanged Other whitespace: ${order}`, async () => {
    const h = production()
    const draft = choices(); draft.choices.q1[3].value = '  Unchanged answer\n '
    const captured = questions.prepareAsyncQuestionSend({ snapshot: ready(), questionDraft: draft, rawText: 'Original text' })
    h.state.localState.asyncQuestionDrafts.s = structuredClone(draft)
    h.state.localState.draftMessages.s.message = 'New draft'
    await retryCaptured(h, captured)
    if (order === 'ack') await h.state.acknowledgeInflightSend('s', 'retry')
    await h.state.applyAsyncQuestionSnapshot('s', resolution('retry'))
    assert.equal(h.state.localState.draftMessages.s.message, 'New draft')
    assert.equal(h.state.localState.asyncQuestionDrafts.s.choices.q1, undefined)
})

// ── A failed send carrying BOTH question answers AND staged attachment refs ──────────────────────

const withAttachments = () => ({ ...outgoing(), attachments: snapshotAttachments([record('a1'), record('a2', 'b2')]), medias: [] })

it('the stored snapshot of a send keeps the answers and the attachment refs (metadata only)', () => {
    const stored = storage.inflightSnapshotRecord({
        ...withAttachments(), sessionId: 's',
        attachments: [{ ...record('a1'), previewUrl: 'blob:x', data: 'must-not-persist' }],
    })
    assert.deepEqual(stored.attachments, [{ bucket: 'b1', id: 'a1', name: 'a1.png', size: 3, mimeType: 'image/png', kind: 'image' }])
    assert.deepEqual(stored.asyncQuestions.batch_ids, ['q1', 'unanswered'])
    assert.equal(stored.rawText, '  Extra text  ')
    assert.equal(stored.images, undefined)
})

it('Retry of a failed send with answers and refs re-sends both under a fresh identity and releases nothing', async () => {
    const source = readFileSync(new URL('../components/session/detail/items/FailedSendBanner.vue', import.meta.url), 'utf8')
    const start = source.indexOf('async function retry()'), end = source.indexOf('\n/** Put', start)
    const entry = { ...withAttachments(), requestId: 'failed', code: 'send_failed' }
    let sent
    const deps = {
        getEntry: () => entry, snapshotAttachmentRefs, retryState: { value: questions.asyncQuestionRetryState(entry, ready()) },
        props: { sessionId: 's', projectId: 'p' }, generateUUID: () => 'fresh',
        resizeMediasForSend: async () => assert.fail('Refs are never resized'), getProviderHelpers: () => ({}), getProviderStore: () => ({}),
        mediasToSdkFormat: () => ({ images: [], documents: [] }),
        toast: { error: message => assert.fail(message) },
        sendWsMessage: () => assert.fail('A question send uses the staged action'),
        store: { getSession: () => ({ provider: 'codex' }), applyCreationSendMode() {},
            releaseAttachments: () => assert.fail('Retry never releases the refs it re-sends'),
            sendAsyncQuestionMessage: async (...args) => { sent = args } },
    }
    await new Function(...Object.keys(deps), `${source.slice(start, end)}; return retry`)(...Object.values(deps))()
    assert.deepEqual(sent[3].attachments, [{ bucket: 'b1', id: 'a1' }, { bucket: 'b2', id: 'a2' }])
    assert.equal(sent[3].images, undefined)
    assert.equal(sent[3].text, entry.rawText)
    assert.deepEqual(sent[3].async_questions, entry.asyncQuestions)
    assert.deepEqual(sent[4].attachments.map(item => item.id), ['a1', 'a2'])
    assert.deepEqual(sent[4].medias, [])
    assert.equal(sent[5].retryRequestId, 'failed')
})

it('Edit of a failed send with answers and refs puts the refs back first, never releases, and keeps the failure on error', async () => {
    const h = production()
    const entry = { ...withAttachments(), requestId: 'failed', code: 'send_failed', sessionId: 's' }
    h.state.localState.failedSends.s = { failed: entry }
    h.disk.sends.failed = structuredClone(entry)
    const calls = []
    h.state.restoreDraftAttachmentRefs = async (sessionId, attachments) => { calls.push(['restore', sessionId, attachments.map(a => a.id)]) }
    h.state.releaseAttachments = () => assert.fail('Edit never releases the refs')
    assert.equal(await h.state.editAsyncQuestionFailure('s', 'failed'), true)
    assert.deepEqual(calls, [['restore', 's', ['a1', 'a2']]])
    assert.equal(h.state.localState.asyncQuestionDrafts.s.choices.q1[3].value, 'Original answer')
    assert.equal(h.state.getFailedSend('s', 'failed'), undefined)
    assert.equal(h.disk.sends.failed, undefined)

    const again = production()
    again.state.localState.failedSends.s = { failed: structuredClone(entry) }
    again.disk.sends.failed = structuredClone(entry)
    const boom = new Error('IndexedDB write failed')
    again.state.restoreDraftAttachmentRefs = async () => { throw boom }
    await assert.rejects(again.state.editAsyncQuestionFailure('s', 'failed'), failure => failure === boom)
    assert.ok(again.state.getFailedSend('s', 'failed'), 'The failed send stays available')
    assert.ok(again.disk.sends.failed, 'Its snapshot stays on disk')
})

it('Delete of a failed send with answers and refs releases the refs exactly once (the ownership matrix)', () => {
    const source = readFileSync(new URL('../components/session/detail/items/FailedSendBanner.vue', import.meta.url), 'utf8')
    const start = source.indexOf('function discard()'), end = source.indexOf('\n}\n', start)
    const entry = { ...withAttachments(), requestId: 'failed', code: 'send_failed' }, removed = [], released = []
    const deps = {
        actionInProgress: { value: false }, uncertain: { value: false }, getEntry: () => entry, snapshotAttachmentRefs,
        props: { sessionId: 's' },
        store: { removeFailedSend: (...args) => removed.push(args), releaseAttachments: refs => released.push(refs) },
    }
    new Function(...Object.keys(deps), `${source.slice(start, end + 3)}; return discard`)(...Object.values(deps))()
    assert.deepEqual(removed, [['s', 'failed']])
    assert.deepEqual(released, [[{ bucket: 'b1', id: 'a1' }, { bucket: 'b2', id: 'a2' }]])
})

it('an uncertain failed send keeps its refs: Delete and Edit are refused', () => {
    const source = readFileSync(new URL('../components/session/detail/items/FailedSendBanner.vue', import.meta.url), 'utf8')
    const start = source.indexOf('function discard()'), end = source.indexOf('\n}\n', start)
    const entry = { ...withAttachments(), requestId: 'failed', code: 'send_uncertain' }
    const deps = {
        actionInProgress: { value: false }, uncertain: { value: true }, getEntry: () => entry, snapshotAttachmentRefs,
        props: { sessionId: 's' },
        store: { removeFailedSend: () => assert.fail('Uncertain'), releaseAttachments: () => assert.fail('Uncertain') },
    }
    new Function(...Object.keys(deps), `${source.slice(start, end + 3)}; return discard`)(...Object.values(deps))()
})
