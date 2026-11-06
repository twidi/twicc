import test from 'node:test'
import assert from 'node:assert/strict'
import { createNativeTtsClient } from './nativeTtsClient.js'

const snapshot = (revision = 1, extra = {}) => ({ version: 1, revision, instanceId: 'instance', sessionStatus: 'connected', playback: { status: 'idle' }, listening: null, pendingCount: 0, results: [], ...extra })
function fixture({ capability = 1, server = true } = {}) {
    const calls = [], listeners = new Map(), scheduled = new Map(), delays = []
    let next = 0, current = snapshot()
    const document = { visibilityState: 'visible', addEventListener: (n, fn) => listeners.set(n, fn), removeEventListener: n => listeners.delete(n) }
    const appClient = { request(command, args) {
        calls.push({ command, args })
        if (command === 'getState') return Promise.resolve({ capabilities: { nativeTts: capability } })
        return Promise.resolve(command === 'tts.state' || command === 'tts.attach' ? { snapshot: current } : { accepted: true, operationId: args?.operationId, snapshot: current })
    } }
    const client = createNativeTtsClient({ appClient, document, fetchImpl: async () => ({ ok: true, json: async () => ({ protocol: 1, available: server }) }), timers: { setTimeout(fn, delay) { delays.push(delay); scheduled.set(++next, fn); return next }, clearTimeout(id) { scheduled.delete(id) } } })
    return { client, calls, scheduled, delays, document, listeners, set(value) { current = value }, async poll() { const [id, fn] = scheduled.entries().next().value; scheduled.delete(id); await fn() } }
}
for (const options of [{ capability: null }, { capability: 0 }, { server: false }]) test(`unsupported capability remains Web ${JSON.stringify(options)}`, async () => {
    const f = fixture(options)
    assert.equal(await f.client.attach(), false)
    assert.equal(f.scheduled.size, 0)
})
test('manual reserves synchronously, preserves large UTF8 selections, and resolves start/end once', async () => {
    const f = fixture(); assert.equal(await f.client.attach(), true)
    const text = 'é'.repeat(20000)
    const start = f.client.manual({ kind: 'text', source: 'selection', speech: { text }, text })
    const sent = f.calls.at(-1); assert.equal(sent.command, 'tts.manual'); assert.equal(sent.args.request.text, text)
    f.set(snapshot(3, { playback: { status: 'playing', operationId: sent.args.operationId, started: true } })); await f.poll()
    const result = await start; assert.equal(result.ok, true)
    f.set(snapshot(4, { results: [{ operationId: sent.args.operationId, started: true, start: { ok: true }, ended: { ok: true } }] })); await f.poll()
    assert.deepEqual(await result.ended, { ok: true })
    f.set(snapshot(2, { playback: { status: 'loading' } })); await f.poll(); assert.equal(f.client.state().revision, 4)
    f.client.detach(); assert.equal(f.scheduled.size, 0); assert.equal(f.calls.some(c => c.command === 'tts.stop_all'), false)
})
test('hidden page stops polling without stopping native speech; visible page reattaches mirror', async () => {
    const f = fixture(); await f.client.attach()
    const refreshed = []
    f.client.subscribe((value, refresh) => refreshed.push([value.revision, refresh]))
    f.document.visibilityState = 'hidden'; f.listeners.get('visibilitychange')(); assert.equal(f.scheduled.size, 0)
    f.document.visibilityState = 'visible'; await f.listeners.get('visibilitychange')(); assert.equal(f.scheduled.size, 1)
    assert.deepEqual(refreshed, [[1, true]], 'fresh native state reaches the page even when its revision does not change')
    assert.equal(f.calls.some(c => c.command === 'tts.stop_all'), false); f.client.detach()
})
test('expired native outcomes release pending controls without reporting a new failure', async () => {
    const f = fixture(); await f.client.attach()
    const start = f.client.manual({ kind: 'text', text: 'Hello' })
    f.set(snapshot(2, { playback: { status: 'playing', operationId: start.operationId, started: true } })); await f.poll()
    const result = await start
    f.set(snapshot(3)); await f.poll()
    assert.deepEqual(await result.ended, { ok: false, aborted: true })
    f.client.detach()
})
test('accepted failure and saved mismatch are terminal without replay', async () => {
    const f = fixture(); await f.client.attach()
    const start = f.client.manual({ kind: 'summary', summary: { saved_id: 7 } }); const id = f.calls.at(-1).args.operationId
    f.set(snapshot(3, { results: [{ operationId: id, started: false, start: { ok: false, error: 'Changed', summaryChanged: true, savedId: 7 }, ended: { ok: false } }], error: { summary_changed: true, saved_id: 7 } })); await f.poll()
    assert.deepEqual(await start, { ok: false, error: 'Changed', summaryChanged: true, savedId: 7 }); await f.poll()
    assert.equal(f.calls.filter(c => c.command === 'tts.manual').length, 1); f.client.detach()
})
test('a replacement, Skip, and Stop all resolve cancelled controls as aborted without an error toast', async () => {
    const f = fixture(); await f.client.attach()
    const first = f.client.manual({ kind: 'text', text: 'first' })
    const second = f.client.manual({ kind: 'text', text: 'second' })
    assert.deepEqual(await first, { ok: false, aborted: true })
    await f.client.skip(undefined, second.operationId)
    assert.equal(f.calls.at(-1).args.operationId, second.operationId)
    assert.deepEqual(await second, { ok: false, aborted: true })
    const third = f.client.manual({ kind: 'text', text: 'third' })
    await f.client.stopAll(); assert.deepEqual(await third, { ok: false, aborted: true }); f.client.detach()
})
test('pagehide during capability negotiation cannot attach a released document', async () => {
    const f = fixture()
    const attaching = f.client.attach(); f.client.detach()
    assert.equal(await attaching, false); assert.equal(f.scheduled.size, 0)
})
test('a cached supported client can reattach after a bfcache pagehide without restarting work', async () => {
    const f = fixture(); await f.client.attach(); f.client.detach()
    assert.equal(await f.client.attach(), true); assert.equal(f.scheduled.size, 1)
    assert.equal(f.calls.filter(c => c.command === 'getState').length, 1)
    assert.equal(f.calls.filter(c => c.command === 'tts.manual').length, 0); f.client.detach()
})

