// Composer attachments (spec 2026-10-03 §9.1, §9.2): the pure ref/state
// helpers, the staging HTTP helpers, and the attachment actions the data store
// delegates to. The actions run on top of the REAL upload controller, built
// with fake dependencies (clock, timers, apiFetch, tus `Upload`, toasts), so
// every §9.2 transition goes through the controller code the app uses.

import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { reactive } from 'vue'
import {
    COMPOSER_PANEL,
    attachmentChipItem,
    attachmentKindIcon,
    attachmentPayloadFields,
    canSendAttachments,
    createComposerAttachments,
    formatAttachmentSize,
    getDisplayKind,
    mapAttachmentStatus,
    readTextPreview,
    releaseRef,
    sendComposerMessage,
    shouldAcceptCompletion,
    snapshotAttachments,
    statusRefs,
    toAttachmentRefs,
    touchRefs,
    uploadDisplayState,
} from './composerAttachments.js'
import { createUploadsController } from './uploads/controller.js'
import { RETRY_DELAYS } from './uploads/transport.js'

const TAB = '11111111-1111-4111-8111-111111111111'
const OTHER_TAB = '22222222-2222-4222-8222-222222222222'
const START = 1_800_000_000_000

// ── Fakes ────────────────────────────────────────────────────────────────────

async function flush() {
    for (let i = 0; i < 30; i++) await new Promise(resolve => setImmediate(resolve))
}

function createClock() {
    let t = START
    let seq = 0
    const timers = new Map()
    return {
        now: () => t,
        setTimeout: (fn, ms) => {
            const id = ++seq
            timers.set(id, { fn, at: t + (ms || 0) })
            return id
        },
        clearTimeout: id => { timers.delete(id) },
        async advance(ms) {
            const end = t + ms
            for (;;) {
                await flush()
                let next = null
                for (const [id, timer] of timers) {
                    if (timer.at <= end && (!next || timer.at < next[1].at)) next = [id, timer]
                }
                if (!next) break
                timers.delete(next[0])
                t = next[1].at
                next[1].fn()
            }
            t = end
            await flush()
        },
    }
}

function respond(status, body = null, { upload = true } = {}) {
    const headers = upload ? { 'X-Twicc-Upload': '1' } : {}
    if (body === null || status === 204) return new Response(null, { status, headers })
    return new Response(JSON.stringify(body), { status, headers: { ...headers, 'Content-Type': 'application/json' } })
}

function tusError(status, { upload = false } = {}) {
    const err = new Error('tus error')
    err.originalRequest = {}
    if (status !== null) {
        err.originalResponse = {
            getStatus: () => status,
            getHeader: name => (upload && name === 'X-Twicc-Upload' ? '1' : null),
            getBody: () => null,
        }
    }
    return err
}

let serverSeq = 0
function makeServerRecord(fields = {}) {
    serverSeq += 1
    return {
        id: serverSeq.toString(16).padStart(32, '0'),
        client_id: `${OTHER_TAB}:0000000000000000`,
        state: 'active',
        version: 1,
        filename: 'f.txt',
        target_dir: '/staging/b/i/file',
        size: 5,
        offset: 0,
        origin: { panel: 'composer', key: 'b/i' },
        fingerprint: 'fp',
        final_path: null,
        error: null,
        created_at: new Date(START).toISOString(),
        updated_at: new Date(START).toISOString(),
        ...fields,
    }
}

function next(record, fields = {}) {
    return { ...record, version: record.version + 1, ...fields }
}

/**
 * The real upload controller and the composer actions, over a fake backend.
 * `log` records every outside effect in order (storage writes, requests).
 */
function createHarness(options = {}) {
    const clock = createClock()
    const log = []
    const requests = []
    const server = {
        records: new Map(),
        statuses: new Map(), // "<bucket>/<id>" → status item
        listed: [], // records answered by GET /api/uploads/
        createUpload: req => {
            const existing = server.records.get(req.body.client_id)
            if (existing) return respond(200, existing)
            const record = makeServerRecord({
                client_id: req.body.client_id,
                filename: req.body.filename,
                size: req.body.size,
                origin: req.body.origin,
                fingerprint: req.body.fingerprint,
            })
            server.records.set(req.body.client_id, record)
            return respond(201, record)
        },
        status: req => respond(200, {
            statuses: req.body.refs.map(ref => ({
                bucket: ref.bucket,
                id: ref.id,
                ...(server.statuses.get(`${ref.bucket}/${ref.id}`) || { state: 'missing' }),
            })),
        }),
        gate: null, // a promise every status answer waits for
    }
    const apiFetch = async (url, init = {}) => {
        const req = { url, method: init.method || 'GET', body: init.body ? JSON.parse(init.body) : null }
        requests.push(req)
        log.push(`${req.method} ${url}`)
        if (url === '/api/uploads/' && req.method === 'POST') return server.createUpload(req)
        if (url === '/api/uploads/' && req.method === 'GET') {
            return respond(200, { uploads: server.listed, now: new Date(clock.now()).toISOString() })
        }
        if (url.startsWith('/api/uploads/')) return respond(204)
        if (url === '/api/composer-attachments/status/') {
            if (server.gate) await server.gate
            return server.status(req)
        }
        if (url.startsWith('/api/composer-attachments/')) return respond(204, null, { upload: false })
        throw new Error(`unexpected ${req.method} ${url}`)
    }
    const tusUploads = []
    const createTusUpload = (file, options) => {
        const upload = { file, options, started: 0, aborted: 0, start() { this.started += 1 }, abort() { this.aborted += 1 } }
        tusUploads.push(upload)
        return upload
    }
    const toasts = []
    const toast = {
        success: (message, opts = {}) => toasts.push({ type: 'success', message, ...opts }),
        error: (message, opts = {}) => toasts.push({ type: 'error', message, ...opts }),
    }
    let hex = 0
    const controller = createUploadsController({
        apiFetch,
        createTusUpload,
        toast,
        now: clock.now,
        setTimeout: clock.setTimeout,
        clearTimeout: clock.clearTimeout,
        tabId: TAB,
        randomHex: n => (hex++).toString(16).padStart(n, '0'),
        isAuthenticated: () => true,
        onUnauthorized: () => {},
        isAppNavigation: () => false,
        wakeLock: null,
        isVisible: () => true,
        events: null,
    })
    const rows = new Map()
    const legacyRows = new Set()
    const storage = {
        async saveDraftAttachment(record) {
            log.push(`save ${record.id}`)
            rows.set(record.id, structuredClone(record))
        },
        async deleteDraftAttachment(id) {
            log.push(`delete ${id}`)
            rows.delete(id)
        },
        async deleteLegacyMedia(id) {
            log.push(`delete-legacy ${id}`)
            legacyRows.delete(id)
        },
    }
    let uuidSeq = 0
    const records = reactive({})
    const runtime = reactive({})
    const actions = createComposerAttachments({
        records,
        runtime,
        storage,
        uploads: controller,
        fetch: apiFetch,
        uuid: () => {
            uuidSeq += 1
            return `00000000-0000-4000-8000-${uuidSeq.toString(16).padStart(12, '0')}`
        },
        randomHex: n => `c${(hex++).toString(16)}`.padStart(n, '0'),
        objectUrls: options.objectUrls,
        previewUrlsInUse: options.previewUrlsInUse,
    })
    return {
        clock, log, requests, server, tusUploads, toasts, controller, rows, legacyRows, records, runtime, actions,
        posts: () => requests.filter(r => r.method === 'POST' && r.url === '/api/uploads/'),
        statusCalls: () => requests.filter(r => r.url === '/api/composer-attachments/status/'),
        entry: clientId => controller.entries.get(clientId),
        state: id => runtime[id]?.state,
        async add(sessionId = 's1', name = 'a.txt', content = 'hello', type = 'text/plain') {
            const record = await actions.addAttachment(sessionId, new File([content], name, { type }))
            await flush()
            return record
        },
    }
}

