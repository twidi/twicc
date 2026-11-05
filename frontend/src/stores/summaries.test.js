import test from 'node:test'
import assert from 'node:assert/strict'
import { createPinia, setActivePinia } from 'pinia'
import { watch } from 'vue'
import { useSummariesStore } from './summaries.js'

const source = { session_id: 'session', line_num: 10 }
const descriptor = (overrides = {}) => ({ source_key: 'native', source_hash: 'hash', policy_key: 'auto', session_id: 'session', first_line: 10, text_lines: [10, 11], eligible: true, saved: null, previous: null, ...overrides })
const json = body => new Response(JSON.stringify(body), { headers: { 'Content-Type': 'application/json' } })
const tick = () => new Promise(resolve => setImmediate(resolve))
async function until(check) { for (let i = 0; i < 100; i++) { if (check()) return; await tick() } assert.fail('condition did not become true') }
function stream() {
    let controller
    return { response: new Response(new ReadableStream({ start(value) { controller = value } })),
        send(value) { controller.enqueue(new TextEncoder().encode(JSON.stringify(value) + '\n')) },
        bytes(value) { controller.enqueue(value) }, close() { controller.close() }, fail() { controller.error(new Error('Disconnected')) } }
}
function setup(overrides = {}) {
    setActivePinia(createPinia())
    const store = useSummariesStore()
    const wire = stream(), calls = []
    store._fetch = async (url, options = {}) => {
        const body = options.body && JSON.parse(options.body)
        calls.push({ url, ...options, body })
        if (options.method === 'DELETE') return new Response(null, { status: 204 })
        if (url.endsWith('resolve/')) return json({ sources: body.sources.map(() => descriptor(overrides)) })
        if (url === '/api/summaries/') return json({ id: 'handle', descriptor: descriptor(overrides), state: 'running' })
        if (url.includes('/saved/')) return json({ id: 8, text: 'Saved body', result_hash: 'saved-hash', source_key: 'native', source_hash: 'hash', policy_key: 'old' })
        return wire.response
    }
    return { store, wire, calls }
}

test('one stream joins equivalent lines and independent subscribers release only their own interest', async () => {
    const { store, wire, calls } = setup()
    const a = store.request(source, { manual: false }), b = store.request({ session_id: 'session', line_num: 11 }, { manual: false })
    await Promise.all([a.ready, b.ready])
    assert.equal(a.id, 'handle'); assert.equal(a.entry, b.entry)
    assert.equal(calls.filter(call => call.url === '/api/summaries/').length, 1)
    a.release(); await tick()
    assert.equal(calls.filter(call => call.method === 'DELETE').length, 0)
    wire.send({ revision: 1, state: 'running', text: 'Shared', saved_id: null })
    await until(() => b.entry.text === 'Shared')
    b.release(); await until(() => calls.some(call => call.method === 'DELETE'))
})

test('manual message survives view release and explicit cancel preserves another subscriber', async () => {
    const { store, wire, calls } = setup()
    const manual = store.request(source), equivalent = store.request(source), other = store.request(source, { manual: false })
    await Promise.all([manual.ready, equivalent.ready, other.ready]); manual.release(); equivalent.release(); store.cancel(manual.entry)
    await tick(); assert.equal(calls.filter(call => call.method === 'DELETE').length, 0)
    wire.send({ revision: 1, state: 'complete', text: 'Other remains', saved_id: 4, result_hash: 'four' })
    await until(() => other.entry.state === 'complete')
    assert.equal(other.entry.text, 'Other remains'); other.release()
})

test('navigation alone does not release a manual message and completion releases its detached owner', async () => {
    const { store, wire, calls } = setup()
    const sub = store.request(source); await sub.ready; sub.release(); await tick()
    assert.equal(calls.filter(call => call.method === 'DELETE').length, 0)
    wire.send({ revision: 1, state: 'complete', text: 'Done', saved_id: 4, result_hash: 'four' })
    await until(() => calls.some(call => call.method === 'DELETE'))
    assert.equal(sub.entry.state, 'complete')
})

test('arbitrary byte splits decode Unicode, replace fallback revisions, and preserve terminal failure', async () => {
    const { store, wire } = setup()
    const sub = store.request(source, { manual: false }); await sub.ready
    const bytes = new TextEncoder().encode(JSON.stringify({ revision: 1, state: 'running', text: 'Café 🐱' }) + '\n')
    for (const byte of bytes) wire.bytes(new Uint8Array([byte]))
    await until(() => sub.entry.text === 'Café 🐱')
    wire.send({ revision: 2, state: 'running', text: 'Fallback', engine: 'luna', fallback_reason: 'failed' })
    await until(() => sub.entry.revision === 2); assert.equal(sub.entry.text, 'Fallback')
    wire.send({ revision: 2, state: 'failed', text: 'Fallback', error: 'stalled' }); wire.close()
    await until(() => sub.entry.state === 'failed'); assert.equal(sub.entry.error, 'stalled'); sub.release()
})

