import test from 'node:test'
import assert from 'node:assert/strict'
import { createPinia, setActivePinia } from 'pinia'
import { makeShareApi } from './shims/shareApi.js'
import { makeShareInlineAdapter } from './inlineAdapter.js'
import { createShareInlineCompletion } from './inlineCompletion.js'
import { createInlineArtifactRuntime } from '../inline-artifacts/runtime.js'
import { useFramePoolStore } from '../stores/framePool.js'

const key = '["root","widget"]'
function wire(revision, status) {
    return { enabled: status !== 'not_included', revision, artifacts: [{ source_session_id: 'root', artifact_id: 'widget',
        publication: ['root', 10, 0, 0], title: 'Widget', height: 360, entry_filename: 'index.html',
        status, code_revision: status === 'ready' ? 1 : null }] }
}
function fakeClock() {
    const jobs = new Map()
    let next = 0
    return {
        setTimer(fn, delay) { assert.equal(delay, 1000); jobs.set(++next, fn); return next },
        clearTimer(id) { jobs.delete(id) },
        async tick() { const job = jobs.entries().next().value; if (!job) return; jobs.delete(job[0]); await job[1]() },
        size: () => jobs.size,
    }
}
function fixture({ responses } = {}) {
    setActivePinia(createPinia())
    const pool = useFramePoolStore(), clock = fakeClock(), calls = []
    let closed = false, mode = 'snapshot'
    const api = makeShareApi('/share/token')
    const adapter = makeShareInlineAdapter({ api, tokenPath: api.base,
        store: { sharedSessionId: 'root', getSession: id => ({ id, type: 'session' }) } })
    const runtime = createInlineArtifactRuntime({ viewId: 'snapshot', pool, adapter })
    adapter.subscribe(manifest => runtime.reconcile(manifest))
    const completion = createShareInlineCompletion({ adapter, isSnapshot: () => mode === 'snapshot',
        accessClosed: () => closed, onAccessClosed: () => { closed = true; runtime.setActive(false) },
        setTimer: clock.setTimer, clearTimer: clock.clearTimer })
    const previous = globalThis.fetch
    globalThis.fetch = async (url, options) => {
        calls.push({ url, ...options })
        if (options.method === 'HEAD') return new Response(null, { status: 200 })
        const next = responses.shift()
        return typeof next === 'function' ? next(options) : Response.json(next)
    }
    return { adapter, runtime, pool, clock, completion, calls, responses,
        close() { closed = true; runtime.setActive(false); completion.accessChanged() },
        live() { mode = 'live'; completion.accessChanged() },
        dispose() { completion.dispose(); runtime.dispose(); globalThis.fetch = previous },
    }
}
async function settle() { for (let i = 0; i < 8; i++) await Promise.resolve() }

for (const result of ['ready', 'error']) {
    test(`focused snapshot automatically moves pending to ${result}, then stops completion fetches`, async () => {
        const f = fixture({ responses: [wire(1, 'pending'), wire(1, 'pending'), wire(2, result)] })
        try {
            await f.adapter.refresh() // The same boot request used by ShareSessionApp.
            f.runtime.attach(key, '["root",10,0,0]', { placeholderEl: {}, clipEl: {}, isSuppressed: () => false })
            f.runtime.setVisible(key, true)
            assert.equal(f.clock.size(), 1)
            assert.equal(f.runtime.loadedEntries.length, 0)
            await f.clock.tick()
            assert.equal(f.clock.size(), 1)
            await f.clock.tick()
            await settle()
            const entry = f.runtime.entries.get(key)
            assert.equal(entry.descriptor.status, result)
            assert.equal(f.runtime.loadedEntries.length, result === 'ready' ? 1 : 0)
            if (result === 'error') {
                assert.ok(entry.attachment) // Stable RuntimeHost retry chrome has its error placement.
                assert.equal(entry.descriptor.status, 'error')
            } else assert.ok(f.pool.frames[entry.frameId])
            assert.equal(f.clock.size(), 0)
            await f.clock.tick()
            assert.equal(f.calls.filter(call => call.method !== 'HEAD').length, 3)
        } finally { f.dispose() }
    })
}

test('a slow pending copy retains one temporary timer beyond sixty completion checks', async () => {
    const f = fixture({ responses: Array.from({ length: 66 }, () => wire(1, 'pending')) })
    try {
        await f.adapter.refresh()
        for (let i = 0; i < 65; i++) {
            assert.equal(f.clock.size(), 1)
            await f.clock.tick()
        }
        assert.equal(f.calls.length, 66)
        assert.equal(f.clock.size(), 1)
    } finally { f.dispose() }
})