// ── Pure helpers ─────────────────────────────────────────────────────────────

test('mapAttachmentStatus: promoted/ready → ready, uploading, missing; a previous failed stays failed', () => {
    assert.equal(mapAttachmentStatus({ state: 'promoted' }).state, 'ready')
    assert.equal(mapAttachmentStatus({ state: 'ready' }).state, 'ready')
    assert.equal(mapAttachmentStatus({ state: 'uploading', client_id: 'x', offset: 1 }).state, 'uploading')
    assert.equal(mapAttachmentStatus({ state: 'missing' }).state, 'missing')
    // Reconnect: a refused creation leaves an entry without ready.json → missing; the chip stays failed.
    assert.equal(mapAttachmentStatus({ state: 'missing' }, 'failed').state, 'failed')
    assert.equal(mapAttachmentStatus({ state: 'missing' }, 'ready').state, 'missing')
    assert.equal(mapAttachmentStatus({ state: 'ready' }, 'failed').state, 'ready')
    assert.deepEqual(Object.keys(mapAttachmentStatus({ state: 'ready' })), ['state'])
})

test('shouldAcceptCompletion: only the chip\'s current client id', () => {
    assert.equal(shouldAcceptCompletion('attempt-new', 'attempt-old'), false)
    assert.equal(shouldAcceptCompletion('attempt-new', 'attempt-new'), true)
    assert.equal(shouldAcceptCompletion(null, 'attempt-old'), false)
    assert.equal(shouldAcceptCompletion(null, null), false)
})

test('toAttachmentRefs: position order, the record bucket (never the current session id), only bucket and id', () => {
    const oldBucket = 'draft-session-id'
    const firstId = '00000000-0000-4000-8000-000000000001'
    const secondId = '00000000-0000-4000-8000-000000000002'
    // The draft was bound to its canonical id: sessionId changed, the bucket did not.
    const records = [
        { id: secondId, sessionId: 'canonical-id', bucket: oldBucket, position: 4, name: 'b', size: 1, mimeType: '', kind: 'other' },
        { id: firstId, sessionId: 'canonical-id', bucket: oldBucket, position: 1, name: 'a', size: 1, mimeType: '', kind: 'other' },
    ]
    assert.deepEqual(toAttachmentRefs(records), [{ bucket: oldBucket, id: firstId }, { bucket: oldBucket, id: secondId }])
    assert.deepEqual(toAttachmentRefs([]), [])
})

test('getDisplayKind: six kinds from MIME type, then extension', () => {
    const f = (name, type = '') => ({ name, type })
    assert.equal(getDisplayKind(f('a.png', 'image/png')), 'image')
    assert.equal(getDisplayKind(f('photo.JPG')), 'image')
    assert.equal(getDisplayKind(f('doc.pdf', 'application/pdf')), 'PDF')
    assert.equal(getDisplayKind(f('doc.PDF')), 'PDF')
    assert.equal(getDisplayKind(f('notes.txt', 'text/plain')), 'text')
    assert.equal(getDisplayKind(f('data.json', 'application/json')), 'text')
    assert.equal(getDisplayKind(f('script.py')), 'text')
    assert.equal(getDisplayKind(f('icon.svg', 'image/svg+xml')), 'text')
    assert.equal(getDisplayKind(f('clip.mp4', 'video/mp4')), 'video')
    assert.equal(getDisplayKind(f('clip.mkv')), 'video')
    assert.equal(getDisplayKind(f('song.mp3', 'audio/mpeg')), 'audio')
    assert.equal(getDisplayKind(f('song.flac')), 'audio')
    assert.equal(getDisplayKind(f('archive.zip', 'application/zip')), 'other')
    assert.equal(getDisplayKind(f('noext')), 'other')
})

test('uploadDisplayState: removal only fails a seen entry; an error pause without record and a stalled entry fail', () => {
    assert.equal(uploadDisplayState(null, { seen: false, stalled: false }), 'uploading')
    assert.equal(uploadDisplayState(null, { seen: true, stalled: false }), 'failed')
    assert.equal(uploadDisplayState({ localState: 'paused', pauseReason: 'network', server: null }, { seen: true, stalled: false }), 'uploading')
    assert.equal(uploadDisplayState({ localState: 'paused', pauseReason: 'error', server: null }, { seen: true, stalled: false }), 'failed')
    assert.equal(uploadDisplayState({ localState: 'paused', pauseReason: 'error', server: { state: 'active' } }, { seen: true, stalled: false }), 'uploading')
    assert.equal(uploadDisplayState({ localState: null, server: { state: 'active' } }, { seen: true, stalled: true }), 'failed')
})

// ── HTTP helpers ─────────────────────────────────────────────────────────────

test('statusRefs posts the refs and returns the statuses in order; no request for no refs; throws on a failed answer', async () => {
    const calls = []
    const answer = { statuses: [{ bucket: 'b', id: '2', state: 'ready' }, { bucket: 'b', id: '1', state: 'uploading', client_id: 'c', offset: 3 }] }
    const fetchFake = async (url, init) => {
        calls.push({ url, init })
        return new Response(JSON.stringify(answer), { status: 200 })
    }
    const refs = [{ bucket: 'b', id: '2' }, { bucket: 'b', id: '1' }]
    assert.deepEqual(await statusRefs(refs, fetchFake), answer.statuses)
    assert.equal(calls[0].url, '/api/composer-attachments/status/')
    assert.equal(calls[0].init.method, 'POST')
    assert.equal(calls[0].init.headers['Content-Type'], 'application/json')
    assert.deepEqual(JSON.parse(calls[0].init.body), { refs })
    assert.deepEqual(await statusRefs([], fetchFake), [])
    assert.equal(calls.length, 1)
    await assert.rejects(statusRefs(refs, async () => new Response('{}', { status: 500 })))
    await assert.rejects(statusRefs(refs, async () => new Response('{"statuses": 1}', { status: 200 })))
})

