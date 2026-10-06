import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { it } from 'node:test'
import { reactive } from 'vue'
import * as storage from './draftStorage.js'
import { createAsyncQuestionActions } from './asyncQuestionState.js'

const png = {
    id: 'png', sessionId: 's', name: 'pixel.png', type: 'image', mimeType: 'image/png',
    data: 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+j8V8AAAAASUVORK5CYII=',
    createdAt: 123,
}
const batch = { item_id: 'q1', status: 'ready', questions: [{ index: 0, title: 'Choose?', options: ['Alpha'] }] }
const captured = { choices: { q1: { 0: { kind: 'option', value: 'Alpha' } } }, sourceBatches: { q1: batch }, recoveredIds: [] }

// Node has no IndexedDB. Clone at put(), stage writes, and expose commit/abort
// separately so a proxy rejection and an uncommitted recovery remain observable.
function database() {
    const data = { draftMedias: {}, draftMessages: {}, asyncQuestionDrafts: {}, inflightSends: {} }
    const transactions = []
    const db = { transaction(names, mode) {
        const changes = [], stores = Array.isArray(names) ? names : [names]
        let stopped = false
        const tx = {
            names: stores, mode,
            objectStore: name => ({
                put(value, key = value.id) {
                    const record = structuredClone(value)
                    const request = {}
                    if (name === 'draftMedias' && db.mediaError) {
                        queueMicrotask(() => {
                            stopped = true
                            request.error = db.mediaError
                            request.onerror?.()
                        })
                    } else {
                        changes.push([name, key, record])
                        queueMicrotask(() => request.onsuccess?.())
                    }
                    return request
                },
                delete(key) { changes.push([name, key]); return {} },
                get(key) {
                    const request = {}
                    queueMicrotask(() => { request.result = structuredClone(data[name][key]); request.onsuccess?.() })
                    return request
                },
            }),
            commit() {
                if (stopped) return
                stopped = true
                for (const [name, key, value] of changes) {
                    if (value === undefined) delete data[name][key]
                    else data[name][key] = value
                }
                tx.oncomplete?.()
            },
            abort() { stopped = true; tx.onabort?.() },
        }
        transactions.push(tx)
        if (!stores.includes('inflightSends')) queueMicrotask(() => queueMicrotask(() => tx.commit()))
        return tx
    } }
    return { data, transactions, open: async () => db, connection: db }
}

const db = database()
globalThis.indexedDB = { open() {
    const request = {}
    queueMicrotask(() => { request.result = db.connection; request.onsuccess?.() })
    return request
} }

const dataSource = readFileSync(new URL('../stores/data.js', import.meta.url), 'utf8')
const start = dataSource.indexOf('        async restoreDraftAttachments(')
const end = dataSource.indexOf('\n        // Expanded groups actions', start)
const attachmentActions = new Function('getDraftMessage', 'saveDraftMedia', 'saveDraftMessage',
    `return {${dataSource.slice(start, end)}}`)(storage.getDraftMessage, storage.saveDraftMedia, storage.saveDraftMessage)

function recovery() {
    for (const store of Object.values(db.data)) for (const key of Object.keys(store)) delete store[key]
    db.transactions.length = 0
    const entry = {
        sessionId: 's', requestId: 'failed', code: 'async_questions_not_admitted', rawText: '  Extra text  ',
        text: '::: Answers to your questions\n\n**Question:** Choose?\n\n**Answer:** Alpha\n\n:::\n\n  Extra text  ',
        questionDraft: captured, asyncQuestions: { batch_ids: ['q1'], answers: [{ item_id: 'q1', question_index: 0, answer: 'Alpha' }] },
        medias: [png],
    }
    db.data.inflightSends.failed = structuredClone(entry)
    db.data.draftMessages.s = { message: 'Later' }
    db.data.asyncQuestionDrafts.s = { choices: {}, sourceBatches: {}, recoveredIds: [] }
    const state = {
        localState: reactive({ draftMessages: { s: structuredClone(db.data.draftMessages.s) },
            asyncQuestionDrafts: { s: structuredClone(db.data.asyncQuestionDrafts.s) },
            asyncQuestionSnapshots: { s: { batches: [batch], widget_enabled: true, resolutions: {} } },
            draftAppendSignals: {}, attachments: {}, failedSends: { s: { failed: entry } } }),
        getFailedSend(sessionId, id) { return this.localState.failedSends[sessionId]?.[id] },
        removeFailedSend(sessionId, id) { delete this.localState.failedSends[sessionId][id] },
        ...attachmentActions,
        ...createAsyncQuestionActions({ cancelDraftSave() {}, pendingSends: () => ({}),
            recover: (id, draft, questions) => storage.saveAsyncQuestionRecovery(id, draft, questions, db.open),
            restoreStaged: (id, sessionId, draft, questions) => storage.restoreStagedAsyncQuestionSend(id, sessionId, draft, questions, db.open),
        }),
    }
    return state
}

