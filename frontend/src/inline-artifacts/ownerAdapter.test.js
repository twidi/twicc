import test from 'node:test'
import assert from 'node:assert/strict'
import { makeOwnerInlineAdapter } from './ownerAdapter.js'

const publication = { artifact_id: 'widget', line_num: 4, text_block_index: 0, tag_offset: 9,
    src: 'inline-artifacts/widget/index.html', title: 'Widget', height: 360 }
function fixture(type = 'session') {
    const session = { id: 'parent', type, inline_artifacts: { widget: publication } }
    const requests = []
    const store = { getSession: id => id === 'parent' ? session : null,
        artifactBookmarkFor: (id, path) => store.bookmark }
    const adapter = makeOwnerInlineAdapter({ sessionId: 'parent', store,
        api: async (url, options) => { requests.push({ url, options }); return { ok: true } } })
    return { session, store, adapter, requests }
}
test('owner normalizes only frozen regular-session publications', () => {
    const { adapter } = fixture()
    const first = adapter.manifest()
    assert.deepEqual(first.descriptors[0], { sourceSessionId: 'parent', artifactId: 'widget',
        publication, publicationKey: '["parent",4,0,9]', status: 'ready', title: 'Widget', height: 360,
        codeRevision: null })
    assert.ok(adapter.manifest().revision > first.revision)
    assert.deepEqual(fixture('subagent').adapter.manifest().descriptors, [])
})
test('owner probes the current published document and rejects foreign or stale sources', async () => {
    const { adapter, requests, session } = fixture()
    const descriptor = adapter.manifest().descriptors[0]
    assert.equal(adapter.documentUrl(descriptor), '/api/sessions/parent/inline-artifacts/widget/index.html')
    const controller = new AbortController()
    assert.deepEqual(await adapter.probe(descriptor, { signal: controller.signal }), { available: true })
    assert.equal(requests[0].options.method, 'HEAD')
    assert.equal(requests[0].options.signal, controller.signal)
    assert.throws(() => adapter.documentUrl({ ...descriptor, sourceSessionId: 'child' }), /source/)
    session.inline_artifacts = { widget: { ...publication, line_num: 5, src: 'inline-artifacts/widget/next.htm' } }
    assert.throws(() => adapter.documentUrl(descriptor), /publication/)
})
test('owner uses live bookmark grants and silent artifacts-root data writes', async () => {
    const { adapter, requests, store } = fixture()
    const config = adapter.brokerConfig(adapter.manifest().descriptors[0])
    assert.equal(config.inArtifactsRoot, true)
    assert.equal(config.getBookmarkId(), null)
    assert.deepEqual(config.getAllowedHosts(), {})
    store.bookmark = { id: 7, allowed_hosts: { a: {} }, denied_hosts: { b: {} } }
    assert.equal(config.getBookmarkId(), 7)
    assert.deepEqual(config.getAllowedHosts(), { a: {} })
    assert.deepEqual(config.getDeniedHosts(), { b: {} })
    await config.persistAllow('https://example.test', 'public')
    assert.equal(requests[0].url, '/api/artifact-bookmarks/7/allowed-hosts/')
})

test('a native child with the parent artifact ID never enters the private runtime', async () => {
    const { createInlineArtifactRuntime } = await import('./runtime.js')
    const { createPinia, setActivePinia } = await import('pinia')
    const { useFramePoolStore } = await import('../stores/framePool.js')
    const { adapter, requests, store } = fixture()
    const mainSession = store.getSession('parent')
    store.getSession = id => id === 'parent' ? mainSession : {
        id: 'child', type: 'subagent', inline_artifacts: { widget: publication } }
    setActivePinia(createPinia())
    const pool = useFramePoolStore()
    const runtime = createInlineArtifactRuntime({ viewId: 'parent', pool, adapter })
    runtime.reconcile(adapter.manifest())
    const key = '["parent","widget"]'
    runtime.attach(key, '["parent",4,0,9]', { isVisible: () => true })
    await new Promise(resolve => setImmediate(resolve))
    const entry = runtime.entries.get(key)
    runtime.documentReady(key, entry.generation)
    const frame = pool.frames[entry.frameId]
    runtime.setActive(false)
    const detachChild = runtime.attach('["child","widget"]', '["child",4,0,9]', { isVisible: () => true })
    assert.equal(runtime.entries.size, 1)
    assert.equal(runtime.loadedEntries.length, 1)
    assert.equal(requests.length, 1)
    assert.equal(frame.visible, false)
    detachChild()
    runtime.setActive(true)
    assert.equal(pool.frames[entry.frameId], frame)
    assert.equal(frame.visible, true)
    assert.equal(requests.length, 1)
    runtime.dispose()
})
