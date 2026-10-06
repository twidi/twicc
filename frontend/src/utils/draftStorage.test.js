// IndexedDB schema, draft attachment CRUD and the blocked-upgrade report of
// `draftStorage.js`, driven with a controlled IndexedDB fake: the test fires
// each open-request event (`blocked`, `upgradeneeded`, `success`) itself.
// Every test imports a fresh module instance (query string), so the cached
// connection promise of one test never leaks into another.

import { test } from 'node:test'
import assert from 'node:assert/strict'
import { installDraftStorageBlockedNotice, DRAFT_STORAGE_BLOCKED_MESSAGE } from './draftStorageBlockedNotice.js'

let moduleSeq = 0
function freshStorage() {
    moduleSeq += 1
    return import(`./draftStorage.js?case=${moduleSeq}`)
}

async function flush() {
    for (let i = 0; i < 10; i++) await new Promise(resolve => setImmediate(resolve))
}

// ── IndexedDB fake ───────────────────────────────────────────────────────────

function createObjectStoreFake(name, options = {}) {
    const rows = new Map()
    const indexes = new Map()
    const keyOf = (value, key) => (options.keyPath ? value[options.keyPath] : key)
    const request = run => {
        const req = { result: undefined, error: null, onsuccess: null, onerror: null }
        queueMicrotask(() => {
            try {
                req.result = run()
                req.onsuccess?.({ target: req })
            } catch (error) {
                req.error = error
                req.onerror?.({ target: req })
            }
        })
        return req
    }
    return {
        name,
        keyPath: options.keyPath ?? null,
        indexes,
        rows,
        createIndex(indexName, keyPath, indexOptions = {}) {
            indexes.set(indexName, { keyPath, unique: !!indexOptions.unique })
        },
        put(value, key) {
            return request(() => { rows.set(keyOf(value, key), structuredClone(value)) })
        },
        get(key) {
            return request(() => (rows.has(key) ? structuredClone(rows.get(key)) : undefined))
        },
        delete(key) {
            return request(() => { rows.delete(key) })
        },
        getAll() {
            return request(() => [...rows.values()].map(v => structuredClone(v)))
        },
        index(indexName) {
            const { keyPath } = indexes.get(indexName)
            return {
                getAll: value => request(() => [...rows.values()].filter(v => v[keyPath] === value).map(v => structuredClone(v))),
                getAllKeys: value => request(() => [...rows.entries()].filter(([, v]) => v[keyPath] === value).map(([k]) => k)),
            }
        },
    }
}

function createDatabaseFake(name, version, stores = new Map()) {
    const db = {
        name,
        version,
        stores,
        closed: 0,
        onversionchange: null,
        objectStoreNames: { contains: storeName => stores.has(storeName) },
        createObjectStore(storeName, options) {
            const store = createObjectStoreFake(storeName, options)
            stores.set(storeName, store)
            return store
        },
        deleteObjectStore(storeName) { stores.delete(storeName) },
        transaction(storeName) {
            // Requests settle in microtasks; the transaction completes after them.
            const tx = { oncomplete: null, onerror: null, onabort: null, objectStore: requested => stores.get(requested ?? storeName) }
            setImmediate(() => tx.oncomplete?.())
            return tx
        },
        close() { db.closed += 1 },
    }
    return db
}

/** A fake `indexedDB` whose open requests stay pending until the test fires their events. */
function createIndexedDbFake({ existingVersion = 0, existingStores = [] } = {}) {
    const stores = new Map()
    for (const [storeName, options] of existingStores) stores.set(storeName, createObjectStoreFake(storeName, options))
    let currentVersion = existingVersion
    const opens = []
    const fake = {
        opens,
        stores,
        open(name, version) {
            const request = { name, version, result: null, error: null, onsuccess: null, onerror: null, onblocked: null, onupgradeneeded: null }
            const control = {
                request,
                blocked() { request.onblocked?.({ target: request, oldVersion: currentVersion, newVersion: version }) },
                upgrade() {
                    const oldVersion = currentVersion
                    request.result = createDatabaseFake(name, version, stores)
                    request.onupgradeneeded?.({ target: request, oldVersion, newVersion: version })
                    currentVersion = version
                },
                succeed() {
                    if (!request.result) request.result = createDatabaseFake(name, version, stores)
                    request.onsuccess?.({ target: request })
                    return request.result
                },
            }
            opens.push(control)
            return request
        },
    }
    return fake
}

const VERSION_8_STORES = [
    ['ephemeralControls'],
    ['draftMessages'],
    ['draftSessions'],
    ['draftMedias', { keyPath: 'id' }],
    ['codeComments', { keyPath: ['projectId', 'sessionId', 'filePath', 'source', 'sourceRef', 'lineNumber'] }],
    ['inflightSends'],
    ['pendingRequestDrafts', { keyPath: ['sessionId', 'requestId'] }],
]

function installFake(options) {
    const fake = createIndexedDbFake(options)
    globalThis.indexedDB = fake
    return fake
}

