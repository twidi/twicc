import test from 'node:test'
import assert from 'node:assert/strict'
import { makeShareInlineAdapter } from './inlineAdapter.js'

const wire = (revision = 11, status = 'ready', enabled = true) => ({ revision, enabled, artifacts: [{
    source_session_id: 'root', artifact_id: 'widget', publication: ['root', 10, 2, 4],
    title: 'Widget', height: 360, entry_filename: 'index.html', status, code_revision: status === 'ready' ? 7 : null,
}] })
function fixture() {
    const calls = []
    const api = { fetchInlineManifest: async () => wire(), retryInlineArtifact: async (...args) => { calls.push(args); return wire(12) } }
    const store = { sharedSessionId: 'root', getSession: id => id === 'root' ? { id, type: 'session' } : { id, type: 'subagent' } }
    const adapter = makeShareInlineAdapter({ api, tokenPath: '/share/token/', store })
    return { adapter, calls }
}
test('normalizes public occurrence arrays and uses token-only broker routes', () => {
    const { adapter } = fixture()
    const manifest = adapter.acceptManifest(wire())
    const d = manifest.descriptors[0]
    assert.equal(d.publicationKey, '["root",10,2,4]')
    assert.equal(d.publication.line_num, 10)
    assert.equal(d.publication.text_block_index, 2)
    assert.equal(d.publication.tag_offset, 4)
    assert.equal(d.codeRevision, 7)
    assert.equal(adapter.documentUrl(d), '/share/token/inline-artifacts/root/widget/index.html')
    const config = adapter.brokerConfig(d)
    assert.equal(config.mode, 'share')
    assert.equal(config.proxyUrl, '/share/token/api/inline-artifacts/root/widget/proxy/')
})
test('older HTTP or WS manifests cannot restore readiness or disabled access', () => {
    const { adapter } = fixture()
    adapter.acceptManifest(wire(11))
    assert.equal(adapter.acceptManifest(wire(10, 'pending')), null)
    const disabled = adapter.acceptManifest(wire(12, 'not_included', false))
    assert.equal(disabled.descriptors[0].status, 'not_included')
    assert.equal(adapter.acceptManifest(wire(11)), null)
    assert.throws(() => adapter.documentUrl(disabled.descriptors[0]))
})
test('HEAD probes exact document URL and preserves authorization errors', async () => {
    const { adapter } = fixture()
    const d = adapter.acceptManifest(wire()).descriptors[0]
    const previous = globalThis.fetch
    globalThis.fetch = async (url, options) => {
        assert.equal(url, adapter.documentUrl(d)); assert.equal(options.method, 'HEAD')
        return new Response(null, { status: 401 })
    }
    try { assert.deepEqual(await adapter.probe(d, { signal: new AbortController().signal }), { available: false, error: 'share_password_required' }) }
    finally { globalThis.fetch = previous }
})
test('retry reconciles returned manifest and rejects child sources and stale descriptors', async () => {
    const { adapter, calls } = fixture()
    const d = adapter.acceptManifest(wire()).descriptors[0]
    let latest
    adapter.subscribe(manifest => { latest = manifest })
    await adapter.retry(d)
    assert.deepEqual(calls, [['root', 'widget', { signal: calls[0][2].signal }]])
    assert.equal(latest.revision, 12)
    const child = wire(13); child.artifacts[0].source_session_id = 'child'; child.artifacts[0].publication[0] = 'child'
    assert.deepEqual(adapter.acceptManifest(child).descriptors, [])
    assert.throws(() => adapter.documentUrl(d))
})
test('disposal aborts in-flight manifest fetch and ignores its result', async () => {
    let resolve, signal
    const adapter = makeShareInlineAdapter({ tokenPath: '/share/token', store: { sharedSessionId: 'root', getSession: () => ({ type: 'session' }) },
        api: { fetchInlineManifest: options => { signal = options.signal; return new Promise(r => { resolve = r }) } } })
    const pending = adapter.refresh()
    adapter.dispose()
    assert.equal(signal.aborted, true)
    resolve(wire())
    assert.equal(await pending, null)
})

test('public code replacement reloads once while data revisions retain the same frame', async () => {
    const { createPinia, setActivePinia } = await import('pinia')
    const { useFramePoolStore } = await import('../stores/framePool.js')
    const { createInlineArtifactRuntime } = await import('../inline-artifacts/runtime.js')
    setActivePinia(createPinia())
    const { adapter } = fixture()
    const pool = useFramePoolStore()
    const runtime = createInlineArtifactRuntime({ viewId: 'public', pool, adapter })
    adapter.subscribe(manifest => runtime.reconcile(manifest))
    const originalFetch = globalThis.fetch
    globalThis.fetch = async () => new Response(null, { status: 200 })
    const key = '["root","widget"]'
    try {
        adapter.acceptManifest(wire(11))
        const detach = runtime.attach(key, '["root",10,2,4]', { placeholderEl: {}, clipEl: {}, isSuppressed: () => false })
        runtime.setVisible(key, true)
        await Promise.resolve(); await Promise.resolve(); await Promise.resolve()
        const entry = runtime.entries.get(key), frame = pool.frames[entry.frameId], originalSrc = frame.src
        runtime.documentReady(key, entry.generation)
        detach()
        runtime.setActive(false)
        assert.equal(pool.frames[entry.frameId], frame)
        runtime.setActive(true)
        runtime.attach(key, '["root",10,2,4]', { placeholderEl: {}, clipEl: {}, isSuppressed: () => false })
        adapter.acceptManifest(wire(12))
        assert.equal(frame.src, originalSrc)
        const replacement = wire(13); replacement.artifacts[0].code_revision = 8
        adapter.acceptManifest(replacement)
        await Promise.resolve(); await Promise.resolve(); await Promise.resolve()
        assert.equal(pool.frames[entry.frameId], frame)
        assert.notEqual(frame.src, originalSrc)
        const newSrc = frame.src
        adapter.acceptManifest(replacement)
        assert.equal(frame.src, newSrc)
        adapter.acceptManifest(wire(14, 'not_included', false))
        assert.equal(frame.visible, false)
        assert.equal(adapter.acceptManifest(wire(13)), null)
        assert.equal(frame.visible, false)
    } finally { runtime.dispose(); globalThis.fetch = originalFetch }
})

test('normalization refuses another regular session and keeps its fixed root identity', () => {
    const store = { sharedSessionId: 'root', getSession: id => ({ id, type: 'session' }) }
    const adapter = makeShareInlineAdapter({ api: {}, tokenPath: '/share/token', store })
    const other = wire(11); other.artifacts[0].source_session_id = 'other'; other.artifacts[0].publication[0] = 'other'
    assert.deepEqual(adapter.acceptManifest(other).descriptors, [])
    store.sharedSessionId = 'other'
    assert.equal(adapter.acceptManifest(wire(12)).descriptors.length, 1)
})
