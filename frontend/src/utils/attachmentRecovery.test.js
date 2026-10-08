// Failed-send recovery of composer attachments (spec 2026-10-03 §6.1.4, §9.4,
// §9.5): in-flight snapshots keep staged refs (no bytes), Retry resends them,
// Edit restores them as draft records, binding rehomes unsent records, and
// every cleanup caller either releases explicit refs or only forgets locally.
//
// The actions run through the same factories the data store delegates to
// (`createComposerAttachments`, `createEphemeralActions`), with injected
// storage, fetch and upload fakes: Pinia cannot load under node:test.

import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { reactive } from 'vue'
import {
    composerRecordsFor,
    createAttachmentRefCollector,
    createComposerAttachments,
    optimisticAttachmentFields,
    snapshotAttachmentRefs,
    snapshotAttachments,
    toAttachmentRefs,
    uniqueRefs,
} from './composerAttachments.js'
import { attachmentCountForMessage, attachmentMatchKey, inflightAttachmentCount } from './attachmentStrip.js'
import { toStoredInflightSend } from './inflightStorage.js'
import { createEphemeralActions, createSendFailureActions } from './ephemeralSessions.js'

const TAB = '11111111-1111-4111-8111-111111111111'

async function flush() {
    for (let i = 0; i < 20; i++) await new Promise(resolve => setImmediate(resolve))
}

// ── Fakes ────────────────────────────────────────────────────────────────────

/** A minimal uploads store: each attempt becomes a local entry until completed. */
function createUploadsFake() {
    const entries = reactive(new Map())
    const listeners = new Set()
    const cancels = []
    return {
        tabId: TAB,
        entries,
        cancels,
        async startUploads({ files, origin, clientId }) {
            entries.set(clientId, {
                key: clientId, clientId, origin, local: true, localState: 'uploading',
                pauseReason: null, server: null, sentBytes: 0, size: files[0].size,
            })
        },
        async cancel(key) {
            cancels.push(key)
            entries.delete(key)
        },
        onCompleted(fn) {
            listeners.add(fn)
            return () => listeners.delete(fn)
        },
        isStalled: () => false,
        complete(clientId) {
            const entry = entries.get(clientId)
            for (const fn of listeners) fn({ client_id: clientId, origin: entry.origin })
            entries.delete(clientId)
        },
    }
}

function createEnv({ statuses = {} } = {}) {
    const requests = []
    const rows = new Map()
    const bulkWrites = []
    const apiFetch = async (url, init = {}) => {
        const req = { url, method: init.method || 'GET', body: init.body ? JSON.parse(init.body) : null }
        requests.push(req)
        if (url === '/api/composer-attachments/status/') {
            return new Response(JSON.stringify({
                statuses: req.body.refs.map(ref => ({ ...ref, ...(statuses[`${ref.bucket}/${ref.id}`] || { state: 'ready' }) })),
            }), { status: 200 })
        }
        return new Response(null, { status: 204 })
    }
    let saveGate = null
    const storage = {
        async saveDraftAttachment(record) {
            if (saveGate) await saveGate
            rows.set(record.id, structuredClone(record))
        },
        async saveDraftAttachments(records) {
            bulkWrites.push(records.map(record => structuredClone(record)))
            for (const record of records) rows.set(record.id, structuredClone(record))
        },
        async deleteDraftAttachment(id) { rows.delete(id) },
        async deleteLegacyMedia() {},
    }
    const uploads = createUploadsFake()
    const records = reactive({})
    const runtime = reactive({})
    const aliases = reactive({})
    let uuidSeq = 0
    let hex = 0
    const actions = createComposerAttachments({
        records,
        runtime,
        aliases,
        storage,
        uploads,
        fetch: apiFetch,
        uuid: () => `00000000-0000-4000-8000-${(++uuidSeq).toString(16).padStart(12, '0')}`,
        randomHex: n => (hex++).toString(16).padStart(n, '0'),
    })
    return {
        requests, rows, bulkWrites, storage, uploads, records, runtime, aliases, actions,
        gateSaves() {
            let open
            saveGate = new Promise(resolve => { open = resolve })
            return () => { saveGate = null; open() }
        },
        deletes: () => requests.filter(r => r.method === 'DELETE').map(r => r.url),
        touches: () => requests.filter(r => r.url === '/api/composer-attachments/touch/').map(r => r.body),
        async add(sessionId, name = 'a.png', type = 'image/png') {
            const record = await actions.addAttachment(sessionId, new File(['data'], name, { type }))
            await flush()
            return record
        },
        async ready(record) {
            uploads.complete(runtime[record.id].clientId)
            await flush()
        },
    }
}