/** Open the database through `getDb()` with an immediate upgrade and success. */
async function openReady(storage, fake) {
    const pending = storage.getDb()
    const control = fake.opens.at(-1)
    control.upgrade()
    control.succeed()
    return pending
}

// ── Schema ───────────────────────────────────────────────────────────────────

test('version 11 adds draftAttachments (keyPath id, non-unique sessionId index) and asyncQuestionDrafts, and keeps the v8 stores', async () => {
    const fake = installFake({ existingVersion: 8, existingStores: VERSION_8_STORES })
    const keptMedias = fake.stores.get('draftMedias')
    const keptComments = fake.stores.get('codeComments')
    const storage = await freshStorage()
    await openReady(storage, fake)
    assert.equal(fake.opens[0].request.version, 11)
    const store = fake.stores.get('draftAttachments')
    assert.ok(store)
    assert.equal(store.keyPath, 'id')
    assert.deepEqual(store.indexes.get('sessionId'), { keyPath: 'sessionId', unique: false })
    const questions = fake.stores.get('asyncQuestionDrafts')
    assert.ok(questions)
    assert.equal(questions.keyPath, null)
    // Stores of earlier versions are not recreated (their rows survive).
    assert.equal(fake.stores.get('draftMedias'), keptMedias)
    assert.equal(fake.stores.get('codeComments'), keptComments)
})

// The two lineages each created a v9 (and main a v10): one upgrade must add exactly
// the store each lacks, whatever the version it comes from.
for (const [label, existingVersion, extra, kept, added] of [
    ['v9 with draftAttachments only (attachments lineage)', 9, [['draftAttachments', { keyPath: 'id' }]], 'draftAttachments', 'asyncQuestionDrafts'],
    ['v9 with asyncQuestionDrafts only (questions lineage)', 9, [['asyncQuestionDrafts']], 'asyncQuestionDrafts', 'draftAttachments'],
    ['v10 with asyncQuestionDrafts only', 10, [['asyncQuestionDrafts']], 'asyncQuestionDrafts', 'draftAttachments'],
]) {
    test(`upgrade from ${label} creates the missing store and keeps the other`, async () => {
        const fake = installFake({ existingVersion, existingStores: [...VERSION_8_STORES, ...extra] })
        const existing = fake.stores.get(kept)
        const storage = await freshStorage()
        await openReady(storage, fake)
        assert.equal(fake.opens[0].request.version, 11)
        assert.equal(fake.stores.get(kept), existing, 'the store already there is not recreated')
        assert.ok(fake.stores.get(added))
        if (added === 'draftAttachments') {
            assert.deepEqual(fake.stores.get('draftAttachments').indexes.get('sessionId'), { keyPath: 'sessionId', unique: false })
        }
    })
}

test('the merged schema version is above both lineages', async () => {
    const source = (await import('node:fs')).readFileSync(new URL('./draftStorage.js', import.meta.url), 'utf8')
    assert.ok(Number(source.match(/const DB_VERSION = (\d+)/)[1]) > 10)
})

// ── CRUD ─────────────────────────────────────────────────────────────────────

test('draft attachment records: save, read all, read by session, delete', async () => {
    const fake = installFake()
    const storage = await freshStorage()
    await openReady(storage, fake)
    const a = { id: 'a', sessionId: 's1', bucket: 's1', position: 0, name: 'a.txt', size: 1, mimeType: 'text/plain', kind: 'text' }
    const b = { id: 'b', sessionId: 's1', bucket: 'old', position: 1, name: 'b.png', size: 2, mimeType: 'image/png', kind: 'image' }
    const c = { id: 'c', sessionId: 's2', bucket: 's2', position: 0, name: 'c', size: 3, mimeType: '', kind: 'other' }
    for (const record of [a, b, c]) await storage.saveDraftAttachment(record)
    assert.deepEqual((await storage.getAllDraftAttachments()).map(r => r.id).sort(), ['a', 'b', 'c'])
    assert.deepEqual((await storage.getDraftAttachmentsBySession('s1')).map(r => r.id).sort(), ['a', 'b'])
    assert.deepEqual(await storage.getDraftAttachmentsBySession('s1').then(rows => rows.find(r => r.id === 'b')), b)
    await storage.deleteDraftAttachment('a')
    assert.deepEqual((await storage.getAllDraftAttachments()).map(r => r.id).sort(), ['b', 'c'])
})

test('several draft attachment records saved in one transaction; one session deleted by index', async () => {
    const fake = installFake()
    const storage = await freshStorage()
    await openReady(storage, fake)
    const rows = [
        { id: 'a', sessionId: 'canonical', bucket: 'draft', position: 3, name: 'a', size: 1, mimeType: '', kind: 'other' },
        { id: 'b', sessionId: 'canonical', bucket: 'draft', position: 4, name: 'b', size: 1, mimeType: '', kind: 'other' },
        { id: 'c', sessionId: 'other', bucket: 'other', position: 0, name: 'c', size: 1, mimeType: '', kind: 'other' },
    ]
    await storage.saveDraftAttachments(rows)
    await storage.saveDraftAttachments([])
    assert.deepEqual((await storage.getDraftAttachmentsBySession('canonical')).map(r => [r.id, r.position]).sort(), [['a', 3], ['b', 4]])
    await storage.deleteDraftAttachmentsBySession('canonical')
    assert.deepEqual((await storage.getAllDraftAttachments()).map(r => r.id), ['c'])
})