const tick = async () => { for (let i = 0; i < 40; i++) await Promise.resolve() }

it('saves reactive PNG media with all encoded data and metadata', async () => {
    const media = reactive({ ...png, metadata: { source: 'clipboard' } })
    await storage.saveDraftMedia(media)
    assert.deepEqual(await storage.getDraftMedia('png'), { ...png, metadata: { source: 'clipboard' } })
})

it('failed Edit restores reactive PNG and commits text, choices, and snapshot removal together', async () => {
    const state = recovery()
    let finished = false
    const result = state.editAsyncQuestionFailure('s', 'failed').then(value => { finished = true; return value })
    result.catch(() => {})
    await tick()
    assert.deepEqual(await storage.getDraftMedia('png'), png)
    assert.equal(finished, false)
    assert.equal(db.data.draftMessages.s.message, 'Later')
    assert.deepEqual(db.data.draftMessages.s.mediaIds, ['png'])
    assert.deepEqual(db.data.asyncQuestionDrafts.s.choices, {})
    assert.ok(db.data.inflightSends.failed)
    assert.ok(state.getFailedSend('s', 'failed'))
    const restoration = db.transactions.find(tx => tx.names.includes('inflightSends'))
    assert.deepEqual(restoration.names, ['draftMessages', 'asyncQuestionDrafts', 'inflightSends'])
    restoration.commit()
    assert.equal(await result, true)
    await tick()
    assert.equal(db.data.draftMessages.s.message, '  Extra text  \n\nLater')
    assert.deepEqual(db.data.draftMessages.s.mediaIds, ['png'])
    assert.deepEqual(db.data.asyncQuestionDrafts.s.choices, captured.choices)
    assert.equal(db.data.inflightSends.failed, undefined)
    assert.equal(state.getFailedSend('s', 'failed'), undefined)
    assert.deepEqual({ ...state.localState.attachments.s.get('png') }, png)
})

it('aborted PNG recovery retains the failed snapshot and current text and choices', async () => {
    const state = recovery()
    const result = state.editAsyncQuestionFailure('s', 'failed')
    const rejected = assert.rejects(result, /Draft transaction aborted/)
    rejected.catch(() => {})
    await tick()
    const restoration = db.transactions.find(tx => tx.names.includes('inflightSends'))
    restoration.abort()
    await rejected
    assert.equal(db.data.draftMessages.s.message, 'Later')
    assert.deepEqual(db.data.asyncQuestionDrafts.s.choices, {})
    assert.ok(db.data.inflightSends.failed)
    assert.ok(state.getFailedSend('s', 'failed'))
    assert.equal(state.localState.draftMessages.s.message, 'Later')
    assert.deepEqual(state.localState.asyncQuestionDrafts.s.choices, {})
})

it('strict media write errors keep failed Edit available without restoring text or choices', async () => {
    const state = recovery()
    const error = new DOMException('Storage quota exceeded', 'QuotaExceededError')
    db.connection.mediaError = error
    try {
        await assert.rejects(state.editAsyncQuestionFailure('s', 'failed'), failure => failure === error)
        assert.equal(await storage.getDraftMedia('png'), null)
        assert.equal(db.data.draftMessages.s.message, 'Later')
        assert.deepEqual(db.data.asyncQuestionDrafts.s.choices, {})
        assert.ok(db.data.inflightSends.failed)
        assert.ok(state.getFailedSend('s', 'failed'))
        assert.equal(state.localState.draftMessages.s.message, 'Later')
        assert.deepEqual(state.localState.asyncQuestionDrafts.s.choices, {})
        assert.equal(state.localState.attachments.s.size, 0)
    } finally {
        delete db.connection.mediaError
    }
})