const deleteUrl = ref => `/api/composer-attachments/${ref.bucket}/${ref.id}/`

// ── Snapshots ────────────────────────────────────────────────────────────────

test('a new snapshot stores attachment metadata only: no bytes, no cap, no mediasDropped', async () => {
    const env = createEnv()
    const record = await env.add('s1', 'huge.mov', 'video/quicktime')
    const attachments = snapshotAttachments(env.actions.getRecords('s1'))
    attachments[0].size = 40 * 1024 * 1024 // far above the legacy 8 MB cap
    const saved = toStoredInflightSend({ sessionId: 's1', text: '', attachments, optimisticShown: true })
    assert.equal(saved.attachments.length, 1)
    assert.equal(Object.hasOwn(saved, 'mediasDropped'), false)
    assert.deepEqual(Object.keys(saved.attachments[0]).sort(), ['bucket', 'id', 'kind', 'mimeType', 'name', 'size'])
    assert.equal(saved.attachments[0].bucket, record.bucket)
    assert.equal(saved.attachments[0].id, record.id)
    assert.equal(inflightAttachmentCount(saved), 1)
    // A plain copy: IndexedDB rejects the store's reactive proxies.
    assert.doesNotThrow(() => structuredClone(saved))
})

test('a legacy snapshot keeps its medias, the 8 MB cap and mediaCount', () => {
    const big = { id: 'm1', type: 'image', data: 'x'.repeat(9 * 1024 * 1024) }
    const saved = toStoredInflightSend({ sessionId: 's1', text: '', medias: [big] })
    assert.equal(saved.mediasDropped, true)
    assert.equal(saved.medias.length, 0)
    assert.equal(saved.mediaCount, 1)
    assert.equal(inflightAttachmentCount(saved), 1)
    const small = toStoredInflightSend({ sessionId: 's1', text: 'hi', medias: [{ id: 'm2', type: 'image', data: 'abc' }] })
    assert.equal(Object.hasOwn(small, 'mediasDropped'), false)
    assert.equal(small.medias.length, 1)
})

test('server failure after the composer forget: snapshot refs stay available for Retry and Edit', async () => {
    const env = createEnv()
    const first = await env.add('s1', 'one.png')
    const second = await env.add('s1', 'two.pdf', 'application/pdf')
    await env.ready(first)
    await env.ready(second)
    const originalRefs = toAttachmentRefs(env.actions.getRecords('s1'))
    const originalBucket = first.bucket
    const saved = toStoredInflightSend({ sessionId: 's1', text: '', attachments: snapshotAttachments(env.actions.getRecords('s1')) })

    // Post-send composer clear: forget only.
    await env.actions.forgetAttachments('s1')
    assert.deepEqual(env.actions.getRecords('s1'), [])
    assert.equal(env.rows.size, 0)
    assert.deepEqual(env.deletes(), [])

    // Retry resends the same refs, in order.
    const retryPayload = { attachments: snapshotAttachmentRefs(saved) }
    assert.deepEqual(retryPayload.attachments, originalRefs)

    // Edit appends after the attachments already in the composer.
    const kept = await env.add('s1', 'kept.txt', 'text/plain')
    const previousLastPosition = kept.position
    const restored = await env.actions.restoreDraftAttachmentRefs('s1', saved.attachments)
    await flush()
    assert.equal(restored.length, 2)
    assert.equal(restored[0].bucket, originalBucket)
    assert.equal(restored[0].id, first.id)
    assert.equal(restored[0].position, previousLastPosition + 1)
    assert.equal(restored[1].position, previousLastPosition + 2)
    assert.deepEqual(env.actions.getRecords('s1').map(r => r.name), ['kept.txt', 'one.png', 'two.pdf'])
    assert.equal(env.rows.get(first.id).sessionId, 's1')
    // Touched at once as draft refs, before any status request.
    assert.deepEqual(env.touches(), [{ refs: originalRefs, holder: 'draft' }])
    assert.equal(env.runtime[first.id].state, 'ready')
    assert.deepEqual(env.deletes(), [])
})