test('resolve deduplicates concurrent lines, batches at 100, and never starts generation', async () => {
    const { store, calls } = setup()
    const sources = Array.from({ length: 205 }, (_, index) => ({ session_id: 'session', line_num: index + 1 }))
    const [a, b] = await Promise.all([store.resolve([...sources, sources[0]]), store.resolve([sources[0]])])
    assert.equal(a.length, 206); assert.equal(b.length, 1)
    const requests = calls.filter(call => call.url.endsWith('resolve/'))
    assert.deepEqual(requests.map(call => call.body.sources.length), [100, 100, 5])
    assert.equal(calls.length, 3)
})

test('start false is sent unchanged and saved lookup cannot generate', async () => {
    const { store, wire, calls } = setup()
    const sub = store.request(source, { start: false, manual: false }); await sub.ready
    assert.equal(calls.find(call => call.url === '/api/summaries/').body.start, false)
    const saved = await store.loadSaved(8)
    assert.equal(saved.text, 'Saved body'); assert.equal(saved.result_hash, 'saved-hash')
    wire.send({ revision: 1, state: 'complete', text: 'body', saved_id: 8, result_hash: 'exact-body-hash' })
    await until(() => sub.entry.state === 'complete')
    assert.equal(sub.entry.result_hash, 'exact-body-hash'); sub.release()
})

for (const phase of ['resolve', 'acquire', 'stream', 'truncated']) test(`${phase} network failure is visible without an unhandled promise`, async () => {
    const { store, wire } = setup()
    const original = store._fetch
    store._fetch = async (url, options) => {
        if ((phase === 'resolve' && url.endsWith('resolve/')) || (phase === 'acquire' && url === '/api/summaries/')) throw new Error('Offline')
        return original(url, options)
    }
    const sub = store.request(source, { manual: false })
    if (phase === 'resolve' || phase === 'acquire') await assert.rejects(sub.ready, /Offline/)
    else { await sub.ready; if (phase === 'stream') wire.fail(); else wire.close() }
    await until(() => sub.entry.state === 'failed'); assert.ok(sub.entry.error); sub.release()
})

test('session activity invalidates nothing: a finished message never changes', async () => {
    const { store } = setup()
    await store.resolve([source])
    for (const type of ['session_items_added', 'session_updated', 'process_state']) store.invalidate({ type, session_id: 'session' })
    assert.ok(store.descriptorFor(source)); assert.equal(store.versionFor(source), 0)
})

test('a removed session invalidates coordinates immediately and ignores an obsolete lookup response', async () => {
    const { store } = setup()
    await store.resolve([source]); assert.ok(store.descriptorFor(source))
    let finish
    const original = store._fetch
    store.invalidate({ type: 'session_removed', session_id: 'session' })
    assert.equal(store.descriptorFor(source), null)
    store._fetch = (url, opts) => url.endsWith('resolve/') ? new Promise(resolve => { finish = resolve }) : original(url, opts)
    const old = store.resolve([source]); await until(() => finish)
    store.invalidate({ type: 'session_removed', session_id: 'session' })
    finish(json({ sources: [descriptor()] })); await old
    assert.equal(store.descriptorFor(source), null)
})

test('saved notifications invalidate live and timeline metadata without altering frozen streamed body/hash', async () => {
    const { store, wire } = setup({ saved: { id: 8, result_hash: 'A' } })
    const event = { event_id: 20 }; await store.resolve([source, event])
    const sub = store.request(source, { manual: false }); await sub.ready
    wire.send({ revision: 1, state: 'complete', text: 'A', saved_id: 8, result_hash: 'A-hash' }); await until(() => sub.entry.state === 'complete')
    store.invalidate({ type: 'summary_saved', source_key: 'native', source_hash: 'hash', policy_key: 'auto', saved_id: 8 })
    assert.equal(store.descriptorFor(source), null); assert.equal(store.descriptorFor(event), null)
    assert.equal(sub.entry.text, 'A'); assert.equal(sub.entry.result_hash, 'A-hash'); sub.release()
})

test('temporary close and replacement cannot publish an old pending acquisition', async () => {
    const { store, calls } = setup()
    let finish
    const original = store._fetch
    store._fetch = (url, options) => url === '/api/summaries/' ? new Promise(resolve => { finish = resolve }) : original(url, options)
    store.openTemporary('Captured selection')
    const oldEntry = store.temporary.entry
    await until(() => finish)
    store.closeTemporary(); assert.equal(store.temporary, null)
    finish(json({ id: 'old', descriptor: descriptor({ source_key: null }), state: 'running' }))
    await until(() => calls.some(call => call.url === '/api/summaries/old/' && call.method === 'DELETE'))
    assert.equal(store.temporary, null); assert.equal(oldEntry.text, '')
    assert.equal(calls.filter(call => call.url.includes('resolve/')).length, 0)
})