test('touchRefs: holder draft or snapshot; no request for no refs; releaseRef: DELETE of one entry', async () => {
    const calls = []
    const fetchFake = async (url, init) => {
        calls.push({ url, method: init.method, body: init.body ? JSON.parse(init.body) : null })
        return new Response(null, { status: 204 })
    }
    await touchRefs([{ bucket: 'b', id: '1' }], 'draft', fetchFake)
    await touchRefs([{ bucket: 'b', id: '2' }], 'snapshot', fetchFake)
    await touchRefs([], 'draft', fetchFake)
    await releaseRef({ bucket: 'b x', id: '3' }, fetchFake)
    assert.deepEqual(calls, [
        { url: '/api/composer-attachments/touch/', method: 'POST', body: { refs: [{ bucket: 'b', id: '1' }], holder: 'draft' } },
        { url: '/api/composer-attachments/touch/', method: 'POST', body: { refs: [{ bucket: 'b', id: '2' }], holder: 'snapshot' } },
        { url: '/api/composer-attachments/b%20x/3/', method: 'DELETE', body: null },
    ])
    await assert.rejects(touchRefs([{ bucket: 'b', id: '1' }], 'other', fetchFake))
})

// ── addAttachment ────────────────────────────────────────────────────────────

test('addAttachment persists the record before the upload, with no File, encoded data or progress', async () => {
    const h = createHarness()
    const record = await h.add('s1', 'a.txt', 'hello', 'text/plain')
    const second = await h.add('s1', 'b.png', 'png', 'image/png')
    assert.deepEqual(Object.keys(h.rows.get(record.id)).sort(),
        ['bucket', 'id', 'kind', 'mimeType', 'name', 'position', 'sessionId', 'size'])
    assert.deepEqual(h.rows.get(record.id), {
        id: record.id, sessionId: 's1', bucket: 's1', position: 0, name: 'a.txt', size: 5, mimeType: 'text/plain', kind: 'text',
    })
    assert.equal(h.rows.get(second.id).position, 1)
    assert.equal(h.rows.get(second.id).kind, 'image')
    // The record is saved before its creation POST.
    assert.ok(h.log.indexOf(`save ${record.id}`) < h.log.indexOf('POST /api/uploads/'))
    const [post] = h.posts()
    assert.deepEqual(post.body.origin, { panel: COMPOSER_PANEL, key: `s1/${record.id}` })
    assert.equal(Object.hasOwn(post.body, 'target_dir'), false)
    assert.equal(Object.hasOwn(post.body, 'root'), false)
    assert.ok(post.body.client_id.startsWith(`${TAB}:`))
    assert.equal(h.runtime[record.id].clientId, post.body.client_id)
    assert.equal(h.state(record.id), 'uploading')
    assert.equal(h.actions.getFile(record.id).name, 'a.txt')
    assert.deepEqual(h.actions.getRecords('s1').map(r => r.id), [record.id, second.id])
})

test('an IndexedDB failure keeps nothing and starts no upload', async () => {
    const h = createHarness()
    h.actions.dispose()
    const fail = new Error('quota')
    const records = reactive({})
    const runtime = reactive({})
    const actions = createComposerAttachments({
        records,
        runtime,
        storage: { saveDraftAttachment: async () => { throw fail }, deleteDraftAttachment: async () => {}, deleteLegacyMedia: async () => {} },
        uploads: h.controller,
        fetch: async () => respond(204),
        uuid: () => '00000000-0000-4000-8000-0000000000ff',
        randomHex: () => '0'.repeat(16),
    })
    await assert.rejects(actions.addAttachment('s1', new File(['x'], 'x')), fail)
    assert.deepEqual(actions.getRecords('s1'), [])
    assert.equal(runtime['00000000-0000-4000-8000-0000000000ff'], undefined)
    assert.equal(actions.getFile('00000000-0000-4000-8000-0000000000ff'), null)
    assert.equal(h.posts().length, 0)
})

// ── §9.2 upload states ───────────────────────────────────────────────────────

test('own completed → ready; progress follows the entry', async () => {
    const h = createHarness()
    const record = await h.add()
    const clientId = h.runtime[record.id].clientId
    const entry = h.entry(clientId)
    entry.sentBytes = 2
    await flush()
    assert.equal(h.runtime[record.id].progress, 40)
    h.controller.applyServerRecord(next(entry.server, { state: 'completed', offset: 5 }), { fromWs: true })
    assert.equal(h.state(record.id), 'ready')
    assert.equal(h.runtime[record.id].progress, 100)
    // No completion toast for the composer.
    assert.equal(h.toasts.length, 0)
    // The tus tombstone expiry later removes nothing more: still ready.
    await h.controller.reconcile()
    assert.equal(h.state(record.id), 'ready')
})

test('chip Retry: cancels the old local attempt, new client id into the same entry; an old completion is ignored', async () => {
    const h = createHarness()
    const record = await h.add()
    const oldClientId = h.runtime[record.id].clientId
    const oldEntry = h.entry(oldClientId)
    const oldRecord = oldEntry.server
    h.controller.applyServerRecord(next(oldRecord, { state: 'failed', error: 'boom' }), { fromWs: true })
    await flush()
    assert.equal(h.state(record.id), 'failed')
    assert.equal(h.runtime[record.id].retryable, true)
    assert.equal(await h.actions.retryAttachment(record.id), true)
    await flush()
    const newClientId = h.runtime[record.id].clientId
    assert.notEqual(newClientId, oldClientId)
    assert.ok(newClientId.startsWith(`${TAB}:`))
    const posts = h.posts()
    assert.equal(posts.length, 2)
    assert.equal(posts[1].body.client_id, newClientId)
    assert.deepEqual(posts[1].body.origin, posts[0].body.origin)
    assert.equal(h.state(record.id), 'uploading')
    // A late completion of the old attempt (settled by the server) changes nothing.
    h.controller.applyServerRecord(next(oldRecord, { version: 9, state: 'completed', offset: 5 }), { fromWs: true })
    assert.equal(h.state(record.id), 'uploading')
    const newEntry = h.entry(newClientId)
    h.controller.applyServerRecord(next(newEntry.server, { state: 'completed', offset: 5 }), { fromWs: true })
    assert.equal(h.state(record.id), 'ready')
})

test('chip Retry cancels a creation paused on the network so autoRestart never re-sends the old client id', async () => {
    const h = createHarness()
    let calls = 0
    const create = h.server.createUpload
    h.server.createUpload = req => {
        calls += 1
        if (calls <= RETRY_DELAYS.length + 1) throw new TypeError('offline')
        return create(req)
    }
    const record = await h.add()
    for (const delay of RETRY_DELAYS) await h.clock.advance(delay)
    const oldClientId = h.runtime[record.id].clientId
    assert.equal(h.entry(oldClientId).pauseReason, 'network')
    // A network pause stays uploading.
    assert.equal(h.state(record.id), 'uploading')
    // Force the failed state the way a terminal failure would, then Retry.
    h.runtime[record.id].state = 'failed'
    await h.actions.retryAttachment(record.id)
    await flush()
    assert.equal(h.entry(oldClientId), undefined)
    h.controller.autoRestart()
    await flush()
    const clientIds = h.posts().map(p => p.body.client_id)
    assert.equal(clientIds.at(-1), h.runtime[record.id].clientId)
    assert.equal(clientIds.filter(c => c === oldClientId).length, RETRY_DELAYS.length + 1)
})