test('focus refresh joins a completion fetch without a second request or timer', async () => {
    let release
    const f = fixture({ responses: [wire(1, 'pending'), () => new Promise(resolve => { release = resolve })] })
    try {
        await f.adapter.refresh()
        const tick = f.clock.tick()
        await settle()
        const focus = f.adapter.refresh().catch(() => null)
        const calls = f.calls.length
        assert.equal(f.clock.size(), 0)
        release(Response.json(wire(2, 'ready')))
        await Promise.all([tick, focus])
        assert.equal(calls, 2)
        assert.equal(f.clock.size(), 0)
    } finally { f.dispose() }
})

test('live shares use their channel and never start snapshot completion work', async () => {
    const f = fixture({ responses: [wire(1, 'pending')] })
    try { f.live(); await f.adapter.refresh(); assert.equal(f.clock.size(), 0) }
    finally { f.dispose() }
})

for (const action of ['close', 'dispose']) {
    test(`${action} aborts an active completion fetch and refuses its delayed ready result`, async () => {
        let release, signal
        const f = fixture({ responses: [wire(1, 'pending'), options => {
            signal = options.signal
            return new Promise(resolve => { release = resolve })
        }] })
        try {
            await f.adapter.refresh()
            const pending = f.clock.tick()
            await settle()
            assert.equal(signal.aborted, false)
            f[action]()
            assert.equal(signal.aborted, true)
            release(Response.json(wire(2, 'ready')))
            await pending
            assert.equal(f.clock.size(), 0)
            assert.equal(f.runtime.loadedEntries.length, 0)
            if (action === 'close') assert.equal(f.runtime.entries.get(key).descriptor.status, 'pending')
        } finally { f.dispose() }
    })
}

test('authorization failure closes access and stops completion work', async () => {
    const f = fixture({ responses: [wire(1, 'pending'), () => Response.json({ error: 'share_password_required' }, { status: 401 })] })
    try {
        await f.adapter.refresh(); await f.clock.tick()
        assert.equal(f.runtime.active.value, false)
        assert.equal(f.clock.size(), 0)
        assert.equal(f.calls.length, 2)
    } finally { f.dispose() }
})

test('disabled revision stops completion and delayed older readiness cannot restart it', async () => {
    const f = fixture({ responses: [wire(1, 'pending')] })
    try {
        await f.adapter.refresh()
        f.adapter.acceptManifest(wire(3, 'not_included'))
        assert.equal(f.clock.size(), 0)
        assert.equal(f.adapter.acceptManifest(wire(2, 'ready')), null)
        assert.equal(f.runtime.loadedEntries.length, 0)
    } finally { f.dispose() }
})

test('completed export error retains its actionable runtime Reload and then creates a ready frame', async () => {
    const f = fixture({ responses: [wire(1, 'pending'), wire(2, 'error'), wire(3, 'ready')] })
    try {
        await f.adapter.refresh()
        f.runtime.attach(key, '["root",10,0,0]', { placeholderEl: {}, clipEl: {}, isSuppressed: () => false })
        f.runtime.setVisible(key, true)
        await f.clock.tick()
        const entry = f.runtime.entries.get(key)
        assert.equal(entry.descriptor.status, 'error')
        assert.ok(entry.attachment)
        assert.equal(f.runtime.loadedEntries.length, 0)
        assert.equal(f.clock.size(), 0)
        await f.runtime.reload(key)
        await settle()
        assert.equal(entry.descriptor.status, 'ready')
        assert.equal(f.runtime.loadedEntries.length, 1)
        assert.equal(f.calls.filter(call => call.method === 'POST').length, 1)
        assert.equal(f.clock.size(), 0)
    } finally { f.dispose() }
})

test('a disabled revision aborts its completion fetch before a delayed older ready response', async () => {
    let release, signal
    const f = fixture({ responses: [wire(1, 'pending'), options => {
        signal = options.signal
        return new Promise(resolve => { release = resolve })
    }] })
    try {
        await f.adapter.refresh()
        const pending = f.clock.tick()
        await settle()
        f.adapter.acceptManifest(wire(3, 'not_included'))
        assert.equal(signal.aborted, true)
        release(Response.json(wire(2, 'ready')))
        await pending
        assert.equal(f.runtime.entries.get(key).descriptor.status, 'not_included')
        assert.equal(f.runtime.loadedEntries.length, 0)
        assert.equal(f.clock.size(), 0)
    } finally { f.dispose() }
})