test('Edit after a delivered send (lost ack) restores missing chips', async () => {
    const meta = [{ bucket: 'b1', id: 'gone', name: 'gone.png', size: 3, mimeType: 'image/png', kind: 'image' }]
    const env = createEnv({ statuses: { 'b1/gone': { state: 'missing' } } })
    const restored = await env.actions.restoreDraftAttachmentRefs('s1', meta)
    await flush()
    assert.equal(restored[0].bucket, 'b1')
    assert.equal(env.runtime.gone.state, 'missing')
    assert.equal(env.runtime.gone.retryable, false)
})

test('Edit into an alias or recovered ephemeral id keeps the original bucket', async () => {
    const env = createEnv()
    const meta = [{ bucket: 'draft-id', id: 'a1', name: 'a.png', size: 3, mimeType: 'image/png', kind: 'image' }]
    env.aliases['draft-id'] = 'recovered-id'
    const restored = await env.actions.restoreDraftAttachmentRefs('recovered-id', meta)
    assert.equal(restored[0].sessionId, 'recovered-id')
    assert.equal(restored[0].bucket, 'draft-id')
    // Restoring through the old draft id lands in its canonical composer too.
    const again = await env.actions.restoreDraftAttachmentRefs('draft-id', [{ ...meta[0], id: 'a2' }])
    assert.equal(again[0].sessionId, 'recovered-id')
    assert.equal(again[0].bucket, 'draft-id')
    // An attachment already held is never duplicated.
    assert.deepEqual(await env.actions.restoreDraftAttachmentRefs('recovered-id', meta), [])
})

// ── Count matching ───────────────────────────────────────────────────────────

test('total counts: metadata, snapshots and optimistic bubbles share one match key', () => {
    const metadata = { twicc_attachments: { owner: 's', entries: [{ n: 1 }, { n: 2 }, { n: 3 }] } }
    assert.equal(attachmentCountForMessage(metadata, 1), 3)
    assert.equal(attachmentMatchKey('', attachmentCountForMessage(metadata, 1)), 'a:3')
    assert.equal(attachmentCountForMessage({}, 2), 2)
    assert.equal(attachmentCountForMessage(null, 0), 0)
    assert.equal(attachmentMatchKey('  hello ', 3), 't:hello')
    assert.equal(attachmentMatchKey('', 0), null)
    assert.equal(attachmentMatchKey(null, 0), null)

    const attachments = [
        { bucket: 'b', id: '1', name: 'one.png', size: 1, mimeType: 'image/png', kind: 'image', previewUrl: 'blob:local-1' },
        { bucket: 'b', id: '2', name: 'movie.mp4', size: 1, mimeType: 'video/mp4', kind: 'video' },
        { bucket: 'b', id: '3', name: 'two.png', size: 1, mimeType: 'image/png', kind: 'image', previewUrl: '/api/composer-attachments/b/3/content' },
    ]
    const snapshot = toStoredInflightSend({ sessionId: 's', text: '', attachments: snapshotAttachments(attachments.map((a, position) => ({ ...a, position }))) })
    assert.equal(attachmentMatchKey(snapshot.text, inflightAttachmentCount(snapshot)), 'a:3')

    const bubble = optimisticAttachmentFields(attachments)
    assert.equal(bubble.attachmentCount, 3)
    assert.deepEqual(bubble.attachmentItems.map(item => item.name), ['one.png', 'movie.mp4', 'two.png'])
    assert.deepEqual(bubble.attachmentItems.map(item => item.kind), ['image', 'video', 'image'])
    // Local object URLs only, never a staging content URL.
    assert.deepEqual(bubble.attachmentItems.map(item => item.src), ['blob:local-1', null, null])
    assert.equal(attachmentMatchKey('', attachmentCountForMessage(bubble, 0)), 'a:3')
    // The metadata total wins over the provider's native block count.
    assert.equal(attachmentCountForMessage({ ...bubble, twicc_attachments: { entries: [{}] } }, 0), 1)
})

