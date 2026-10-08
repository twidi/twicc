import { test } from 'node:test'
import assert from 'node:assert/strict'
import { createPinia, setActivePinia } from 'pinia'
import { connect, WindowMessenger } from 'penpal'
import { mountBrokerHost, createInlineBrokerMethods } from '../artifact-broker/host.js'
import { createInlineHeightObserver, intrinsicInlineHeight } from './heightObserver.js'
import { createInlineArtifactRuntime } from './runtime.js'
import { inlineArtifactBrokerConfig } from '../composables/useArtifactBroker.js'
import { useFramePoolStore } from '../stores/framePool.js'

async function fixture() {
    setActivePinia(createPinia())
    const pool = useFramePoolStore()
    const runtime = createInlineArtifactRuntime({ viewId: 'v', pool, adapter: {
        probe: async () => ({ available: true }), documentUrl: () => '/doc.html',
        brokerConfig: () => ({}), retry: async () => {}, dispose() {},
    } })
    const key = '["s","a"]'
    runtime.reconcile({ revision: 1, descriptors: [{ sourceSessionId: 's', artifactId: 'a',
        publicationKey: 'p', codeRevision: null, status: 'ready', title: 'A', height: 360 }] })
    runtime.attach(key, 'p', { isVisible: () => true })
    await Promise.resolve(); await Promise.resolve()
    const entry = runtime.entries.get(key)
    const methods = createInlineBrokerMethods(inlineArtifactBrokerConfig(entry, runtime, 'https://example.com'))
    methods.reportInlineReady({ inlineGeneration: 1 })
    return { runtime, entry, key, pool, methods }
}

test('bound generation, finite values and actual host mode protect remembered inline height', async () => {
    const f = await fixture()
    for (const height of [NaN, Infinity, -Infinity, '200']) f.methods.reportInlineHeight({ height, mode: 'inline', inlineGeneration: 1 })
    assert.equal(f.entry.inlineHeight, 360)
    f.methods.reportInlineHeight({ height: 159, mode: 'inline', inlineGeneration: 1 })
    assert.equal(f.entry.inlineHeight, 160)
    f.methods.reportInlineHeight({ height: 901, mode: 'inline', inlineGeneration: 1 })
    assert.equal(f.entry.inlineHeight, 900)
    f.methods.reportInlineHeight({ height: 200, mode: 'inline', inlineGeneration: 0 })
    f.runtime.openFullscreen(f.key)
    f.methods.reportInlineHeight({ height: 1800, mode: 'fullscreen', inlineGeneration: 1 })
    f.methods.reportInlineHeight({ height: 200, mode: 'inline', inlineGeneration: 1 })
    assert.equal(f.entry.inlineHeight, 900)
    f.runtime.closeFullscreen()
    f.methods.reportInlineHeight({ height: 200, mode: 'fullscreen', inlineGeneration: 1 })
    assert.equal(f.entry.inlineHeight, 900)
    f.runtime.dispose()
})

test('Escape from another bound frame or stale document cannot close fullscreen', async () => {
    const f = await fixture()
    f.runtime.openFullscreen(f.key)
    createInlineBrokerMethods({ inlineGeneration: 1, onInlineEscape: () => f.runtime.requestEscape('other', 1) })
        .requestInlineEscape({ inlineGeneration: 1 })
    f.methods.requestInlineEscape({ inlineGeneration: 0 })
    assert.equal(f.runtime.fullscreenArtifactKey.value, f.key)
    f.methods.requestInlineEscape({ inlineGeneration: 1 })
    assert.equal(f.runtime.fullscreenArtifactKey.value, null)
    f.runtime.dispose()
})