// ── Blocked upgrade and versionchange ────────────────────────────────────────

test('a blocked upgrade is reported, the open request stays pending, and the report clears on success', async () => {
    const fake = installFake({ existingVersion: 8, existingStores: VERSION_8_STORES })
    const storage = await freshStorage()
    const reports = []
    const unsubscribe = storage.subscribeDraftStorageBlocked(blocked => reports.push(blocked))
    assert.deepEqual(reports, [false])
    let resolved = false
    const pending = storage.getDb().then(db => { resolved = true; return db })
    // An old version-8 tab keeps its connection open: the upgrade is blocked.
    fake.opens[0].blocked()
    await flush()
    assert.deepEqual(reports, [false, true])
    assert.equal(resolved, false)
    assert.equal(fake.opens.length, 1)
    // The user closes the old tab: the same request upgrades and succeeds.
    fake.opens[0].upgrade()
    fake.opens[0].succeed()
    const db = await pending
    assert.equal(resolved, true)
    assert.deepEqual(reports, [false, true, false])
    // An unsubscribed callback receives nothing more.
    unsubscribe()
    db.onversionchange({})
    storage.getDb()
    fake.opens[1].blocked()
    assert.deepEqual(reports, [false, true, false])
})

test('a late subscriber receives the current blocked state at once', async () => {
    const fake = installFake({ existingVersion: 8, existingStores: VERSION_8_STORES })
    const storage = await freshStorage()
    storage.getDb()
    fake.opens[0].blocked()
    const reports = []
    storage.subscribeDraftStorageBlocked(blocked => reports.push(blocked))
    assert.deepEqual(reports, [true])
})

test('versionchange closes the current connection and the next getDb() opens a new one', async () => {
    const fake = installFake()
    const storage = await freshStorage()
    const db = await openReady(storage, fake)
    assert.equal(typeof db.onversionchange, 'function')
    db.onversionchange({ oldVersion: 9, newVersion: 10 })
    assert.equal(db.closed, 1)
    const reopened = storage.getDb()
    assert.equal(fake.opens.length, 2)
    fake.opens[1].succeed()
    assert.notEqual(await reopened, db)
})

// ── Startup notice ───────────────────────────────────────────────────────────

function createDocumentFake() {
    const children = []
    const makeElement = tag => {
        const el = {
            tagName: tag.toUpperCase(),
            attributes: {},
            style: {},
            textContent: '',
            setAttribute(name, value) { el.attributes[name] = String(value) },
            remove() {
                const index = children.indexOf(el)
                if (index >= 0) children.splice(index, 1)
            },
        }
        return el
    }
    return {
        children,
        createElement: makeElement,
        body: { appendChild(el) { children.push(el); return el } },
    }
}

test('the blocked notice reaches the page before the awaited hydration, and clears after the upgrade', async () => {
    const fake = installFake({ existingVersion: 8, existingStores: VERSION_8_STORES })
    const storage = await freshStorage()
    const doc = createDocumentFake()
    // Bootstrap order of main.js: subscribe first, then await the hydration.
    const remove = installDraftStorageBlockedNotice(storage.subscribeDraftStorageBlocked, doc)
    assert.equal(doc.children.length, 0)
    let hydrated = false
    const hydration = storage.getAllDraftAttachments().then(rows => { hydrated = true; return rows })
    fake.opens[0].blocked()
    await flush()
    assert.equal(hydrated, false)
    assert.equal(doc.children.length, 1)
    const [notice] = doc.children
    assert.equal(notice.textContent, 'Draft storage upgrade blocked. Close other TwiCC tabs, then keep this page open.')
    assert.equal(notice.textContent, DRAFT_STORAGE_BLOCKED_MESSAGE)
    assert.equal(notice.attributes.role, 'alert')
    // The old tab is closed: the upgrade completes, the notice goes away, hydration ends.
    fake.opens[0].upgrade()
    fake.opens[0].succeed()
    assert.deepEqual(await hydration, [])
    assert.equal(hydrated, true)
    assert.equal(doc.children.length, 0)
    // Before the mount: unsubscribe. A later report shows nothing.
    remove()
    assert.equal(doc.children.length, 0)
})

test('removing the notice before the mount also removes a still-shown notice', async () => {
    const fake = installFake({ existingVersion: 8, existingStores: VERSION_8_STORES })
    const storage = await freshStorage()
    const doc = createDocumentFake()
    const remove = installDraftStorageBlockedNotice(storage.subscribeDraftStorageBlocked, doc)
    storage.getDb()
    fake.opens[0].blocked()
    assert.equal(doc.children.length, 1)
    remove()
    assert.equal(doc.children.length, 0)
})