test('terminal failure → failed with Retry (File in memory) and Remove', async () => {
    const h = createHarness()
    const record = await h.add()
    const entry = h.entry(h.runtime[record.id].clientId)
    h.controller.applyServerRecord(next(entry.server, { state: 'failed', error: 'expired' }), { fromWs: true })
    await flush()
    assert.equal(h.state(record.id), 'failed')
    assert.equal(h.runtime[record.id].retryable, true)
})

test('upload entry removed before completion → failed; after completion → unchanged', async () => {
    const h = createHarness()
    const record = await h.add()
    // reconcile() no longer lists the upload: the controller drops the entry.
    await h.clock.advance(1)
    await h.controller.reconcile()
    assert.equal(h.entry(h.runtime[record.id].clientId), undefined)
    assert.equal(h.state(record.id), 'failed')

    const h2 = createHarness()
    const r2 = await h2.add()
    const e2 = h2.entry(h2.runtime[r2.id].clientId)
    h2.controller.applyServerRecord(next(e2.server, { state: 'completed', offset: 5 }), { fromWs: true })
    await h2.controller.reconcile()
    assert.equal(h2.state(r2.id), 'ready')
})

test('creation refused (4xx and 507) → failed with Retry and Remove', async () => {
    for (const status of [400, 507]) {
        const h = createHarness()
        h.server.createUpload = () => respond(status, { error: 'Not enough disk space' })
        const record = await h.add()
        assert.equal(h.state(record.id), 'failed')
        assert.equal(h.runtime[record.id].retryable, true)
    }
})

test('exhausted unanswered creation retries ending on an upload 500 → failed; a network pause stays uploading', async () => {
    const h = createHarness()
    h.server.createUpload = () => respond(500, { error: 'Cannot cancel' })
    const record = await h.add()
    assert.equal(h.state(record.id), 'uploading')
    for (const delay of RETRY_DELAYS) await h.clock.advance(delay)
    const entry = h.entry(h.runtime[record.id].clientId)
    assert.equal(entry.localState, 'paused')
    assert.equal(entry.server, null)
    assert.equal(h.state(record.id), 'failed')
    assert.equal(h.runtime[record.id].retryable, true)

    const h2 = createHarness()
    h2.server.createUpload = () => { throw new TypeError('offline') }
    const r2 = await h2.add()
    for (const delay of RETRY_DELAYS) await h2.clock.advance(delay)
    assert.equal(h2.entry(h2.runtime[r2.id].clientId).pauseReason, 'network')
    assert.equal(h2.state(r2.id), 'uploading')
})

test('transfer 500 / 507: the controller pauses the same upload; the chip stays uploading with its controller Retry', async () => {
    for (const status of [500, 507]) {
        const h = createHarness()
        const record = await h.add()
        const clientId = h.runtime[record.id].clientId
        h.tusUploads.at(-1).options.onError(tusError(status, { upload: true }))
        await flush()
        assert.equal(h.state(record.id), 'uploading')
        assert.equal(h.runtime[record.id].pauseReason, 'error')
        assert.equal(h.runtime[record.id].uploadKey, clientId)
        h.controller.retry(h.runtime[record.id].uploadKey)
        await flush()
        assert.equal(h.runtime[record.id].clientId, clientId)
        assert.equal(h.runtime[record.id].pauseReason, null)
        assert.equal(h.posts().length, 1)
    }
})

test('a file read failure before any entry exists → that exact chip failed, Retry and Remove', async () => {
    const h = createHarness()
    const broken = { name: 'bad.bin', size: 3, type: '', slice: () => ({ arrayBuffer: async () => { throw new Error('x') } }) }
    const record = await h.actions.addAttachment('s1', broken)
    await flush()
    assert.equal(h.state(record.id), 'failed')
    assert.equal(h.runtime[record.id].retryable, true)
    assert.equal(h.posts().length, 0)
    assert.equal(h.toasts.length, 0)
    assert.ok(h.rows.has(record.id))
})

test('callbacks of a removed chip are ignored, and its stray upload is cancelled', async () => {
    const h = createHarness()
    let release
    const gate = new Promise(resolve => { release = resolve })
    const broken = { name: 'bad.bin', size: 3, type: '', slice: () => ({ arrayBuffer: async () => { await gate; throw new Error('x') } }) }
    const adding = h.actions.addAttachment('s1', broken)
    await flush()
    const [id] = Object.keys(h.runtime)
    await h.actions.releaseAttachments([{ bucket: 's1', id }])
    release()
    await adding
    await flush()
    assert.equal(h.runtime[id], undefined)
    assert.deepEqual(h.actions.getRecords('s1'), [])

    // A readable file whose chip is removed during the read: its upload is cancelled at once.
    const h2 = createHarness()
    let release2
    const gate2 = new Promise(resolve => { release2 = resolve })
    const slow = new File(['hello'], 'slow.txt')
    const originalSlice = slow.slice.bind(slow)
    slow.slice = (...args) => {
        const blob = originalSlice(...args)
        return { arrayBuffer: async () => { await gate2; return blob.arrayBuffer() } }
    }
    const adding2 = h2.actions.addAttachment('s1', slow)
    await flush()
    const [id2] = Object.keys(h2.runtime)
    await h2.actions.releaseAttachments([{ bucket: 's1', id: id2 }])
    release2()
    await adding2
    await flush()
    assert.equal(h2.runtime[id2], undefined)
    // The creation answer arrived anyway: the upload is deleted on the server
    // (the entry waits for its `cancelled` record), and no transfer runs.
    assert.deepEqual([...h2.controller.entries.values()].map(e => e.localState), ['cancelling'])
    const [stray] = h2.controller.entries.values()
    assert.ok(h2.requests.some(r => r.method === 'DELETE' && r.url === `/api/uploads/${stray.server.id}/`))
    assert.equal(h2.tusUploads.filter(u => u.started && !u.aborted).length, 0)
})

// ── Status reconciliation (hydrate and reconnect) ────────────────────────────

function hydrateHarness(rows) {
    const h = createHarness()
    h.actions.hydrate(rows)
    return h
}

const R1 = { id: '00000000-0000-4000-8000-0000000000a1', sessionId: 's1', bucket: 's1', position: 0, name: 'a.txt', size: 5, mimeType: 'text/plain', kind: 'text' }
const R2 = { id: '00000000-0000-4000-8000-0000000000a2', sessionId: 's1', bucket: 's0', position: 1, name: 'b.png', size: 5, mimeType: 'image/png', kind: 'image' }
const R3 = { id: '00000000-0000-4000-8000-0000000000a3', sessionId: 's1', bucket: 's1', position: 2, name: 'c', size: 5, mimeType: '', kind: 'other' }