// ── Binding ──────────────────────────────────────────────────────────────────

test('delayed Codex binding moves an attachment added after the first send, keeping its upload state', async () => {
    const env = createEnv()
    const sent = await env.add('draft', 'sent.png')
    await env.ready(sent)
    await env.actions.forgetAttachments('draft') // post-send clear
    const next = await env.add('draft', 'next.txt', 'text/plain') // still uploading
    const clientId = env.runtime[next.id].clientId
    const runtimeBefore = env.runtime[next.id]
    const file = env.actions.getFile(next.id)
    // The canonical composer already holds an attachment.
    const existing = await env.add('canonical', 'existing.pdf', 'application/pdf')

    env.aliases.draft = 'canonical'
    const viewBefore = composerRecordsFor(env.records, env.aliases, 'canonical').map(r => r.id)
    assert.deepEqual(viewBefore, [existing.id, next.id])
    assert.deepEqual(composerRecordsFor(env.records, env.aliases, 'draft').map(r => r.id), viewBefore)

    const moving = env.actions.rebindDraftAttachments('draft', 'canonical')
    // Published in memory at once: never an empty composer on either id.
    assert.deepEqual(composerRecordsFor(env.records, env.aliases, 'canonical').map(r => r.id), viewBefore)
    assert.deepEqual(composerRecordsFor(env.records, env.aliases, 'draft').map(r => r.id), viewBefore)
    await moving

    const moved = env.actions.getRecords('canonical').find(r => r.id === next.id)
    assert.equal(moved.sessionId, 'canonical')
    assert.equal(moved.bucket, 'draft')
    assert.equal(moved.position, existing.position + 1)
    assert.equal(env.records.draft, undefined)
    assert.equal(env.actions.getFile(next.id), file)
    assert.equal(env.runtime[next.id], runtimeBefore)
    assert.equal(env.runtime[next.id].state, 'uploading')
    assert.equal(env.runtime[next.id].clientId, clientId)
    // One readwrite transaction for the ownership change.
    assert.equal(env.bulkWrites.length, 1)
    assert.deepEqual(env.bulkWrites[0].map(r => [r.id, r.sessionId, r.bucket]), [[next.id, 'canonical', 'draft']])
    assert.equal(env.rows.get(next.id).sessionId, 'canonical')

    // The old draft deletion only forgets what is left: nothing.
    await env.actions.forgetAttachments('draft')
    assert.ok(env.rows.has(next.id))
    // The same upload attempt still drives the chip.
    await env.ready(next)
    assert.equal(env.runtime[next.id].state, 'ready')
    assert.deepEqual(env.deletes(), [])
    assert.deepEqual(env.uploads.cancels, [])
})

test('an addition during an awaited bind is canonical-owned through the alias and keeps its draft bucket', async () => {
    const env = createEnv()
    const existing = await env.add('canonical', 'existing.pdf', 'application/pdf')
    const unsent = await env.add('draft', 'unsent.png')
    env.aliases.draft = 'canonical'
    const late = await env.add('draft', 'late.txt', 'text/plain')
    assert.equal(late.sessionId, 'canonical')
    assert.equal(late.bucket, 'draft')
    const order = [existing.id, unsent.id, late.id]
    assert.deepEqual(composerRecordsFor(env.records, env.aliases, 'canonical').map(r => r.id), order)
    await env.actions.rebindDraftAttachments('draft', 'canonical')
    assert.deepEqual(env.actions.getRecords('canonical').map(r => r.id), order)
    assert.deepEqual(env.actions.getRecords('canonical').map(r => r.position), [0, 1, 2])
    assert.equal(env.rows.get(late.id).position, 2)
    // An addition after the move appends normally.
    const after = await env.add('draft', 'after.txt', 'text/plain')
    assert.equal(after.position, 3)
    assert.equal(after.bucket, 'draft')
})