test('intrinsic content excludes a viewport-sized body and observer feedback settles', async () => {
    let childHeight = 200, callback, disconnected = false, reports = [], resize
    const observed = new Set()
    const listeners = new Map()
    const win = { location: { href: 'https://example.com/doc.html?_twicc_reload=7' },
        addEventListener: (key, fn) => listeners.set(key, fn), removeEventListener: key => listeners.delete(key),
        getComputedStyle: () => ({ marginBottom: '0', marginTop: '0' }) }
    const child = { tagName: 'DIV', getBoundingClientRect: () => ({ bottom: childHeight }) }
    const doc = { body: { getBoundingClientRect: () => ({ top: 0, height: 900 }), children: [child], childNodes: [child] },
        documentElement: {}, readyState: 'complete', addEventListener() {}, removeEventListener() {} }
    assert.equal(intrinsicInlineHeight(doc, win), 200)
    const observer = createInlineHeightObserver({ document: doc, window: win,
        getHost: async () => ({ reportInlineReady: async () => {},
            getInlineState: async () => ({ inlineGeneration: 7, mode: 'inline', requestedHeight: 360 }),
            reportInlineHeight: async report => reports.push(report), requestInlineEscape: async () => {} }),
        requestFrame: fn => { callback = fn; return 1 }, cancelFrame: () => { callback = null },
        ResizeObserver: class { constructor(fn) { resize = fn } observe(element) { observed.add(element) } disconnect() { disconnected = true } },
        MutationObserver: class { observe() {} disconnect() {} }, now: () => 0,
    })
    const flush = async () => { const fn = callback; callback = null; await fn?.(); await Promise.resolve() }
    await flush()
    observer.schedule(); await flush()
    assert.equal(reports.length, 1)
    childHeight = 220
    if (observed.has(child)) resize()
    await flush()
    assert.equal(reports.at(-1).height, 220)
    for (let i = 0; i < 30; i++) { childHeight = i % 2 ? 220 : 200; observer.schedule(); await flush() }
    assert.ok(reports.length <= 10)
    assert.equal(reports.at(-1).height, 360)
    observer.dispose()
    assert.equal(disconnected, true)
    assert.equal(listeners.has('keydown'), false)
    observer.schedule()
    assert.equal(callback, null)
})

test('old connected shim cannot acknowledge current document; a new SYN reconnects the retained host', async () => {
    const previousWindow = globalThis.window
    const previousLocation = globalThis.location
    const listeners = new Set()
    const dispatch = (source, data, ports) => queueMicrotask(() => {
        for (const fn of listeners) fn({ source, origin: 'https://example.com', data, ports: ports || [] })
    })
    const parent = { postMessage: (data, options) => dispatch(child, data, options?.transfer) }
    const child = { postMessage: (data, options) => dispatch(parent, data, options?.transfer) }
    globalThis.window = { addEventListener: (_, fn) => listeners.add(fn), removeEventListener: (_, fn) => listeners.delete(fn) }
    globalThis.location = { origin: 'https://example.com' }
    let ready = 0
    let host, oldShim, newShim
    try {
        host = mountBrokerHost({ contentWindow: child }, { documentUrl: 'https://example.com/doc.html',
            inlineGeneration: 2, onInlineReady: () => ready++ })
        oldShim = connect({ messenger: new WindowMessenger({ remoteWindow: parent, allowedOrigins: ['*'] }), timeout: 1000 })
        const oldRemote = await oldShim.promise
        await host.promise
        await oldRemote.reportInlineReady({ inlineGeneration: 1 })
        assert.equal(ready, 0)
        oldShim.destroy()
        newShim = connect({ messenger: new WindowMessenger({ remoteWindow: parent, allowedOrigins: ['*'] }), timeout: 1000 })
        const newRemote = await newShim.promise
        await newRemote.reportInlineReady({ inlineGeneration: 2 })
        assert.equal(ready, 1)
    } finally {
        newShim?.destroy(); oldShim?.destroy(); host?.destroy()
        assert.equal(listeners.size, 0)
        globalThis.window = previousWindow
        globalThis.location = previousLocation
    }
})
