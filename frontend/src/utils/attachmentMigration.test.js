// Migration of legacy draft medias to staged composer attachments (spec
// 2026-10-03 §9.6, §9.5 legacy Edit). A legacy `draftMedias` row becomes a
// `File` and a composer record with the same id, uploaded only when the server
// does not already hold its entry, and deleted only once the entry is ready.
//
// The decisions run in `migrateLegacyAttachments`; the integration tests drive
// it through the composer actions the data store uses (`migrateLegacy` of
// `createComposerAttachments`), on top of the REAL upload controller with fake
// clock, apiFetch and tus uploads. Storage is in memory and survives a
// "reload" (a second harness over the same disk).

import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { reactive } from 'vue'
import {
    groupLegacyMedias,
    legacyMediaToFile,
    migrateLegacyAttachments,
    migrationDecision,
    orderLegacyMedias,
} from './attachmentMigration.js'
import { composerAttachmentsReady, createComposerAttachments } from './composerAttachments.js'
import { createUploadsController } from './uploads/controller.js'

const TAB = '11111111-1111-4111-8111-111111111111'
const OTHER_TAB = '22222222-2222-4222-8222-222222222222'
const START = 1_800_000_000_000

// ── Fixtures ─────────────────────────────────────────────────────────────────

const PNG_BYTES = Uint8Array.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a, 0x00, 0xff, 0x10])
const PDF_TEXT = '%PDF-1.4\n%âãÏÓ\n1 0 obj\n'
const TXT_TEXT = 'Héllo ✓ — notes\nsecond line\n'

const b64 = bytes => Buffer.from(bytes).toString('base64')

function legacyMedia(fields) {
    return { sessionId: 's1', createdAt: START, ...fields }
}

const IMG = legacyMedia({ id: '00000000-0000-4000-8000-0000000000c1', name: 'shot.png', type: 'image', mimeType: 'image/png', data: b64(PNG_BYTES), createdAt: START + 1 })
const PDF = legacyMedia({ id: '00000000-0000-4000-8000-0000000000c2', name: 'spec.pdf', type: 'pdf', mimeType: 'application/pdf', data: b64(Buffer.from(PDF_TEXT, 'latin1')), createdAt: START + 2 })
const TXT = legacyMedia({ id: '00000000-0000-4000-8000-0000000000c3', name: 'notes.md', type: 'txt', mimeType: 'text/markdown', data: TXT_TEXT, createdAt: START + 3 })

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