test('hydrate: ready / promoted → ready, missing → missing, uploading → uploading', async () => {
    const h = hydrateHarness([R1, R2, R3])
    h.server.statuses.set(`s1/${R1.id}`, { state: 'ready' })
    h.server.statuses.set(`s0/${R2.id}`, { state: 'promoted' })
    h.server.statuses.set(`s1/${R3.id}`, { state: 'uploading', client_id: `${OTHER_TAB}:1`, offset: 2 })
    await h.actions.reconcileAttachmentStatuses({ hydrate: true })
    const [call] = h.statusCalls()
    // The refs carry each record's own bucket.
    assert.deepEqual(call.body.refs, [{ bucket: 's1', id: R1.id }, { bucket: 's0', id: R2.id }, { bucket: 's1', id: R3.id }])
    assert.deepEqual([R1, R2, R3].map(r => h.state(r.id)), ['ready', 'ready', 'uploading'])
    assert.equal(h.runtime[R3.id].clientId, `${OTHER_TAB}:1`)
    assert.equal(h.runtime[R3.id].progress, 40)

    const h2 = hydrateHarness([R1])
    await h2.actions.reconcileAttachmentStatuses({ hydrate: true })
    assert.equal(h2.state(R1.id), 'missing')
    assert.equal(h2.runtime[R1.id].retryable, false)
})

test('reconnect: missing keeps a failed chip failed; a ready chip becomes missing', async () => {
    const h = createHarness()
    h.server.createUpload = () => respond(507, { error: 'Not enough disk space' })
    const failed = await h.add()
    assert.equal(h.state(failed.id), 'failed')
    h.actions.hydrate([R1])
    h.runtime[R1.id].state = 'ready'
    await h.actions.reconcileAttachmentStatuses()
    assert.equal(h.state(failed.id), 'failed')
    assert.equal(h.state(R1.id), 'missing')
})

test('reconcile skips records with a live local upload; a late status never overrides a newer local attempt', async () => {
    const h = createHarness()
    const live = await h.add()
    h.actions.hydrate([R1])
    await h.actions.reconcileAttachmentStatuses()
    assert.deepEqual(h.statusCalls()[0].body.refs, [{ bucket: 's1', id: R1.id }])
    assert.equal(h.state(live.id), 'uploading')

    // A status answer that arrives after the chip started a new local attempt is ignored.
    const h2 = createHarness()
    h2.server.createUpload = () => respond(400, { error: 'no' })
    const chip = await h2.add()
    assert.equal(h2.state(chip.id), 'failed')
    h2.server.createUpload = req => respond(201, makeServerRecord({ client_id: req.body.client_id, origin: req.body.origin }))
    let open
    h2.server.gate = new Promise(resolve => { open = resolve })
    h2.server.statuses.set(`s1/${chip.id}`, { state: 'missing' })
    const reconciling = h2.actions.reconcileAttachmentStatuses()
    await flush()
    await h2.actions.retryAttachment(chip.id)
    open()
    await reconciling
    await flush()
    assert.equal(h2.state(chip.id), 'uploading')
    assert.ok(h2.entry(h2.runtime[chip.id].clientId))
})

test('a stalled upload of another tab → failed with Remove only; a fresh one stays uploading', async () => {
    const h = hydrateHarness([R1])
    const foreignClientId = `${OTHER_TAB}:0000000000000001`
    h.server.statuses.set(`s1/${R1.id}`, { state: 'uploading', client_id: foreignClientId, offset: 0 })
    await h.actions.reconcileAttachmentStatuses({ hydrate: true })
    const record = makeServerRecord({ client_id: foreignClientId, origin: { panel: 'composer', key: `s1/${R1.id}` } })
    h.server.listed = [record]
    await h.controller.reconcile()
    assert.equal(h.state(R1.id), 'uploading')
    // No record for more than 180 s: stalled.
    await h.clock.advance(200_000)
    assert.equal(h.state(R1.id), 'failed')
    assert.equal(h.runtime[R1.id].retryable, false)
})

test('tab ownership after a reload: an upload of this tab without its File is stalled at once', async () => {
    const h = hydrateHarness([R1])
    const ownClientId = `${TAB}:00000000000000ee`
    h.server.statuses.set(`s1/${R1.id}`, { state: 'uploading', client_id: ownClientId, offset: 1 })
    await h.actions.reconcileAttachmentStatuses({ hydrate: true })
    assert.equal(h.state(R1.id), 'uploading')
    h.server.listed = [makeServerRecord({ client_id: ownClientId, origin: { panel: 'composer', key: `s1/${R1.id}` } })]
    await h.controller.reconcile()
    assert.equal(h.state(R1.id), 'failed')
})

test('the completion of an upload seen through status/ makes the chip ready', async () => {
    const h = hydrateHarness([R1])
    const foreignClientId = `${OTHER_TAB}:0000000000000002`
    h.server.statuses.set(`s1/${R1.id}`, { state: 'uploading', client_id: foreignClientId, offset: 0 })
    await h.actions.reconcileAttachmentStatuses({ hydrate: true })
    const record = makeServerRecord({ client_id: foreignClientId, origin: { panel: 'composer', key: `s1/${R1.id}` } })
    h.controller.applyServerRecord(record, { fromWs: true })
    h.controller.applyServerRecord(next(record, { state: 'completed', offset: 5 }), { fromWs: true })
    assert.equal(h.state(R1.id), 'ready')
})

// ── Release, forget, heartbeat ───────────────────────────────────────────────

test('releaseAttachments: cancel the local attempt, delete local and legacy rows, then DELETE each explicit ref', async () => {
    const h = createHarness()
    const record = await h.add()
    const clientId = h.runtime[record.id].clientId
    const serverId = h.entry(clientId).server.id
    h.legacyRows.add(record.id)
    const snapshotRef = { bucket: 'old-bucket', id: '00000000-0000-4000-8000-0000000000b1' }
    h.log.length = 0
    await h.actions.releaseAttachments([{ bucket: 's1', id: record.id }, snapshotRef])
    assert.deepEqual(h.log, [
        `DELETE /api/uploads/${serverId}/`,
        `delete ${record.id}`,
        `delete-legacy ${record.id}`,
        `delete ${snapshotRef.id}`,
        `delete-legacy ${snapshotRef.id}`,
        `DELETE /api/composer-attachments/s1/${record.id}/`,
        `DELETE /api/composer-attachments/old-bucket/${snapshotRef.id}/`,
    ])
    assert.equal(h.runtime[record.id], undefined)
    assert.equal(h.rows.has(record.id), false)
    assert.equal(h.legacyRows.has(record.id), false)
    assert.deepEqual(h.actions.getRecords('s1'), [])
    assert.equal(h.actions.getFile(record.id), null)
})

test('forgetAttachments: local and legacy rows of that session only; no cancel, no release request', async () => {
    const h = createHarness()
    const mine = await h.add('s1')
    const other = await h.add('s2')
    const clientId = h.runtime[mine.id].clientId
    h.log.length = 0
    await h.actions.forgetAttachments('s1')
    assert.deepEqual(h.log, [`delete ${mine.id}`, `delete-legacy ${mine.id}`])
    assert.equal(h.entry(clientId).localState, 'sending')
    assert.deepEqual(h.actions.getRecords('s1'), [])
    assert.deepEqual(h.actions.getRecords('s2').map(r => r.id), [other.id])
    assert.equal(h.runtime[mine.id], undefined)
    assert.ok(h.runtime[other.id])
})