test('equal selections stay independent and stores never share controllers', async () => {
    const first = setup(); const a = first.store.request({ text: 'same', purpose: 'selection' }), b = first.store.request({ text: 'same', purpose: 'selection' })
    await Promise.all([a.ready, b.ready]); assert.notEqual(a.entry, b.entry)
    a.release(); b.release()
    const second = setup(); const sub = second.store.request(source, { manual: false }); await sub.ready
    assert.equal(second.calls.filter(call => call.url === '/api/summaries/').length, 1); sub.release()
})

test('invalidation before the scheduled batch resolves the displaced caller and permits a fresh lookup', async () => {
    const { store } = setup()
    const old = store.resolve([source])
    store.invalidate({ type: 'session_removed', session_id: 'session' })
    const fresh = store.resolve([source])
    assert.equal((await old)[0], null)
    assert.equal((await fresh)[0].source_hash, 'hash')
})

test('retry replaces a temporary pending request without sharing ownership or accepting its late text', async () => {
    const { store, calls } = setup()
    const wires = [stream(), stream()]
    let acquired = 0
    const original = store._fetch
    store._fetch = async (url, options) => {
        if (url === '/api/summaries/') return json({ id: `temp${++acquired}`, descriptor: descriptor({ source_key: null }), state: 'running' })
        if (options?.method !== 'DELETE' && /temp[12]\/$/.test(url)) return wires[Number(url.at(-2)) - 1].response
        return original(url, options)
    }
    const first = store.openTemporary('Original selection'); await first.ready
    wires[0].send({ revision: 1, state: 'running', text: 'Old provisional' }); await until(() => first.entry.text)
    const second = store.retryTemporary(); await second.ready
    assert.notEqual(first.entry, second.entry); assert.equal(store.temporary.entry.text, '')
    wires[1].send({ revision: 1, state: 'complete', text: 'Replacement', saved_id: null }); await until(() => second.entry.state === 'complete')
    assert.equal(store.temporary.source.text, 'Original selection'); assert.equal(store.temporary.entry.text, 'Replacement')
    assert.equal(Object.keys(store.entries).length, 0)
    store.closeTemporary(); assert.equal(second.entry.text, '')
    await until(() => calls.filter(call => call.method === 'DELETE').length === 2)
})

test('policy and source changes clear current eligibility but preserve timeline snapshot identity', async () => {
    const { store } = setup()
    const event = { event_id: 2 }; await store.resolve([source, event])
    store.invalidate({ type: 'session_removed', session_id: 'session' })
    assert.equal(store.descriptorFor(source), null); assert.ok(store.descriptorFor(event))
    store.invalidate({ type: 'synced_settings_updated', settings: { summaryModel: 'luna' } })
    assert.equal(store.descriptorFor(event), null)
})

test('a newer saved fingerprint reacquires the current body while the older body/hash stay paired', async () => {
    const { store, wire, calls } = setup({ saved: { id: 8, result_hash: 'A-hash' } })
    const first = store.request(source, { manual: false }); await first.ready
    wire.send({ revision: 1, state: 'complete', text: 'A', saved_id: 8, result_hash: 'A-hash' }); await until(() => first.entry.state === 'complete')
    store.invalidate({ type: 'summary_saved', source_key: 'native', source_hash: 'hash', saved_id: 8 })
    const original = store._fetch, next = stream()
    store._fetch = async (url, options) => {
        if (url.endsWith('resolve/')) return json({ sources: [descriptor({ saved: { id: 8, result_hash: 'B-hash' } })] })
        if (url === '/api/summaries/handle/') return next.response
        return original(url, options)
    }
    await store.resolve([source]); assert.equal(store.entryFor(source), null)
    const second = store.request(source, { manual: false }); await second.ready
    assert.equal(calls.filter(call => call.url === '/api/summaries/').length, 2)
    next.send({ revision: 1, state: 'complete', text: 'B', saved_id: 8, result_hash: 'B-hash' }); await until(() => second.entry.state === 'complete')
    assert.equal(first.entry.text, 'A'); assert.equal(first.entry.result_hash, 'A-hash')
    assert.equal(second.entry.text, 'B'); assert.equal(second.entry.result_hash, 'B-hash')
    first.release(); second.release()
})