test('a record write still pending during the bind ends with the canonical owner', async () => {
    const env = createEnv()
    const open = env.gateSaves()
    const adding = env.actions.addAttachment('draft', new File(['x'], 'slow.png', { type: 'image/png' }))
    await flush()
    const [record] = env.actions.getRecords('draft')
    await env.actions.rebindDraftAttachments('draft', 'canonical')
    open()
    await adding
    await flush()
    assert.equal(env.rows.get(record.id).sessionId, 'canonical')
    assert.equal(env.rows.get(record.id).bucket, 'draft')
})

// ── Heartbeat and ref collection ─────────────────────────────────────────────

test('heartbeat touches draft refs and snapshot refs separately', async () => {
    const env = createEnv()
    const record = await env.add('s1')
    const snapshotRefs = uniqueRefs([{ bucket: 'b', id: 'x' }, { bucket: 'b', id: 'x' }, { bucket: 'b', id: 'y' }])
    await env.actions.touchHeldAttachments({ snapshotRefs })
    assert.deepEqual(env.touches(), [
        { refs: [{ bucket: record.bucket, id: record.id }], holder: 'draft' },
        { refs: [{ bucket: 'b', id: 'x' }, { bucket: 'b', id: 'y' }], holder: 'snapshot' },
    ])
})

test('the ref collector reads memory before any removal, then storage, deduplicated by bucket/id', async () => {
    const records = { d: { a: { id: 'a', bucket: 'd', position: 0 } }, c: { b: { id: 'b', bucket: 'd', position: 0 } } }
    const inflight = new Map([['r1', { sessionId: 'c', attachments: [{ bucket: 'd', id: 'a' }, { bucket: 'd', id: 'f' }] }]])
    const failed = { c: { r2: { sessionId: 'c', attachments: [{ bucket: 'c', id: 'g' }] } }, other: { r3: { attachments: [{ bucket: 'o', id: 'z' }] } } }
    const collect = createAttachmentRefCollector({
        records,
        inflightEntries: () => inflight.values(),
        failedEntries: () => Object.values(failed).flatMap(Object.values),
        storage: {
            getDraftAttachmentsBySession: async id => (id === 'd' ? [{ id: 'stored', bucket: 'd' }, { id: 'a', bucket: 'd' }] : []),
            getAllInflightSends: async () => ({ r9: { sessionId: 'd', attachments: [{ bucket: 'd', id: 'old' }] }, r8: { sessionId: 'x', attachments: [{ bucket: 'x', id: 'no' }] } }),
        },
    })
    const pending = collect(['d', 'c'])
    // Removal right after the call cannot hide what memory held.
    delete records.d
    delete records.c
    inflight.clear()
    delete failed.c
    const refs = await pending
    assert.deepEqual(refs.map(r => `${r.bucket}/${r.id}`).sort(), ['c/g', 'd/a', 'd/b', 'd/f', 'd/old', 'd/stored'])
})

// ── Ephemeral discard, recovery and binding ──────────────────────────────────

function ephemeralStore(env, overrides = {}) {
    const inflight = new Map()
    const released = []
    const cleared = []
    const collect = createAttachmentRefCollector({
        records: env.records,
        inflightEntries: () => inflight.values(),
        failedEntries: () => Object.values(store.localState.failedSends).flatMap(Object.values),
        storage: { getDraftAttachmentsBySession: async () => [], getAllInflightSends: async () => ({}) },
    })
    const store = {
        sessions: { draft: { id: 'draft', project_id: 'p', provider: 'codex', draft: true, ephemeral: true } },
        processStates: {}, sessionItems: {},
        localState: {
            ephemeralControls: {}, draftAliases: env.aliases, optimisticMessages: {}, failedSends: {},
            attachmentRecords: env.records, attachmentRuntime: env.runtime,
        },
        _saveDraftToIndexedDB() {}, recomputeVisualItems() {}, rekeyMruSession() {}, removeMruSession() {},
        _rekeyEphemeralInflight(oldId, newId) { for (const entry of inflight.values()) if (entry.sessionId === oldId) entry.sessionId = newId },
        _clearEphemeralInflight(id) { for (const [key, entry] of inflight) if (entry.sessionId === id) inflight.delete(key); delete store.localState.failedSends[id] },
        _dropInflightSend(id) { inflight.delete(id) },
        rebindDraftAttachments: (oldId, newId) => env.actions.rebindDraftAttachments(oldId, newId),
        ...createEphemeralActions({
            uuid: () => 'recovered',
            saveControl: async () => {}, deleteControl: async () => {}, deleteSession: async () => {},
            clearContent: async id => { cleared.push(id); await env.actions.forgetAttachments(id) },
            stop: async () => {}, navigate: async () => {}, buildPrompt: () => ({}),
            collectAttachmentRefs: ids => collect(ids),
            releaseAttachmentRefs: async refs => { released.push(refs); await env.actions.releaseAttachments(refs) },
            ...overrides,
        }),
    }
    Object.assign(store, createSendFailureActions(inflight))
    return { store, inflight, released, cleared }
}