test('touchHeldAttachments: draft refs with holder draft, snapshot refs separately with holder snapshot', async () => {
    const h = createHarness()
    h.actions.hydrate([R1, R2])
    await h.actions.touchHeldAttachments()
    await h.actions.touchHeldAttachments({ snapshotRefs: [{ bucket: 'x', id: 'y' }] })
    const touches = h.requests.filter(r => r.url === '/api/composer-attachments/touch/').map(r => r.body)
    assert.deepEqual(touches, [
        { refs: [{ bucket: 's1', id: R1.id }, { bucket: 's0', id: R2.id }], holder: 'draft' },
        { refs: [{ bucket: 's1', id: R1.id }, { bucket: 's0', id: R2.id }], holder: 'draft' },
        { refs: [{ bucket: 'x', id: 'y' }], holder: 'snapshot' },
    ])
})

test('hydrate keeps each record bucket, whatever its current session id', () => {
    const h = hydrateHarness([{ ...R2, sessionId: 'canonical' }])
    assert.deepEqual(toAttachmentRefs(h.actions.getRecords('canonical')), [{ bucket: 's0', id: R2.id }])
    assert.equal(h.state(R2.id), 'uploading')
})

test('releaseAttachments waits for the local cancel before releasing the staging entry', async () => {
    const h = createHarness()
    const record = await h.add()
    let answerDelete
    const deleteAnswered = new Promise(resolve => { answerDelete = resolve })
    const fetchBefore = h.requests.length
    // The tus DELETE of the local upload answers late.
    const originalCancel = h.controller.cancel
    h.controller.cancel = async key => {
        await deleteAnswered
        return originalCancel(key)
    }
    const releasing = h.actions.releaseAttachments([{ bucket: 's1', id: record.id }])
    await flush()
    assert.equal(h.requests.slice(fetchBefore).some(r => r.url.startsWith('/api/composer-attachments/')), false)
    answerDelete()
    await releasing
    assert.ok(h.requests.some(r => r.method === 'DELETE' && r.url === `/api/composer-attachments/s1/${record.id}/`))
})

test('an upload entry created and removed before any watcher run still fails its chip', async () => {
    // A minimal uploads store whose creation is refused within startUploads itself.
    const entries = reactive(new Map())
    const uploads = {
        tabId: TAB,
        entries,
        async startUploads({ clientId, origin }) {
            entries.set(clientId, { key: clientId, clientId, origin, localState: 'creating', local: true, server: null })
            entries.delete(clientId)
        },
        cancel: async () => {},
        onCompleted: () => () => {},
        isStalled: () => false,
    }
    const runtime = reactive({})
    const actions = createComposerAttachments({
        records: reactive({}),
        runtime,
        storage: { saveDraftAttachment: async () => {}, deleteDraftAttachment: async () => {}, deleteLegacyMedia: async () => {} },
        uploads,
        fetch: async () => respond(204),
        uuid: () => '00000000-0000-4000-8000-0000000000c1',
        randomHex: () => '0'.repeat(16),
    })
    const record = await actions.addAttachment('s1', new File(['x'], 'x.txt'))
    await flush()
    assert.equal(runtime[record.id].state, 'failed')
    assert.equal(runtime[record.id].retryable, true)
})

// ── Composer send and chips (spec 2026-10-03 §9.3, §9.4) ─────────────────────

test('canSendAttachments: Send only when every chip is ready; no attachment is fine', () => {
    const id = '00000000-0000-4000-8000-0000000000a1'
    const other = '00000000-0000-4000-8000-0000000000a2'
    const records = [{ id, bucket: 's1', position: 0 }]
    assert.equal(canSendAttachments(records, { [id]: { state: 'uploading' } }), false)
    assert.equal(canSendAttachments(records, { [id]: { state: 'failed' } }), false)
    assert.equal(canSendAttachments(records, { [id]: { state: 'ready' } }), true)
    assert.equal(canSendAttachments([], {}), true)
    assert.equal(canSendAttachments(records, { [id]: { state: 'missing' } }), false)
    // No runtime state yet (hydrated, status not answered): not ready.
    assert.equal(canSendAttachments(records, {}), false)
    const two = [...records, { id: other, bucket: 's1', position: 1 }]
    assert.equal(canSendAttachments(two, { [id]: { state: 'ready' }, [other]: { state: 'uploading' } }), false)
    assert.equal(canSendAttachments(two, { [id]: { state: 'ready' }, [other]: { state: 'ready' } }), true)
})

test('attachmentPayloadFields: the refs in the composer order, nothing else', () => {
    // A bound draft shows its canonical records, then the draft's own group:
    // positions are per group, so the display order is the send order.
    const records = [
        { id: 'c0', bucket: 'canonical', position: 0, name: 'a.png', kind: 'image' },
        { id: 'c1', bucket: 'canonical', position: 1, name: 'b.pdf', kind: 'PDF' },
        { id: 'd0', bucket: 'draft', position: 0, name: 'c.mov', kind: 'video' },
    ]
    assert.deepEqual(attachmentPayloadFields(records), {
        attachments: [{ bucket: 'canonical', id: 'c0' }, { bucket: 'canonical', id: 'c1' }, { bucket: 'draft', id: 'd0' }],
    })
    assert.deepEqual(attachmentPayloadFields([]), {})
})

test('snapshotAttachments keeps the send order and the six metadata fields', () => {
    const records = [
        { id: 'c1', sessionId: 'x', bucket: 'canonical', position: 1, name: 'b.pdf', size: 2, mimeType: 'application/pdf', kind: 'PDF' },
        { id: 'd0', sessionId: 'x', bucket: 'draft', position: 0, name: 'c.mov', size: 3, mimeType: '', kind: 'video', previewUrl: 'blob:x' },
    ]
    assert.deepEqual(snapshotAttachments(records), [
        { bucket: 'canonical', id: 'c1', name: 'b.pdf', size: 2, mimeType: 'application/pdf', kind: 'PDF' },
        { bucket: 'draft', id: 'd0', name: 'c.mov', size: 3, mimeType: '', kind: 'video' },
    ])
})

test('sendComposerMessage: ordered refs and no images/documents; register, then forget only the sent ids', () => {
    const records = [
        { id: 'i1', bucket: 's1', position: 0, name: 'one.png', size: 10, mimeType: 'image/png', kind: 'image' },
        { id: 'i2', bucket: 's1', position: 1, name: 'movie.mp4', size: 20, mimeType: 'video/mp4', kind: 'video' },
    ]
    const calls = []
    let frame = null
    const payload = { type: 'send_message', session_id: 's1', text: 'hi', images: [{ type: 'image' }], documents: [] }
    const ok = sendComposerMessage({
        payload,
        records,
        previewUrlFor: id => (id === 'i1' ? 'blob:one' : null),
        send: p => { calls.push('send'); frame = structuredClone(p); return true },
        register: attachments => calls.push(['register', attachments]),
        forget: ids => calls.push(['forget', ids]),
    })
    assert.equal(ok, true)
    assert.deepEqual(frame.attachments, [{ bucket: 's1', id: 'i1' }, { bucket: 's1', id: 'i2' }])
    assert.equal(Object.hasOwn(frame, 'images'), false)
    assert.equal(Object.hasOwn(frame, 'documents'), false)
    assert.equal(frame.text, 'hi')
    assert.deepEqual(calls, [
        'send',
        ['register', [
            { bucket: 's1', id: 'i1', name: 'one.png', size: 10, mimeType: 'image/png', kind: 'image', previewUrl: 'blob:one' },
            { bucket: 's1', id: 'i2', name: 'movie.mp4', size: 20, mimeType: 'video/mp4', kind: 'video', previewUrl: null },
        ]],
        ['forget', ['i1', 'i2']],
    ])
})