test('visible polling uses one second and visibility return refreshes immediately', async () => {
    const f = fixture(); await f.client.attach()
    assert.equal(f.delays.at(-1), 1000)
    f.document.visibilityState = 'hidden'; f.listeners.get('visibilitychange')()
    const before = f.calls.length
    f.document.visibilityState = 'visible'; await f.listeners.get('visibilitychange')()
    assert.equal(f.calls[before].command, 'tts.state')
    f.client.detach()
})
test('native notification cancellation and retained mismatch metadata reach their operation results', async () => {
    const f = fixture(); await f.client.attach()
    const pending = f.client.manual({ kind: 'summary', summary: { saved_id: 7 } })
    const operationId = pending.operationId
    f.set(snapshot(2, { results: [{ operationId, started: false, start: { ok: false, error: 'Changed', summaryChanged: true, savedId: 7 }, ended: { ok: false, error: 'Changed' } }], error: null })); await f.poll()
    assert.deepEqual(await pending, { ok: false, error: 'Changed', summaryChanged: true, savedId: 7 })
    const speaking = f.client.manual({ kind: 'text', text: 'Message' })
    f.set(snapshot(3, { playback: { operationId: speaking.operationId, started: true } })); await f.poll()
    const started = await speaking
    f.set(snapshot(4, { results: [{ operationId: speaking.operationId, started: true, start: { ok: true }, ended: { ok: false, aborted: true } }] })); await f.poll()
    assert.deepEqual(await started.ended, { ok: false, aborted: true }); f.client.detach()
})

test('supported stale attachment never selects Web or silently retries with a replacement document', async () => {
    const calls = []
    const client = createNativeTtsClient({ document: { visibilityState: 'visible' }, appClient: { async request(command) {
        calls.push(command)
        return command === 'getState' ? { capabilities: { nativeTts: 1 } } : { accepted: false, error: { code: 'stale_document' } }
    } }, fetchImpl: async () => ({ ok: true, json: async () => ({ protocol: 1, available: true }) }) })
    assert.equal(await client.attach(), true)
    assert.equal(client.state(), null)
    assert.equal((await client.manual({ kind: 'text' })).ok, false)
    assert.deepEqual(calls, ['getState', 'tts.attach'])
})