test('ephemeral Discard collects draft, canonical, failed-send and in-flight refs before purging, then releases them once', async () => {
    const env = createEnv()
    const { store, inflight, released, cleared } = ephemeralStore(env)
    const draftRecord = await env.add('draft', 'draft.png')
    store.promoteEphemeralSession('draft', { text: 'hello' })
    await store.bindEphemeralSession('draft', 'canonical')
    const canonicalRecord = await env.add('canonical', 'canonical.txt', 'text/plain')
    inflight.set('r1', { sessionId: 'canonical', attachments: [{ bucket: 'draft', id: 'sent' }, { bucket: draftRecord.bucket, id: draftRecord.id }] })
    store.localState.failedSends.canonical = { r2: { sessionId: 'canonical', attachments: [{ bucket: 'draft', id: 'failed' }] } }

    const discarding = store.discardEphemeralSession('canonical')
    assert.equal(store.sessions.canonical, undefined)
    assert.equal(inflight.size, 0)
    await discarding
    await flush()
    assert.equal(released.length, 1)
    const refs = released[0].map(r => `${r.bucket}/${r.id}`).sort()
    assert.deepEqual(refs, [`canonical/${canonicalRecord.id}`, `draft/${draftRecord.id}`, 'draft/failed', 'draft/sent'].sort())
    assert.deepEqual(env.deletes().sort(), released[0].map(deleteUrl).sort())
    assert.deepEqual(cleared.sort(), ['canonical', 'draft'])
    assert.equal(env.rows.size, 0)
})

test('internal ephemeral purges and recovery only forget locally', async () => {
    const env = createEnv()
    const { store, released } = ephemeralStore(env)
    const record = await env.add('draft', 'kept.png')
    store.promoteEphemeralSession('draft', { text: 'hello' })
    const recovered = store.recoverEphemeralDraft('draft')
    await flush()
    assert.equal(recovered, 'recovered')
    const moved = env.actions.getRecords('recovered')[0]
    assert.equal(moved.id, record.id)
    assert.equal(moved.bucket, 'draft')
    assert.equal(env.rows.get(record.id).sessionId, 'recovered')
    await store.purgeEphemeralContent(['recovered'])
    await flush()
    assert.deepEqual(released, [])
    assert.deepEqual(env.deletes(), [])
    assert.deepEqual(env.actions.getRecords('recovered'), [])
    assert.equal(env.rows.size, 0)
})

test('ephemeral binding rehomes unsent records before the old draft disappears', async () => {
    const env = createEnv()
    const { store } = ephemeralStore(env)
    const record = await env.add('draft', 'unsent.png')
    store.promoteEphemeralSession('draft', { text: 'hello' })
    await store.bindEphemeralSession('draft', 'canonical')
    await flush()
    assert.equal(env.records.draft, undefined)
    assert.equal(env.actions.getRecords('canonical')[0].id, record.id)
    assert.equal(env.actions.getRecords('canonical')[0].bucket, 'draft')
    assert.equal(env.rows.get(record.id).sessionId, 'canonical')
    assert.deepEqual(env.deletes(), [])
})