test('manual cancel during descriptor lookup publishes cancelled and never opens generation', async () => {
    const { store, calls } = setup()
    let finish
    store._fetch = async (url, options) => {
        calls.push({ url, ...options })
        return new Promise(resolve => { finish = resolve })
    }
    const sub = store.request(source); await until(() => finish)
    store.cancel(sub.entry)
    assert.equal(sub.entry.state, 'cancelled')
    await assert.rejects(sub.ready, /cancelled/)
    finish(json({ sources: [descriptor()] })); await tick()
    assert.equal(calls.some(call => call.url === '/api/summaries/'), false)
})


test('an active listener sees repeated invalidations while its descriptor remains null', async () => {
    const { store } = setup()
    await store.resolve([source])
    const responses = []
    store._fetch = () => new Promise(resolve => { responses.push(resolve) })
    const pending = []
    const stop = watch(() => store.versionFor(source), () => { pending.push(store.resolve([source])) }, { flush: 'sync' })
    store.invalidate({ type: 'session_removed', session_id: 'session' })
    await until(() => responses.length === 1)
    store.invalidate({ type: 'session_removed', session_id: 'session' })
    await until(() => responses.length === 2)
    responses[0](json({ sources: [descriptor({ source_hash: 'obsolete' })] }))
    responses[1](json({ sources: [descriptor({ source_hash: 'current' })] }))
    await Promise.all(pending)
    assert.equal(store.descriptorFor(source).source_hash, 'current')
    stop()
})

for (const withNonmanual of [false, true]) test(`cancel shares pending manual intent ${withNonmanual ? 'and preserves an independent nonmanual subscriber' : 'without acquiring a backend handle'}`, async () => {
    const { store, wire, calls } = setup()
    const original = store._fetch
    let finishLookup
    store._fetch = (url, options) => url.endsWith('resolve/')
        ? new Promise(resolve => { finishLookup = resolve })
        : original(url, options)
    const first = store.request({ ...source }), second = store.request({ ...source })
    const nonmanual = withNonmanual ? store.request({ ...source }, { manual: false }) : null
    await until(() => finishLookup)
    store.cancel(first.entry)
    assert.equal(first.entry.state, 'cancelled')
    assert.equal(second.entry.state, 'cancelled')
    await Promise.all([assert.rejects(first.ready, /cancelled/), assert.rejects(second.ready, /cancelled/)])
    finishLookup(json({ sources: [descriptor()] }))
    if (nonmanual) {
        await nonmanual.ready
        assert.equal(nonmanual.entry.state, 'running')
        wire.send({ revision: 1, state: 'complete', text: 'Independent result', saved_id: 4, result_hash: 'four' })
        await until(() => nonmanual.entry.state === 'complete')
        assert.equal(nonmanual.entry.text, 'Independent result')
        assert.equal(first.entry.state, 'cancelled'); assert.equal(second.entry.state, 'cancelled')
        assert.equal(calls.filter(call => call.method === 'DELETE').length, 0)
        nonmanual.release()
        await until(() => calls.some(call => call.method === 'DELETE'))
    } else {
        await tick(); await tick()
    }
    assert.equal(calls.filter(call => call.url === '/api/summaries/').length, withNonmanual ? 1 : 0)
})

test('temporary Cancel releases generation but retains captured source and partial result until Close', async () => {
    const { store, wire, calls } = setup()
    const sub = store.openTemporary('Captured source'); await sub.ready
    wire.send({ revision: 1, state: 'running', text: 'Partial sentence.' }); await until(() => sub.entry.text)
    const id = store.temporary.id
    store.cancelTemporary()
    assert.equal(store.temporary.id, id); assert.equal(store.temporary.source.text, 'Captured source')
    assert.equal(store.temporary.entry.text, 'Partial sentence.'); assert.equal(store.temporary.entry.state, 'cancelled')
    await until(() => calls.some(call => call.method === 'DELETE'))
    assert.equal(Object.keys(store.entries).length, 0)
    const cancelledEntry = store.temporary.entry, capturedSource = store.temporary.source
    store.closeTemporary(); assert.equal(store.temporary, null)
    assert.equal(cancelledEntry.text, ''); assert.equal(cancelledEntry.source.text, ''); assert.equal(capturedSource.text, '')
})

test('temporary Cancel before acquisition retains its source and releases a late backend handle', async () => {
    const { store, calls } = setup(); let finish
    const original = store._fetch
    store._fetch = (url, options) => url === '/api/summaries/' ? new Promise(resolve => { finish = resolve }) : original(url, options)
    const sub = store.openTemporary('Pending selection'); await until(() => finish)
    store.cancelTemporary(); await assert.rejects(sub.ready, /cancelled/)
    finish(json({ id: 'late', descriptor: null }))
    await until(() => calls.some(call => call.method === 'DELETE' && call.url.endsWith('/late/')))
    assert.equal(store.temporary.source.text, 'Pending selection'); assert.equal(store.temporary.entry.state, 'cancelled')
    store.closeTemporary()
})