test('sendComposerMessage: a text-only send keeps its payload; a failed socket send registers and forgets nothing', () => {
    const calls = []
    const payload = { type: 'send_message', text: 'hi' }
    assert.equal(sendComposerMessage({
        payload, records: [], send: () => true, register: a => calls.push(['register', a]), forget: ids => calls.push(['forget', ids]),
    }), true)
    assert.deepEqual(payload, { type: 'send_message', text: 'hi' })
    assert.deepEqual(calls, [['register', []]])
    calls.length = 0
    assert.equal(sendComposerMessage({
        payload: { type: 'send_message', text: '' },
        records: [{ id: 'x', bucket: 's', position: 0 }],
        send: () => false,
        register: a => calls.push(['register', a]),
        forget: ids => calls.push(['forget', ids]),
    }), false)
    assert.deepEqual(calls, [])
})

test('an ordinary socket-send failure keeps the draft records, rows and uploads intact', async () => {
    const h = createHarness()
    const first = await h.add('s1', 'a.txt')
    const second = await h.add('s1', 'b.bin', 'xx', 'application/octet-stream')
    h.log.length = 0
    const ok = sendComposerMessage({
        payload: { type: 'send_message', text: '' },
        records: h.actions.getRecords('s1'),
        send: () => false,
        register: () => { throw new Error('never registered') },
        forget: ids => h.actions.forgetAttachments('s1', { ids }),
    })
    await flush()
    assert.equal(ok, false)
    assert.deepEqual(h.actions.getRecords('s1').map(r => r.id), [first.id, second.id])
    assert.ok(h.rows.has(first.id) && h.rows.has(second.id))
    assert.deepEqual(h.log, [])
})

test('post-send forget drops only the sent records; one added after the send survives', async () => {
    const h = createHarness()
    const sent = await h.add('s1', 'a.txt')
    const records = h.actions.getRecords('s1')
    const later = await h.add('s1', 'later.txt')
    h.log.length = 0
    await h.actions.forgetAttachments('s1', { ids: records.map(r => r.id) })
    assert.deepEqual(h.actions.getRecords('s1').map(r => r.id), [later.id])
    assert.deepEqual(h.log, [`delete ${sent.id}`, `delete-legacy ${sent.id}`])
    // Forget never cancels nor releases.
    assert.equal(h.requests.filter(r => r.method === 'DELETE').length, 0)
})

test('attachmentChipItem: six display kinds, previews from local object URLs, else the content endpoint once ready', () => {
    const rec = (kind, name) => ({ id: `id-${kind}`, bucket: 'b', position: 0, name, size: 1234, mimeType: '', kind })
    const ready = { state: 'ready', progress: 100, retryable: false, pauseReason: null }
    const uploading = { state: 'uploading', progress: 40, retryable: false, pauseReason: null }

    const localImage = attachmentChipItem(rec('image', 'a.png'), uploading, { previewUrl: 'blob:img' })
    assert.equal(localImage.type, 'image')
    assert.equal(localImage.src, 'blob:img')
    assert.equal(localImage.progress, 40)
    assert.equal(localImage.state, 'uploading')
    const remoteImage = attachmentChipItem(rec('image', 'a.png'), ready)
    assert.equal(remoteImage.src, '/api/composer-attachments/b/id-image/content')
    const pendingImage = attachmentChipItem(rec('image', 'a.png'), uploading)
    assert.equal(pendingImage.src, null)
    assert.equal(pendingImage.icon, 'file-image')

    assert.equal(attachmentChipItem(rec('text', 'a.txt'), uploading, { previewUrl: 'blob:txt' }).textUrl, 'blob:txt')
    const remoteText = attachmentChipItem(rec('text', 'a.txt'), ready)
    assert.equal(remoteText.type, 'txt')
    assert.equal(remoteText.textUrl, '/api/composer-attachments/b/id-text/content')
    assert.equal(attachmentChipItem(rec('text', 'a.txt'), uploading).textUrl, null)

    const pdf = attachmentChipItem(rec('PDF', 'a.pdf'), ready)
    assert.equal(pdf.type, 'pdf')
    assert.equal(pdf.icon, 'file-pdf')
    for (const [kind, icon] of [['video', 'file-video'], ['audio', 'file-audio'], ['other', 'file']]) {
        const item = attachmentChipItem(rec(kind, `a.${kind}`), ready, { previewUrl: 'blob:never' })
        assert.equal(item.type, 'other', kind)
        assert.equal(item.src, null, kind)
        assert.equal(item.textUrl, null, kind)
        assert.equal(item.icon, icon, kind)
        assert.equal(attachmentKindIcon(kind), icon)
    }
    const item = attachmentChipItem(rec('other', 'x.zip'), ready)
    for (const key of ['id', 'name', 'size', 'kind', 'state', 'progress', 'retryable']) assert.ok(Object.hasOwn(item, key), key)
    // No native/file indicator on a chip.
    for (const key of ['mode', 'native', 'inline']) assert.equal(Object.hasOwn(item, key), false, key)
})

test('formatAttachmentSize: bytes, KB, MB, GB; the chip carries the label', () => {
    assert.equal(formatAttachmentSize(0), '0 B')
    assert.equal(formatAttachmentSize(1023), '1023 B')
    assert.equal(formatAttachmentSize(1536), '1.5 KB')
    assert.equal(formatAttachmentSize(5 * 1024 * 1024), '5.0 MB')
    assert.equal(formatAttachmentSize(3 * 1024 ** 3), '3.0 GB')
    assert.equal(formatAttachmentSize(undefined), '0 B')
    const item = attachmentChipItem({ id: 'x', bucket: 'b', position: 0, name: 'x', size: 2048, kind: 'other' }, null)
    assert.equal(item.sizeLabel, '2.0 KB')
})