test('a persisted discard intention repeats the explicit release safely', async () => {
    const env = createEnv()
    const refs = [{ bucket: 'draft', id: 'stored' }]
    const releases = []
    const { store } = ephemeralStore(env, {
        collectAttachmentRefs: async () => refs,
        releaseAttachmentRefs: async value => { releases.push(value); await env.actions.releaseAttachments(value) },
    })
    await store.purgeEphemeralContent(['draft', 'canonical'], { releaseAttachments: true })
    await store.purgeEphemeralContent(['draft', 'canonical'], { releaseAttachments: true })
    await flush()
    assert.deepEqual(releases, [refs, refs])
    assert.deepEqual(env.deletes(), [deleteUrl(refs[0]), deleteUrl(refs[0])])
})

// ── Ownership matrix ─────────────────────────────────────────────────────────

test('ownership matrix: explicit releases call the endpoint; every other cleanup only forgets', async () => {
    const env = createEnv()
    const a = await env.add('s1', 'a.png')
    const b = await env.add('s1', 'b.png')
    const c = await env.add('s1', 'c.png')
    // Chip Remove: that ref only.
    await env.actions.releaseAttachments([{ bucket: a.bucket, id: a.id }])
    assert.deepEqual(env.deletes(), [deleteUrl(a)])
    // Remove all / Reset / Clear: the composer's refs.
    await env.actions.releaseAttachments(toAttachmentRefs(env.actions.getRecords('s1')))
    assert.deepEqual(env.deletes(), [deleteUrl(a), deleteUrl(b), deleteUrl(c)])
    // Failed-send Dismiss: the snapshot refs.
    const snapshot = { attachments: [{ bucket: 'old', id: 'x', name: 'x', size: 1, mimeType: '', kind: 'other' }] }
    await env.actions.releaseAttachments(snapshotAttachmentRefs(snapshot))
    assert.equal(env.deletes().at(-1), '/api/composer-attachments/old/x/')
    const releasesSoFar = env.deletes().length
    // Forget-only events: post-send clear, keepInStore and bind deletions, orphan cleanups.
    const d = await env.add('s2', 'd.png')
    await env.actions.forgetAttachments('s2')
    // Edit + snapshot resolve/expiry: restore then forget only.
    await env.actions.restoreDraftAttachmentRefs('s3', snapshot.attachments)
    await env.actions.forgetAttachments('s3')
    // Binding moves, never releases.
    await env.add('s4', 'e.png')
    await env.actions.rebindDraftAttachments('s4', 's5')
    assert.equal(env.deletes().length, releasesSoFar)
    assert.equal(env.rows.has(d.id), false)
})

const read = path => readFileSync(new URL(path, import.meta.url), 'utf8')
const between = (source, start, end) => source.slice(source.indexOf(start), source.indexOf(end, source.indexOf(start) + start.length))

