import assert from 'node:assert/strict'
import { it } from 'node:test'

const mainStores = ['ephemeralControls', 'draftMessages', 'draftSessions', 'draftMedias',
    'codeComments', 'inflightSends', 'pendingRequestDrafts']

// Node has no IndexedDB. Model its version gate and missing-store error, then
// run the production upgrade and transaction helpers against preserved records.
function database(version, names) {
    const records = new Map(names.map(name => [name, new Map([['existing', { preserved: name }]])]))
    const transactions = []
    const db = {
        version,
        objectStoreNames: { contains: name => records.has(name) },
        createObjectStore(name) {
            assert.equal(records.has(name), false)
            records.set(name, new Map())
            return { createIndex() {} }
        },
        deleteObjectStore() { assert.fail('An upgrade must preserve existing stores') },
        transaction(names) {
            const stores = Array.isArray(names) ? names : [names]
            if (stores.some(name => !records.has(name))) {
                throw new DOMException('Object store not found', 'NotFoundError')
            }
            const changes = []
            const tx = {
                objectStore: name => ({ put(value, key) { changes.push([name, key, structuredClone(value)]) } }),
                commit() {
                    for (const [name, key, value] of changes) records.get(name).set(key, value)
                    tx.oncomplete()
                },
                abort() { tx.onabort() },
            }
            transactions.push(tx)
            return tx
        },
    }
    const factory = { open(name, requestedVersion) {
        assert.equal(name, 'twicc')
        const request = { result: db }
        queueMicrotask(() => {
            if (requestedVersion < db.version) {
                request.error = new DOMException('Older database version', 'VersionError')
                request.onerror()
                return
            }
            if (requestedVersion > db.version) {
                const oldVersion = db.version
                db.version = requestedVersion
                request.onupgradeneeded({ oldVersion, target: request })
            }
            request.onsuccess()
        })
        return request
    } }
    return { db, factory, records, transactions }
}

const nextTurn = () => new Promise(resolve => setImmediate(resolve))

for (const fixture of [
    { name: 'v9 forking schema missing question drafts', version: 9, stores: [...mainStores, 'forkDrafts'] },
    { name: 'v8 main schema', version: 8, stores: mainStores },
    { name: 'v9 question schema with an unknown store', version: 9, stores: [...mainStores, 'asyncQuestionDrafts', 'unknownDrafts'] },
]) {
    it(`upgrades ${fixture.name}, preserving records and enabling durable answers and send staging`, async () => {
        const previous = globalThis.indexedDB
        const { db, factory, records, transactions } = database(fixture.version, fixture.stores)
        const original = structuredClone(records)
        globalThis.indexedDB = factory
        try {
            // Each import owns its lazy connection, as each application reload does.
            const storage = await import(`./draftStorage.js?schema=${fixture.version}-${fixture.name}`)
            assert.equal(await storage.getDb(), db)
            const draft = { message: 'Additional text' }
            const questions = { choices: { q1: { 0: { kind: 'option', value: 'Compact' } } } }
            let saved = false
            const recovery = storage.saveAsyncQuestionRecovery('s', draft, questions).then(() => { saved = true })
            // Attach before yielding so the RED missing-store rejection remains observed.
            recovery.catch(() => {})
            await nextTurn()
            if (!transactions.length) await recovery
            assert.equal(saved, false)
            transactions.at(-1).commit()
            await recovery
            assert.deepEqual(records.get('draftMessages').get('s'), draft)
            assert.deepEqual(records.get('asyncQuestionDrafts').get('s'), questions)

            const send = { sessionId: 's', rawText: 'Additional text', text: 'Formatted answers',
                asyncQuestions: { batch_ids: ['q1'], answers: [{ item_id: 'q1', question_index: 0, answer: 'Compact' }] } }
            let staged = false
            const staging = storage.stageAsyncQuestionSend('request', send, { message: '' }, { choices: {} })
                .then(() => { staged = true })
            await nextTurn()
            assert.equal(staged, false)
            assert.equal(records.get('inflightSends').has('request'), false)
            transactions.at(-1).commit()
            await staging
            assert.equal(records.get('inflightSends').get('request').status, 'staged')
            assert.equal(records.get('inflightSends').get('request').rawText, 'Additional text')
            assert.deepEqual(records.get('inflightSends').get('request').asyncQuestions, send.asyncQuestions)
            assert.deepEqual(records.get('draftMessages').get('s'), { message: '' })
            assert.deepEqual(records.get('asyncQuestionDrafts').get('s'), { choices: {} })
            for (const [name, entries] of original) {
                assert.deepEqual(records.get(name).get('existing'), entries.get('existing'), name)
            }
        } finally {
            globalThis.indexedDB = previous
        }
    })
}