test('attachmentChipItem: Retry for a failed chip with its File, or a paused transfer; state messages', () => {
    const rec = { id: 'x', bucket: 'b', position: 0, name: 'x.bin', size: 1, mimeType: '', kind: 'other' }
    const failed = attachmentChipItem(rec, { state: 'failed', progress: 0, retryable: true })
    assert.equal(failed.retryable, true)
    assert.equal(failed.statusText, 'Upload failed')
    const stalled = attachmentChipItem(rec, { state: 'failed', progress: 0, retryable: false })
    assert.equal(stalled.retryable, false)
    assert.equal(stalled.statusText, 'Upload interrupted, attach the file again')
    const missing = attachmentChipItem(rec, { state: 'missing', progress: 0, retryable: false })
    assert.equal(missing.retryable, false)
    assert.equal(missing.statusText, 'File no longer available')
    const paused = attachmentChipItem(rec, { state: 'uploading', progress: 30, retryable: false, pauseReason: 'error' })
    assert.equal(paused.retryable, true)
    assert.equal(paused.statusText, 'Upload paused')
    const offline = attachmentChipItem(rec, { state: 'uploading', progress: 30, retryable: false, pauseReason: 'network' })
    assert.equal(offline.retryable, false)
    assert.equal(offline.statusText, 'Waiting for the connection')
    assert.equal(attachmentChipItem(rec, { state: 'ready', progress: 100 }).statusText, '')
    // Not answered yet (hydrate): uploading, no Retry.
    const unknown = attachmentChipItem(rec, null)
    assert.equal(unknown.state, 'uploading')
    assert.equal(unknown.retryable, false)
})

test('chip Retry of a paused transfer resumes the same upload through the controller', async () => {
    const h = createHarness()
    const record = await h.add()
    const clientId = h.runtime[record.id].clientId
    h.tusUploads.at(-1).options.onError(tusError(500, { upload: true }))
    await flush()
    assert.equal(h.runtime[record.id].pauseReason, 'error')
    assert.equal(await h.actions.retryAttachment(record.id), true)
    await flush()
    assert.equal(h.runtime[record.id].clientId, clientId)
    assert.equal(h.runtime[record.id].pauseReason, null)
    assert.equal(h.posts().length, 1)
})

test('composer object URLs: image and text Files only; revoked once no composer nor optimistic bubble holds them', async () => {
    const created = []
    const revoked = []
    let seq = 0
    const objectUrls = {
        create: file => { const url = `blob:${++seq}`; created.push([url, file.name]); return url },
        revoke: url => revoked.push(url),
    }
    const inUse = reactive(new Set())
    const h = createHarness({ objectUrls, previewUrlsInUse: () => inUse })
    const image = await h.add('s1', 'a.png', 'png', 'image/png')
    const pdf = await h.add('s1', 'a.pdf', 'pdf', 'application/pdf')
    const video = await h.add('s1', 'a.mp4', 'mp4', 'video/mp4')
    const text = await h.add('s1', 'a.txt', 'hello', 'text/plain')
    assert.deepEqual(created, [['blob:1', 'a.png'], ['blob:2', 'a.txt']])
    assert.equal(h.actions.getPreviewUrl(image.id), 'blob:1')
    assert.equal(h.actions.getPreviewUrl(pdf.id), null)
    assert.equal(h.actions.getPreviewUrl(video.id), null)
    assert.equal(h.actions.getPreviewUrl(text.id), 'blob:2')

    // Chip Remove: no other user, revoked at once.
    await h.actions.releaseAttachments([{ bucket: image.bucket, id: image.id }])
    assert.deepEqual(revoked, ['blob:1'])
    assert.equal(h.actions.getPreviewUrl(image.id), null)

    // Sent: the optimistic bubble holds the URL, the composer forgets the record.
    inUse.add('blob:2')
    await h.actions.forgetAttachments('s1', { ids: [text.id] })
    await flush()
    assert.deepEqual(revoked, ['blob:1'])
    // The bubble goes away: the URL is revoked.
    inUse.delete('blob:2')
    await flush()
    assert.deepEqual(revoked, ['blob:1', 'blob:2'])
})

test('readTextPreview reads at most the limit and reports the truncation', async () => {
    const fetchFn = async url => (url === '/bad' ? new Response('no', { status: 404 }) : new Response('héllo world'))
    assert.deepEqual(await readTextPreview('/ok', { fetch: fetchFn, limit: 1000 }), { text: 'héllo world', truncated: false })
    assert.deepEqual(await readTextPreview('/ok', { fetch: fetchFn, limit: 6 }), { text: 'héllo', truncated: true })
    await assert.rejects(readTextPreview('/bad', { fetch: fetchFn, limit: 10 }))
})

// ── Entry points (source contract) ───────────────────────────────────────────

function readSource(path) {
    return readFileSync(new URL(path, import.meta.url), 'utf8')
}

test('entry points: every file is accepted, the paperclip always shows, screenshots use addAttachment', () => {
    const input = readSource('../components/message/MessageInput.vue')
    assert.doesNotMatch(input, /:accept=|\baccept="/)
    assert.doesNotMatch(input, /getAttachmentSupport|attachmentSupport|canAttachAnything|resizeMediasForSend\(\s*records/)
    assert.match(input, /sendComposerMessage\(/)
    assert.match(input, /composerAttachmentsReady\(/)
    const paste = input.slice(input.indexOf('async function onPaste('), input.indexOf('\n}\n', input.indexOf('async function onPaste(')))
    assert.match(paste, /item\.kind === 'file'/)
    assert.doesNotMatch(paste, /type|accepted/)

    const popover = readSource('../components/message/AgentSettingsPopover.vue')
    assert.doesNotMatch(popover, /removeNonImageAttachments|getAttachmentSupport|nonImageAttachments/)

    const list = readSource('../components/session/detail/SessionItemsList.vue')
    assert.match(list, /store\.addAttachment\(props\.sessionId, file\)/)
    assert.match(readSource('../components/browser/BrowserPane.vue'), /store\.addAttachment\(props\.sessionId, file\)/)
    assert.match(readSource('../components/files/FilePane.vue'), /dataStore\.addAttachment\(sessionId, file\)/)

    const data = readSource('../stores/data.js')
    assert.doesNotMatch(data, /removeNonImageAttachments/)
    assert.match(data, /snapshotAttachments\(attachments/)
})

test('a large file send never embeds its bytes in the frame, the in-flight snapshot or the draft row', async () => {
    const h = createHarness()
    const big = new Uint8Array(8 * 1024 * 1024).fill(0x41) // 8 MiB of "A"
    const record = await h.add('s1', 'movie.mp4', big, 'video/mp4')
    assert.equal(record.size, big.length)
    let frame = null
    let registered = null
    const ok = sendComposerMessage({
        payload: { type: 'send_message', session_id: 's1', text: '' },
        records: h.actions.getRecords('s1'),
        previewUrlFor: () => 'blob:http://x/1',
        send: payload => { frame = JSON.stringify(payload); return true },
        register: attachments => { registered = JSON.stringify(attachments) },
        forget: () => {},
    })
    assert.equal(ok, true)
    const marker = 'A'.repeat(64)
    const base64Marker = btoa(marker)
    for (const [what, serialized] of [
        ['WebSocket frame', frame],
        ['in-flight snapshot', registered],
        ['draft row', JSON.stringify([...h.rows.values()])],
    ]) {
        assert.ok(serialized.length < 2048, `${what} stays small (${serialized.length} bytes)`)
        assert.ok(!serialized.includes(marker) && !serialized.includes(base64Marker), `${what} carries no file bytes`)
        assert.doesNotMatch(serialized, /data:|base64/, `${what} carries no data URL`)
    }
    assert.deepEqual(JSON.parse(frame).attachments, [{ bucket: 's1', id: record.id }])
})