test('ownership matrix: every cleanup caller uses its required operation', () => {
    const sessionView = read('../views/SessionView.vue')
    assert.match(sessionView, /store\.deleteDraftSession\(sessionId\.value, \{ releaseAttachments: true \}\)/)
    assert.match(sessionView, /store\.discardEphemeralSession\(sessionId\.value\)/)

    const listItem = read('../components/session/list/SessionListItem.vue')
    assert.match(listItem, /store\.deleteDraftSession\(session\.id, \{ releaseAttachments: true \}\)/)
    assert.match(listItem, /store\.discardEphemeralSession\(session\.id\)/)

    const selectionBar = read('../components/session/list/SessionSelectionBar.vue')
    assert.match(selectionBar, /store\.deleteDraftSession\(id, \{ releaseAttachments: true \}\)/)
    assert.match(selectionBar, /store\.discardEphemeralSession\(id\)/)

    const input = read('../components/message/MessageInput.vue')
    assert.match(between(input, 'function handleCancel(', '\n}\n'), /deleteDraftSession\(props\.sessionId, \{ releaseAttachments: true \}\)/)
    assert.match(between(input, 'async function handleReset(', '\n}\n'), /releaseComposerAttachments\(props\.sessionId\)/)
    assert.match(between(input, 'function removeAllAttachments(', '\n}\n'), /releaseComposerAttachments\(props\.sessionId\)/)
    const send = between(input, 'async function handleSend(', '\n}\n')
    assert.match(send, /deleteDraftSession\(props\.sessionId, \{ keepInStore: true \}\)/)
    assert.doesNotMatch(send, /releaseAttachments|releaseComposerAttachments/)

    const peer = read('../components/peer/PeerMessageReviewDialog.vue')
    const rollback = between(peer, 'async function rollbackDelivery(', '\n}\n')
    assert.match(rollback, /deleteDraftSession\(sessionId, \{ releaseAttachments: true \}\)/)
    assert.match(rollback, /releaseAttachments\(addedRecords\.map/)
    assert.doesNotMatch(rollback, /removeAttachment\(/)

    const banner = read('../components/session/detail/items/FailedSendBanner.vue')
    assert.match(between(banner, 'function discard(', '\n}\n'), /store\.releaseAttachments\(refs\)/)
    assert.doesNotMatch(between(banner, 'async function edit(', '\n}\n'), /releaseAttachments/)
    assert.match(between(banner, 'async function retry(', '\n}\n'), /snapshotAttachmentRefs\(entry\)/)

    const plan = read('../components/session/detail/items/codex/PlanImplementationBody.vue')
    const planCalls = plan.match(/deleteDraftSession\([^)]*\)/g)
    assert.deepEqual(planCalls, ['deleteDraftSession(draftId)', 'deleteDraftSession(draftId, { keepInStore: true })'])

    const data = read('../stores/data.js')
    // Inside the store, no deleteDraftSession caller releases (bind, cleanups).
    for (const call of data.match(/this\.deleteDraftSession\([^)]*\)/g)) assert.doesNotMatch(call, /releaseAttachments/)
    assert.match(between(data, 'async bindDraftSession(', '\n        },\n'), /rebindDraftAttachments\(draftId, sessionId\)/)
    for (const name of ['async cleanupOrphanDraftSessions(', 'async _dropOrphanAttachments(', '_dropInflightSend(requestId) {', 'removeFailedSend(sessionId, requestId) {', 'resolveInflightSends(sessionId, items) {']) {
        assert.doesNotMatch(between(data, name, '\n        },\n'), /releaseAttachments|releaseRef/, name)
    }
    const hydrateDrafts = between(data, 'async hydrateDraftSessions(', '\n        },\n')
    assert.match(hydrateDrafts, /purgeEphemeralContent\(\[draftId, control\.canonicalId\], \{ releaseAttachments: true \}\)/)
    assert.match(hydrateDrafts, /purgeEphemeralContent\(\[sessionId, draft\.ephemeralDraftId, control\.canonicalId\], \{ releaseAttachments: true \}\)/)
    assert.match(between(data, 'deleteDraftSession(sessionId, {', '\n        },\n'), /releaseAttachments \? this\.releaseAttachments\(/)

    const popover = read('../components/message/AgentSettingsPopover.vue')
    assert.doesNotMatch(popover, /forgetAttachments|releaseAttachments|attachmentRecords/)
})

test('upload-start notification follows persistence and precedes unresolved upload staging', async () => {
    const env = createEnv()
    const openSaves = env.gateSaves()
    let openUpload
    const uploadGate = new Promise(resolve => { openUpload = resolve })
    const originalStart = env.uploads.startUploads
    env.uploads.startUploads = async args => { await uploadGate; return originalStart(args) }
    const notified = []
    let completed = false
    const adding = env.actions.addAttachment('s1', new File(['shot'], 'shot.png', { type: 'image/png' }), {
        onUploadStarted: record => notified.push(record.id),
    }).then(record => { completed = true; return record })
    await flush()
    assert.deepEqual(notified, [])
    openSaves()
    await flush()
    assert.equal(notified.length, 1)
    assert.equal(completed, false)
    openUpload()
    const record = await adding
    assert.equal(notified[0], record.id)
    await env.actions.retryAttachment(record.id)
    assert.equal(notified.length, 1)
})

test('persistence failure does not notify upload-start', async () => {
    const env = createEnv()
    env.storage.saveDraftAttachment = async () => { throw Error('storage unavailable') }
    let notified = false
    await assert.rejects(env.actions.addAttachment('s1', new File(['shot'], 'shot.png'), {
        onUploadStarted: () => { notified = true },
    }), /storage unavailable/)
    assert.equal(notified, false)
})