let serverSeq = 0
function makeServerRecord(fields = {}) {
    serverSeq += 1
    return {
        id: serverSeq.toString(16).padStart(32, '0'),
        client_id: `${OTHER_TAB}:0000000000000000`,
        state: 'active',
        version: 1,
        filename: 'f',
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
 * The persistent part of the browser: draft attachment records, legacy media
 * rows, and each draft's `mediaIds`. Shared by successive harnesses (reloads).
 */
function createDisk(medias = [], mediaIds = {}) {
    return {
        rows: new Map(),
        legacy: new Map(medias.map(media => [media.id, structuredClone(media)])),
        mediaIds: new Map(Object.entries(mediaIds).map(([sessionId, ids]) => [sessionId, [...ids]])),
    }
}

/**
 * The real upload controller and composer actions over a fake backend, with
 * the data store's legacy side mirrored: the in-memory legacy map (legacy
 * chips) filled at hydrate with the medias that have no record yet, and the
 * claim/unclaim callbacks the store passes to the migration.
 */
function createHarness(disk = createDisk(), options = {}) {
    const clock = createClock()
    const log = []
    const requests = []
    const server = {
        statuses: new Map(), // "<bucket>/<id>" → status item
        listed: [],
        statusFails: false,
        gate: null,
        createUpload: req => respond(201, makeServerRecord({
            client_id: req.body.client_id,
            filename: req.body.filename,
            size: req.body.size,
            origin: req.body.origin,
            fingerprint: req.body.fingerprint,
        })),
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
            if (server.statusFails) return respond(503, { error: 'down' }, { upload: false })
            return respond(200, {
                statuses: req.body.refs.map(ref => ({
                    bucket: ref.bucket,
                    id: ref.id,
                    ...(server.statuses.get(`${ref.bucket}/${ref.id}`) || { state: 'missing' }),
                })),
            })
        }
        if (url.startsWith('/api/composer-attachments/')) return respond(204, null, { upload: false })
        throw new Error(`unexpected ${req.method} ${url}`)
    }
    const tusUploads = []
    let hex = 0
    const controller = createUploadsController({
        apiFetch,
        createTusUpload: (file, opts) => {
            const upload = { file, options: opts, started: 0, aborted: 0, start() { this.started += 1 }, abort() { this.aborted += 1 } }
            tusUploads.push(upload)
            return upload
        },
        toast: { success: () => {}, error: () => {} },
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
    // The in-memory legacy map of the store: `sessionId → Map(id → media)`.
    const legacyMap = new Map()
    const storage = {
        async saveDraftAttachment(record) {
            if (options.failWrites) throw new Error('quota')
            log.push(`save ${record.id}`)
            disk.rows.set(record.id, structuredClone(record))
        },
        async saveDraftAttachments(list) {
            if (options.failWrites) throw new Error('quota')
            log.push(`save-many ${list.map(r => r.id).join(',')}`)
            for (const record of list) disk.rows.set(record.id, structuredClone(record))
        },
        async deleteDraftAttachment(id) {
            log.push(`delete ${id}`)
            disk.rows.delete(id)
        },
        // The store's `_deleteLegacyMediaRow`: row, legacy chip, draft `mediaIds`.
        async deleteLegacyMedia(id) {
            log.push(`delete-legacy ${id}`)
            const media = disk.legacy.get(id)
            if (!media) return
            disk.legacy.delete(id)
            legacyMap.get(media.sessionId)?.delete(id)
            const ids = disk.mediaIds.get(media.sessionId)
            if (ids) disk.mediaIds.set(media.sessionId, ids.filter(other => other !== id))
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
        objectUrls: { create: () => `blob:${++uuidSeq}`, revoke: () => {} },
    })
    const claims = sessionId => ({
        claim: media => legacyMap.get(sessionId)?.delete(media.id) || false,
        unclaim: media => {
            if (!legacyMap.has(sessionId)) legacyMap.set(sessionId, new Map())
            legacyMap.get(sessionId).set(media.id, media)
        },
    })
    const h = {
        disk, clock, log, requests, server, tusUploads, controller, records, runtime, actions, legacyMap,
        errors: [], // migration rejections of `start()`
        posts: () => requests.filter(r => r.method === 'POST' && r.url === '/api/uploads/'),
        statusCalls: () => requests.filter(r => r.url === '/api/composer-attachments/status/'),
        state: id => runtime[id]?.state,
        entryOf: id => controller.entries.get(runtime[id]?.clientId),
        legacyChips: sessionId => [...(legacyMap.get(sessionId)?.values() || [])].map(m => m.id),
        /** The store's hydrate: records, legacy chips without record, then one migration per session. */
        async start() {
            const rows = [...disk.rows.values()].map(row => structuredClone(row))
            actions.hydrate(rows)
            const { bySession, visible } = groupLegacyMedias([...disk.legacy.values()], new Set(rows.map(r => r.id)))
            for (const media of visible) {
                if (!legacyMap.has(media.sessionId)) legacyMap.set(media.sessionId, new Map())
                legacyMap.get(media.sessionId).set(media.id, media)
            }
            const runs = [...bySession].map(([sessionId, medias]) =>
                actions.migrateLegacy(sessionId, medias, disk.mediaIds.get(sessionId) || [], claims(sessionId))
                    .catch(error => h.errors.push(error)))
            const reconcile = actions.reconcileAttachmentStatuses({ hydrate: true })
            await Promise.all([...runs, reconcile])
            await flush()
        },
        /** Legacy failed-send Edit: the rows are restored (store), then migrated into the composer. */
        async editLegacySnapshot(sessionId, medias) {
            for (const media of medias) disk.legacy.set(media.id, { ...structuredClone(media), sessionId })
            disk.mediaIds.set(sessionId, [...(disk.mediaIds.get(sessionId) || []), ...medias.map(m => m.id)])
            if (!legacyMap.has(sessionId)) legacyMap.set(sessionId, new Map())
            for (const media of medias) legacyMap.get(sessionId).set(media.id, { ...media, sessionId })
            await actions.migrateLegacy(sessionId, medias, medias.map(m => m.id), claims(sessionId))
            await flush()
        },
        complete(id) {
            const entry = h.entryOf(id)
            controller.applyServerRecord(next(entry.server, { state: 'completed', offset: entry.server.size }), { fromWs: true })
        },
        fail(id) {
            const entry = h.entryOf(id)
            controller.applyServerRecord(next(entry.server, { state: 'failed', error: 'boom' }), { fromWs: true })
        },
    }
    return h
}

// ── Pure helpers ─────────────────────────────────────────────────────────────

test('legacyMediaToFile: base64 images and PDFs, plain-text txt; name and MIME type kept', async () => {
    const txt = legacyMediaToFile(TXT)
    assert.ok(txt instanceof File)
    assert.deepEqual(await legacyMediaToFile(TXT).text(), TXT_TEXT)
    assert.equal(txt.name, 'notes.md')
    assert.equal(txt.type, 'text/markdown')
    assert.equal(txt.size, Buffer.byteLength(TXT_TEXT, 'utf8'))

    const png = legacyMediaToFile(IMG)
    assert.deepEqual(new Uint8Array(await png.arrayBuffer()), PNG_BYTES)
    assert.equal(png.name, 'shot.png')
    assert.equal(png.type, 'image/png')

    const pdf = legacyMediaToFile(PDF)
    assert.deepEqual(Buffer.from(await pdf.arrayBuffer()), Buffer.from(PDF_TEXT, 'latin1'))
    assert.equal(pdf.type, 'application/pdf')

    // Without a stored MIME type: the family default. A data URL prefix is tolerated.
    assert.equal(legacyMediaToFile({ ...TXT, mimeType: '' }).type, 'text/plain')
    assert.equal(legacyMediaToFile({ ...PDF, mimeType: undefined }).type, 'application/pdf')
    const prefixed = legacyMediaToFile({ ...IMG, data: `data:image/png;base64,${IMG.data}` })
    assert.deepEqual(new Uint8Array(await prefixed.arrayBuffer()), PNG_BYTES)
    assert.throws(() => legacyMediaToFile({ ...IMG, data: '%%%not base64%%%' }))
    assert.throws(() => legacyMediaToFile({ ...IMG, type: 'video' }))
})

test('orderLegacyMedias: the draft mediaIds order first, then the remaining rows by createdAt', () => {
    const late = legacyMedia({ id: 'late', createdAt: START + 50 })
    const early = legacyMedia({ id: 'early', createdAt: START - 50 })
    const ordered = orderLegacyMedias([IMG, late, PDF, early, TXT], [TXT.id, 'gone', IMG.id])
    assert.deepEqual(ordered.map(m => m.id), [TXT.id, IMG.id, early.id, PDF.id, late.id])
    assert.deepEqual(orderLegacyMedias([PDF, IMG], undefined).map(m => m.id), [IMG.id, PDF.id])
    // A duplicated id in mediaIds never duplicates its row.
    assert.deepEqual(orderLegacyMedias([IMG], [IMG.id, IMG.id]).map(m => m.id), [IMG.id])
})

test('migrationDecision: ready/promoted keep, foreign uploading defers, own or missing starts', () => {
    const own = `${TAB}:00000000000000aa`
    const foreign = `${OTHER_TAB}:00000000000000aa`
    assert.equal(migrationDecision({ state: 'ready' }, { tabId: TAB }), 'ready')
    assert.equal(migrationDecision({ state: 'promoted' }, { tabId: TAB }), 'ready')
    assert.equal(migrationDecision({ state: 'uploading', client_id: foreign }, { tabId: TAB }), 'defer')
    assert.equal(migrationDecision({ state: 'uploading', client_id: own }, { tabId: TAB }), 'start')
    assert.equal(migrationDecision({ state: 'missing' }, { tabId: TAB }), 'start')
})

test('groupLegacyMedias: rows by session; only rows without a record show as legacy chips', () => {
    const other = legacyMedia({ id: 'o', sessionId: 's2' })
    const { bySession, visible } = groupLegacyMedias([IMG, other, PDF], new Set([PDF.id]))
    assert.deepEqual([...bySession.keys()], ['s1', 's2'])
    assert.deepEqual(bySession.get('s1').map(m => m.id), [IMG.id, PDF.id])
    assert.deepEqual(visible.map(m => m.id), [IMG.id, other.id])
})

test('composerAttachmentsReady: any legacy media still in the composer blocks Send', () => {
    const ready = { a: { state: 'ready' } }
    assert.equal(composerAttachmentsReady([{ id: 'a' }], ready, 0), true)
    assert.equal(composerAttachmentsReady([], {}, 0), true)
    // A legacy-only composer (pending or failed migration) never sends its
    // medias as legacy images/documents (Codex drops documents).
    assert.equal(composerAttachmentsReady([], {}, 1), false)
    assert.equal(composerAttachmentsReady([{ id: 'a' }], ready, 1), false)
    assert.equal(composerAttachmentsReady([{ id: 'a' }], { a: { state: 'uploading' } }, 0), false)
})

test('migrateLegacyAttachments: readiness cleanup registered before any status, a failed status request fails the chips', async () => {
    const calls = []
    const record = { id: IMG.id, bucket: 's1' }
    const deps = {
        tabId: TAB,
        adoptRecords: async (sessionId, entries) => {
            calls.push(`adopt ${sessionId} ${entries.map(e => e.media.id)}`)
            return [record]
        },
        hasLiveLocalUpload: () => false,
        isCurrent: () => true,
        whenReady: id => calls.push(`when-ready ${id}`),
        deleteLegacyMedia: async id => calls.push(`delete-legacy ${id}`),
        statusRefs: async refs => {
            calls.push(`status ${refs.map(r => `${r.bucket}/${r.id}`)}`)
            throw new Error('down')
        },
        applyStatus: () => calls.push('apply'),
        markFailed: id => calls.push(`failed ${id}`),
        startUpload: async () => calls.push('start'),
        finish: list => calls.push(`finish ${list.length}`),
    }
    await migrateLegacyAttachments({ sessionId: 's1', medias: [IMG], mediaIds: [], dependencies: deps })
    assert.deepEqual(calls, [`adopt s1 ${IMG.id}`, `when-ready ${IMG.id}`, `status s1/${IMG.id}`, `failed ${IMG.id}`, 'finish 1'])

    // An undecodable row is left alone (kept, still a legacy chip); an adopt failure propagates.
    calls.length = 0
    deps.adoptRecords = async () => { throw new Error('quota') }
    await assert.rejects(
        migrateLegacyAttachments({ sessionId: 's1', medias: [{ ...TXT, type: 'bogus' }, IMG], mediaIds: [], dependencies: deps }),
        /quota/,
    )
    assert.deepEqual(calls, [])
})

// ── Migration through the composer actions ───────────────────────────────────

test('a legacy row becomes a record with the media id, uploaded; the row is deleted only once ready', async () => {
    const disk = createDisk([IMG, PDF, TXT], { s1: [TXT.id, IMG.id] })
    const h = createHarness(disk)
    await h.start()
    const migratedRecord = disk.rows.get(IMG.id)
    assert.equal(migratedRecord.id, IMG.id)
    assert.deepEqual(h.actions.getRecords('s1').map(r => [r.id, r.position]), [[TXT.id, 0], [IMG.id, 1], [PDF.id, 2]])
    assert.deepEqual(disk.rows.get(TXT.id), {
        id: TXT.id, sessionId: 's1', bucket: 's1', position: 0, name: 'notes.md', size: Buffer.byteLength(TXT_TEXT), mimeType: 'text/markdown', kind: 'text',
    })
    assert.equal(disk.rows.get(PDF.id).kind, 'PDF')
    // No legacy chip once the record shows it; the File is in memory (previews, Retry).
    assert.deepEqual(h.legacyChips('s1'), [])
    assert.equal(await h.actions.getFile(TXT.id).text(), TXT_TEXT)
    // One status request for the migrated refs, before any upload; the hydrate
    // reconcile does not ask for records under migration.
    assert.equal(h.statusCalls().length, 1)
    assert.deepEqual(h.statusCalls()[0].body.refs.map(r => r.id), [TXT.id, IMG.id, PDF.id])
    assert.ok(h.log.indexOf('POST /api/composer-attachments/status/') < h.log.indexOf('POST /api/uploads/'))
    const posts = h.posts()
    assert.deepEqual(posts.map(p => p.body.origin.key), [`s1/${TXT.id}`, `s1/${IMG.id}`, `s1/${PDF.id}`])
    assert.deepEqual(posts.map(p => p.body.filename), ['notes.md', 'shot.png', 'spec.pdf'])
    assert.ok(posts.every(p => p.body.client_id.startsWith(`${TAB}:`)))
    // Still uploading: every legacy row stays.
    assert.equal(disk.legacy.size, 3)
    assert.deepEqual([TXT, IMG, PDF].map(m => h.state(m.id)), ['uploading', 'uploading', 'uploading'])
    h.complete(IMG.id)
    await flush()
    assert.equal(h.state(IMG.id), 'ready')
    assert.deepEqual([...disk.legacy.keys()].sort(), [PDF.id, TXT.id].sort())
    assert.deepEqual(disk.mediaIds.get('s1'), [TXT.id])
})

test('ready or promoted entries are never uploaded again; their rows are deleted at once', async () => {
    const disk = createDisk([IMG, PDF])
    const h = createHarness(disk)
    h.server.statuses.set(`s1/${IMG.id}`, { state: 'ready' })
    h.server.statuses.set(`s1/${PDF.id}`, { state: 'promoted' })
    await h.start()
    const uploadCallsForReadyEntry = h.posts().length
    assert.equal(uploadCallsForReadyEntry, 0)
    assert.equal(h.state(IMG.id), 'ready')
    assert.equal(h.state(PDF.id), 'ready')
    assert.equal(disk.legacy.size, 0)
    assert.deepEqual(h.actions.getRecords('s1').map(r => r.id), [IMG.id, PDF.id])
})

test('an upload of another tab defers the migration: no upload, row kept; its completion still readies the chip', async () => {
    const disk = createDisk([IMG])
    const foreign = `${OTHER_TAB}:00000000000000f1`
    const h = createHarness(disk)
    h.server.statuses.set(`s1/${IMG.id}`, { state: 'uploading', client_id: foreign, offset: 2 })
    await h.start()
    assert.equal(h.posts().length, 0)
    assert.equal(h.state(IMG.id), 'uploading')
    assert.equal(h.runtime[IMG.id].clientId, foreign)
    assert.equal(disk.legacy.size, 1)
    const record = makeServerRecord({ client_id: foreign, origin: { panel: 'composer', key: `s1/${IMG.id}` } })
    h.controller.applyServerRecord(record, { fromWs: true })
    h.controller.applyServerRecord(next(record, { state: 'completed', offset: 5 }), { fromWs: true })
    await flush()
    assert.equal(h.state(IMG.id), 'ready')
    assert.equal(disk.legacy.size, 0)
})

test('a stalled upload of this tab (before a reload) starts a fresh attempt with a new client id', async () => {
    const disk = createDisk([IMG])
    const stalled = `${TAB}:00000000000000e1`
    const h = createHarness(disk)
    h.server.statuses.set(`s1/${IMG.id}`, { state: 'uploading', client_id: stalled, offset: 1 })
    await h.start()
    const [post] = h.posts()
    assert.ok(post)
    assert.notEqual(post.body.client_id, stalled)
    assert.ok(post.body.client_id.startsWith(`${TAB}:`))
    assert.equal(h.runtime[IMG.id].clientId, post.body.client_id)
    assert.equal(h.state(IMG.id), 'uploading')
})

test('a live local upload of this tab starts nothing: a second migration in the same page keeps one record, one upload', async () => {
    const disk = createDisk([IMG])
    const h = createHarness(disk)
    await h.start()
    assert.equal(h.posts().length, 1)
    const clientId = h.runtime[IMG.id].clientId
    // Same page: the legacy row is migrated again (e.g. a second Edit of the same media).
    await h.actions.migrateLegacy('s1', [IMG], [IMG.id], { claim: () => true, unclaim: () => {} })
    await flush()
    assert.equal(h.posts().length, 1)
    assert.equal(h.statusCalls().length, 1)
    assert.equal(h.runtime[IMG.id].clientId, clientId)
    assert.equal(h.actions.getRecords('s1').length, 1)
})

test('two hydrations reuse the record, its position and its bucket: never a duplicate', async () => {
    const disk = createDisk([IMG, TXT])
    const first = createHarness(disk)
    first.server.createUpload = () => respond(400, { error: 'no' })
    await first.start()
    assert.equal(first.state(IMG.id), 'failed')
    const positions = first.actions.getRecords('s1').map(r => [r.id, r.position])

    // Reload: the stored records come back, the legacy rows are still there.
    disk.rows.set(IMG.id, { ...disk.rows.get(IMG.id), sessionId: 'canonical' })
    const second = createHarness(disk)
    await second.start()
    const recordsAfterTwoHydrations = [...disk.rows.values()].filter(r => r.id === IMG.id)
    assert.equal(recordsAfterTwoHydrations.length, 1)
    assert.equal(disk.rows.size, 2)
    assert.deepEqual(second.actions.getRecords('s1').map(r => [r.id, r.position]), positions.filter(([id]) => id !== IMG.id))
    // The record moved to another session id: same position, bucket unchanged.
    const moved = second.actions.getRecords('canonical')
    assert.deepEqual(moved.map(r => [r.id, r.bucket, r.position]), [[IMG.id, 's1', positions.find(([id]) => id === IMG.id)[1]]])
    assert.deepEqual(second.posts().map(p => p.body.origin.key).sort(), [`s1/${IMG.id}`, `s1/${TXT.id}`].sort())
    assert.deepEqual(second.legacyChips('s1'), [])
})

test('an upload failure keeps the legacy row and a retryable chip; the next start migrates it again', async () => {
    const disk = createDisk([IMG])
    const h = createHarness(disk)
    await h.start()
    h.fail(IMG.id)
    await flush()
    assert.equal(h.state(IMG.id), 'failed')
    assert.equal(h.runtime[IMG.id].retryable, true)
    const legacyRowsAfterUploadFailure = [...disk.legacy.keys()]
    assert.equal(legacyRowsAfterUploadFailure.length, 1)

    const reloaded = createHarness(disk)
    await reloaded.start()
    assert.equal(reloaded.posts().length, 1)
    reloaded.complete(IMG.id)
    await flush()
    assert.equal(reloaded.state(IMG.id), 'ready')
    assert.equal(disk.legacy.size, 0)
})

test('a failed status request keeps the row and fails the chip with Retry; Retry readies it and deletes the row', async () => {
    const disk = createDisk([TXT])
    const h = createHarness(disk)
    h.server.statusFails = true
    await h.start()
    assert.equal(h.posts().length, 0)
    assert.equal(h.state(TXT.id), 'failed')
    assert.equal(h.runtime[TXT.id].retryable, true)
    assert.equal(disk.legacy.size, 1)
    assert.equal(await h.actions.retryAttachment(TXT.id), true)
    await flush()
    h.complete(TXT.id)
    await flush()
    assert.equal(h.state(TXT.id), 'ready')
    assert.equal(disk.legacy.size, 0)
})

test('Remove while the migration upload waits deletes the legacy row and its mediaIds reference: it never returns', async () => {
    const disk = createDisk([IMG, TXT], { s1: [IMG.id, TXT.id] })
    const h = createHarness(disk)
    await h.start()
    const clientId = h.runtime[IMG.id].clientId
    await h.actions.releaseAttachments([{ bucket: 's1', id: IMG.id }])
    assert.equal(disk.legacy.has(IMG.id), false)
    assert.deepEqual(disk.mediaIds.get('s1'), [TXT.id])
    assert.equal(disk.rows.has(IMG.id), false)
    assert.ok(h.requests.some(r => r.method === 'DELETE' && r.url === `/api/composer-attachments/s1/${IMG.id}/`))
    // A late completion of the cancelled attempt changes nothing.
    const record = makeServerRecord({ client_id: clientId, origin: { panel: 'composer', key: `s1/${IMG.id}` } })
    h.controller.applyServerRecord(next(record, { state: 'completed', version: 9 }), { fromWs: true })
    await flush()
    assert.equal(h.runtime[IMG.id], undefined)

    // A forget (orphan cleanup, post-send clear) deletes the legacy row too.
    await h.actions.forgetAttachments('s1')
    assert.equal(disk.legacy.size, 0)
    assert.deepEqual(disk.mediaIds.get('s1'), [])

    const reloaded = createHarness(disk)
    await reloaded.start()
    assert.deepEqual(reloaded.actions.getRecords('s1'), [])
    assert.equal(reloaded.posts().length, 0)
})

test('Remove while the migration waits for its status answer: no upload starts, nothing comes back', async () => {
    const disk = createDisk([IMG])
    const h = createHarness(disk)
    let open
    h.server.gate = new Promise(resolve => { open = resolve })
    const starting = h.start()
    await flush()
    assert.equal(h.state(IMG.id), 'uploading')
    await h.actions.releaseAttachments([{ bucket: 's1', id: IMG.id }])
    open()
    await starting
    assert.deepEqual(h.errors, [])
    assert.equal(h.posts().length, 0)
    assert.equal(h.runtime[IMG.id], undefined)
    assert.equal(disk.legacy.size, 0)
    assert.equal(disk.rows.size, 0)
})

test('concurrent migrations of the same media in one tab: one status request, one upload', async () => {
    const disk = createDisk([IMG])
    const h = createHarness(disk)
    let open
    h.server.gate = new Promise(resolve => { open = resolve })
    const starting = h.start()
    await flush()
    // A legacy Edit of the same media while the startup migration waits for its status.
    const again = h.actions.migrateLegacy('s1', [IMG], [IMG.id], { claim: () => true, unclaim: () => {} })
    open()
    await Promise.all([starting, again])
    await flush()
    assert.deepEqual(h.errors, [])
    assert.equal(h.statusCalls().length, 1)
    assert.equal(h.posts().length, 1)
    assert.equal(h.actions.getRecords('s1').length, 1)
})

test('a legacy chip removed before the migration claims it is skipped', async () => {
    const disk = createDisk([IMG, TXT])
    const h = createHarness(disk)
    // Hydrated as legacy chips, then the user removes one before its migration.
    h.legacyMap.set('s1', new Map([[TXT.id, TXT]]))
    disk.legacy.delete(IMG.id)
    await h.actions.migrateLegacy('s1', [IMG, TXT], [], {
        claim: media => h.legacyMap.get('s1').delete(media.id),
        unclaim: () => {},
    })
    await flush()
    assert.deepEqual(h.actions.getRecords('s1').map(r => r.id), [TXT.id])
    assert.equal(h.posts().length, 1)
})

test('an IndexedDB failure keeps the legacy rows as legacy chips, removable; nothing uploads', async () => {
    const disk = createDisk([IMG, TXT])
    const h = createHarness(disk, { failWrites: true })
    await h.start()
    assert.match(String(h.errors[0]), /quota/)
    assert.deepEqual(h.actions.getRecords('s1'), [])
    assert.deepEqual(h.legacyChips('s1').sort(), [IMG.id, TXT.id].sort())
    assert.equal(h.posts().length, 0)
    assert.equal(h.statusCalls().length, 0)
    assert.equal(disk.legacy.size, 2)
    assert.equal(h.runtime[IMG.id], undefined)
})

test('legacy failed-send Edit with current draft attachments: the migrated records append, existing order unchanged', async () => {
    const h = createHarness()
    const first = await h.actions.addAttachment('s1', new File(['one'], 'one.txt', { type: 'text/plain' }))
    const second = await h.actions.addAttachment('s1', new File(['two'], 'two.png', { type: 'image/png' }))
    await flush()
    const before = h.actions.getRecords('s1').map(r => [r.id, r.position])
    await h.editLegacySnapshot('s1', [PDF, IMG])
    const after = h.actions.getRecords('s1').map(r => [r.id, r.position])
    assert.deepEqual(after.slice(0, 2), before)
    assert.deepEqual(after.map(([id]) => id), [first.id, second.id, PDF.id, IMG.id])
    assert.deepEqual(after.slice(2).map(([, position]) => position), [2, 3])
    assert.deepEqual(h.actions.getRecords('s1').slice(2).map(r => r.bucket), ['s1', 's1'])
    assert.deepEqual(h.legacyChips('s1'), [])
    // The legacy rows stay until each entry is ready.
    assert.equal(h.disk.legacy.size, 2)
    h.complete(PDF.id)
    await flush()
    assert.deepEqual([...h.disk.legacy.keys()], [IMG.id])
})

// ── Callers ──────────────────────────────────────────────────────────────────

const read = path => readFileSync(new URL(path, import.meta.url), 'utf8')
const between = (source, start, end) => source.slice(source.indexOf(start), source.indexOf(end, source.indexOf(start) + start.length))

test('callers: hydrate migrates, legacy Edit migrates, legacy Retry unchanged, the composer never sends legacy medias', () => {
    const data = read('../stores/data.js')
    const hydrate = between(data, 'async hydrateAttachments(', '\n        },\n')
    assert.match(hydrate, /groupLegacyMedias\(/)
    assert.match(hydrate, /_migrateLegacyMedias\(/)
    assert.match(between(data, 'async _migrateLegacyMedias(', '\n        },\n'), /migrateLegacy\(/)

    const banner = read('../components/session/detail/items/FailedSendBanner.vue')
    const edit = between(banner, 'async function edit(', '\n}\n')
    assert.match(edit, /restoreLegacyDraftMedias\(props\.sessionId, entry\.medias\)/)
    assert.doesNotMatch(edit, /restoreDraftAttachments\(/)
    const retry = between(banner, 'async function retry(', '\n}\n')
    assert.match(retry, /resizeMediasForSend\(/)
    assert.match(retry, /mediasToSdkFormat\(medias\)/)

    const input = read('../components/message/MessageInput.vue')
    assert.match(input, /composerAttachmentsReady\(/)
    const send = between(input, 'async function handleSend(', '\n}\n')
    assert.doesNotMatch(send, /payload\.documents|payload\.images|resizeMediasForSend|\bgetAttachments\(/)
})
