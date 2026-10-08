import { test } from 'node:test'
import assert from 'node:assert/strict'
import { createPinia, setActivePinia } from 'pinia'
import { watchEffect } from 'vue'
import { useFramePoolStore } from '../stores/framePool.js'
import { createInlineArtifactRuntime } from './runtime.js'

const key = '["session-a","calculator"]'
const publication = '["session-a",10,0,0]'
function descriptor(overrides = {}) {
    return {
        sourceSessionId: 'session-a', artifactId: 'calculator',
        publication: { line_num: 10, text_block_index: 0, tag_offset: 0,
            src: 'inline-artifacts/calculator/index.html' },
        publicationKey: publication, status: 'ready', title: 'Calculator', height: 360,
        codeRevision: null, ...overrides,
    }
}
function fixture(viewId = 'view-a', sharedPool) {
    setActivePinia(createPinia())
    const pool = sharedPool || useFramePoolStore()
    const probes = []
    let disposals = 0
    const retries = []
    const adapter = {
        documentUrl: d => `/documents/${d.sourceSessionId}/${d.artifactId}/${d.publication.src.split('/').at(-1)}`,
        brokerConfig: d => ({ document: d.publication.src }),
        probe: (d, { signal }) => new Promise((resolve, reject) => probes.push({ d, signal, resolve, reject })),
        retry: d => { retries.push(d); return Promise.resolve() },
        dispose: () => disposals++,
    }
    const runtime = createInlineArtifactRuntime({ viewId, pool, adapter })
    const attachment = { placeholderEl: {}, clipEl: {}, isSuppressed: () => false,
        focusConversation: () => {} }
    const reconcile = (revision = 1, d = descriptor()) => runtime.reconcile({ revision, descriptors: [d] })
    const entry = () => runtime.entries.get(key)
    const succeed = async (index = probes.length - 1) => {
        probes[index].resolve({ available: true, error: null })
        await Promise.resolve()
        await Promise.resolve()
    }
    return { runtime, pool, adapter, probes, retries, attachment, reconcile, entry, succeed, disposals: () => disposals }
}

// These tests catch early iframe allocation, row-owned disposal, and lost frame identity.
test('actual visibility gates probing and frame allocation; detaching retains its loaded frame', async () => {
    const f = fixture()
    f.reconcile()
    const detach = f.runtime.attach(key, publication, f.attachment)
    assert.equal(f.probes.length, 0)
    assert.deepEqual(Object.keys(f.pool.frames), [])
    f.runtime.setVisible(key, true)
    assert.equal(f.entry().loadState, 'probing')
    await f.succeed()
    const entry = f.entry()
    const frame = f.pool.frames[entry.frameId]
    assert.ok(frame)
    assert.equal(frame.attrs.sandbox, 'allow-scripts allow-same-origin allow-forms')
    assert.equal(frame.visible, true)
    assert.equal(entry.loadState, 'loading')
    frame.onLoad()
    assert.equal(entry.loadState, 'loading')
    f.runtime.documentReady(key, entry.generation)
    assert.equal(entry.loadState, 'ready')
    detach()
    assert.equal(f.pool.frames[entry.frameId], frame)
    assert.equal(frame.visible, false)
    f.runtime.dispose()
    assert.equal(f.pool.frames[entry.frameId], undefined)
})

test('loaded entries and frame keys append in first-visibility order across source sessions', async () => {
    const f = fixture()
    const second = descriptor({ sourceSessionId: 'session-b', publicationKey: '["session-b",10,0,0]' })
    const secondKey = '["session-b","calculator"]'
    f.runtime.reconcile({ revision: 1, descriptors: [descriptor(), second] })
    f.runtime.attach(secondKey, second.publicationKey, f.attachment)
    f.runtime.setVisible(secondKey, true)
    await f.succeed()
    f.runtime.attach(key, publication, f.attachment)
    f.runtime.setVisible(key, true)
    await f.succeed()
    const frames = Object.keys(f.pool.frames)
    assert.equal(frames.length, 2)
    assert.notEqual(frames[0], frames[1])
    assert.deepEqual(f.runtime.loadedEntries.map(e => e.artifactKey), [secondKey, key])
    f.runtime.reconcile({ revision: 2, descriptors: [second, descriptor()] })
    assert.deepEqual(Object.keys(f.pool.frames), frames)
    f.runtime.dispose()
})

test('duplicate presentations share one frame and select the first visible attachment', async () => {
    const f = fixture()
    f.reconcile()
    let firstVisible = false
    const first = { ...f.attachment, isVisible: () => firstVisible }
    const second = { ...f.attachment, placeholderEl: { second: true }, isVisible: () => true }
    f.runtime.attach(key, publication, first)
    const detachSecond = f.runtime.attach(key, publication, second)
    f.runtime.setVisible(key, true)
    await f.succeed()
    assert.equal(f.entry().attachment, second)
    firstVisible = true
    f.runtime.setVisible(key, true)
    assert.equal(f.entry().attachment, first)
    firstVisible = false
    f.runtime.setVisible(key, false)
    assert.equal(f.entry().attachment, second)
    assert.equal(f.pool.frames[f.entry().frameId].visible, true)
    firstVisible = true
    f.runtime.setVisible(key, false)
    assert.equal(f.entry().attachment, first)
    detachSecond()
    assert.equal(f.entry().attachment, first)
    assert.equal(Object.keys(f.pool.frames).length, 1)
    assert.equal(f.probes.length, 1)
    f.runtime.dispose()
})

test('an invisible duplicate hint cannot abort a visible attachment probe', async () => {
    const f = fixture()
    f.reconcile()
    const first = { ...f.attachment, isVisible: () => false }
    const second = { ...f.attachment, isVisible: () => true }
    f.runtime.attach(key, publication, first)
    f.runtime.attach(key, publication, second)
    f.runtime.setVisible(key, true)
    f.runtime.setVisible(key, false)
    assert.equal(f.entry().attachment, second)
    assert.equal(f.probes[0].signal.aborted, false)
    await f.succeed()
    assert.equal(f.pool.frames[f.entry().frameId].visible, true)
    assert.equal(f.probes.length, 1)
    f.runtime.dispose()
})

test('mixed attachments use getter visibility and the boolean fallback independently', async () => {
    const f = fixture()
    f.reconcile()
    let getterVisible = false
    const fallback = f.attachment
    const withGetter = { ...f.attachment, isVisible: () => getterVisible }
    f.runtime.attach(key, publication, fallback)
    f.runtime.attach(key, publication, withGetter)
    f.runtime.setVisible(key, false)
    assert.equal(f.entry().attachment, null)
    assert.equal(f.probes.length, 0)
    assert.deepEqual(Object.keys(f.pool.frames), [])
    getterVisible = true
    f.runtime.setVisible(key, false)
    assert.equal(f.entry().attachment, withGetter)
    await f.succeed()
    const frame = f.pool.frames[f.entry().frameId]
    f.runtime.setVisible(key, true)
    assert.equal(f.entry().attachment, fallback)
    f.runtime.setVisible(key, false)
    assert.equal(f.entry().attachment, withGetter)
    assert.equal(frame.visible, true)
    getterVisible = false
    f.runtime.setVisible(key, false)
    assert.equal(frame.visible, false)
    f.runtime.setVisible(key, true)
    assert.equal(f.entry().attachment, fallback)
    assert.equal(frame.visible, true)
    getterVisible = true
    f.runtime.setActive(false)
    assert.equal(f.entry().attachment, null)
    assert.equal(frame.visible, false)
    f.runtime.setActive(true)
    assert.equal(frame.visible, true)
    assert.equal(f.probes.length, 1)
    f.runtime.dispose()
})

test('suppressed and inactive placements do not load; inactive loaded entries retain frame identity', async () => {
    const f = fixture()
    f.reconcile()
    let suppressed = true
    f.runtime.attach(key, publication, { ...f.attachment, isSuppressed: () => suppressed })
    f.runtime.setVisible(key, true)
    assert.equal(f.probes.length, 0)
    suppressed = false
    f.runtime.setActive(false)
    f.runtime.setVisible(key, true)
    assert.equal(f.probes.length, 0)
    f.runtime.setActive(true)
    await f.succeed()
    const frame = f.pool.frames[f.entry().frameId]
    f.runtime.setActive(false)
    assert.equal(frame.visible, false)
    assert.equal(f.runtime.loadedEntries.length, 1)
    f.runtime.setActive(true)
    assert.equal(frame.visible, true)
    assert.equal(f.probes.length, 1)
    f.runtime.dispose()
})

test('corrections clear placement authority, close fullscreen, and reject an old failed probe', async () => {
    const f = fixture()
    f.reconcile()
    f.runtime.attach(key, publication, f.attachment)
    f.runtime.setVisible(key, true)
    await f.succeed()
    const frame = f.pool.frames[f.entry().frameId]
    f.runtime.openFullscreen(key)
    await f.runtime.reload(key)
    const staleProbe = f.probes[1]
    f.reconcile(2, descriptor({ publicationKey: '["session-a",20,0,0]',
        publication: { line_num: 20, text_block_index: 0, tag_offset: 0, src: 'inline-artifacts/calculator/new.html' } }))
    assert.equal(frame.visible, false)
    assert.equal(f.entry().attachment, null)
    assert.equal(f.runtime.fullscreenArtifactKey.value, null)
    assert.equal(staleProbe.signal.aborted, true)
    f.runtime.attach(key, '["session-a",20,0,0]', f.attachment)
    f.runtime.setVisible(key, true)
    await f.succeed()
    const generation = f.entry().generation
    f.runtime.documentReady(key, generation)
    staleProbe.resolve({ available: false, error: 'document_missing' })
    await Promise.resolve()
    assert.equal(f.entry().loadState, 'ready')
    assert.equal(f.entry().error, null)
    assert.equal(f.pool.frames[f.entry().frameId], frame)
    assert.match(frame.src, /new\.html/)
    assert.equal(frame.remountKey, 0)
    f.runtime.documentFailed(key, generation - 1, 'timeout')
    assert.equal(f.entry().loadState, 'ready')
    f.runtime.dispose()
})

test('missing documents remain errors; Reload probes the current URL and waits for shim readiness', async () => {
    const f = fixture()
    f.reconcile()
    f.runtime.attach(key, publication, f.attachment)
    f.runtime.setVisible(key, true)
    f.probes[0].resolve({ available: false, error: 'document_missing' })
    await Promise.resolve()
    assert.equal(f.entry().loadState, 'error')
    assert.deepEqual(Object.keys(f.pool.frames), [])
    await f.runtime.reload(key)
    assert.equal(f.retries.length, 1)
    assert.equal(f.probes.length, 2)
    await f.succeed()
    const entry = f.entry()
    assert.equal(entry.documentUrl, '/documents/session-a/calculator/index.html')
    assert.equal(entry.loadState, 'loading')
    f.runtime.documentFailed(key, entry.generation, 'readiness_timeout')
    assert.equal(entry.loadState, 'error')
    assert.equal(f.pool.frames[entry.frameId].visible, false)
    f.runtime.dispose()
})

test('new publications and code revisions navigate once; equal manifests and data changes do not', async () => {
    const f = fixture()
    f.reconcile()
    f.runtime.attach(key, publication, f.attachment)
    f.runtime.setVisible(key, true)
    await f.succeed()
    const entry = f.entry()
    const frame = f.pool.frames[entry.frameId]
    const firstSrc = frame.src
    f.reconcile(2)
    f.reconcile(2)
    f.reconcile(3, descriptor({ title: 'Updated title' }))
    assert.equal(f.probes.length, 1)
    assert.equal(frame.src, firstSrc)
    f.reconcile(4, descriptor({ codeRevision: 'copy-2' }))
    await f.succeed()
    assert.equal(f.probes.length, 2)
    assert.notEqual(frame.src, firstSrc)
    const nextSrc = frame.src
    f.reconcile(5, descriptor({ codeRevision: 'copy-2' }))
    assert.equal(frame.src, nextSrc)
    assert.equal(f.probes.length, 2)
    f.runtime.dispose()
})

test('never-loaded corrections and inactive corrections stay unloaded until current placement is visible', async () => {
    const f = fixture()
    f.reconcile()
    f.runtime.attach(key, publication, f.attachment)
    f.reconcile(2, descriptor({ publicationKey: '["session-a",20,0,0]' }))
    assert.deepEqual(Object.keys(f.pool.frames), [])
    assert.equal(f.probes.length, 0)
    f.runtime.setActive(false)
    f.runtime.attach(key, '["session-a",20,0,0]', f.attachment)
    f.runtime.setVisible(key, true)
    assert.equal(f.probes.length, 0)
    f.runtime.setActive(true)
    await f.succeed()
    f.runtime.setActive(false)
    f.reconcile(3, descriptor({ publicationKey: '["session-a",30,0,0]' }))
    assert.equal(f.probes.length, 1)
    assert.equal(f.runtime.loadedEntries.length, 1)
    f.runtime.dispose()
})

test('stale and equal manifest revisions cannot restore pending or disabled documents', async () => {
    const f = fixture()
    f.reconcile(11)
    f.reconcile(10, descriptor({ status: 'pending' }))
    assert.equal(f.entry().descriptor.status, 'ready')
    f.reconcile(12, descriptor({ status: 'not_included' }))
    f.reconcile(11)
    f.reconcile(12)
    assert.equal(f.entry().descriptor.status, 'not_included')
    f.runtime.attach(key, publication, f.attachment)
    f.runtime.setVisible(key, true)
    assert.equal(f.probes.length, 0)
    f.runtime.dispose()
})

test('fullscreen retains the same frame after row detach and hides on close or deactivation', async () => {
    const f = fixture()
    f.reconcile()
    const detach = f.runtime.attach(key, publication, f.attachment)
    f.runtime.setVisible(key, true)
    await f.succeed()
    const frame = f.pool.frames[f.entry().frameId]
    const src = frame.src
    f.runtime.openFullscreen(key)
    detach()
    assert.equal(frame.visible, true)
    assert.equal(frame.zTier, 'fullscreen')
    f.runtime.closeFullscreen()
    assert.equal(frame.visible, false)
    assert.equal(frame.src, src)
    f.runtime.attach(key, publication, f.attachment)
    f.runtime.setVisible(key, true)
    f.runtime.openFullscreen(key)
    f.runtime.setActive(false)
    assert.equal(f.runtime.fullscreenArtifactKey.value, null)
    assert.equal(frame.visible, false)
    f.runtime.dispose()
})

test('runtime state is reactive; disposal aborts probes and removes only this view frames once', async () => {
    const f = fixture()
    f.reconcile()
    const other = fixture('view-b', f.pool)
    other.reconcile()
    other.runtime.attach(key, publication, other.attachment)
    other.runtime.setVisible(key, true)
    await other.succeed()
    f.runtime.attach(key, publication, f.attachment)
    f.runtime.setVisible(key, true)
    await f.succeed()
    let loadedCount = 0
    const stop = watchEffect(() => { loadedCount = f.runtime.loadedEntries.length }, { flush: 'sync' })
    assert.equal(loadedCount, 1)
    await f.runtime.reload(key)
    const lateProbe = f.probes.at(-1)
    f.runtime.dispose()
    f.runtime.dispose()
    assert.equal(lateProbe.signal.aborted, true)
    assert.equal(f.disposals(), 1)
    assert.equal(loadedCount, 0)
    lateProbe.resolve({ available: true, error: null })
    await Promise.resolve()
    assert.deepEqual(Object.keys(f.pool.frames), [other.entry().frameId])
    f.runtime.setVisible(key, true)
    assert.equal(f.probes.length, 2)
    stop()
    other.runtime.dispose()
})

test('Reload keeps fullscreen ownership after the source row detaches', async () => {
    const f = fixture()
    f.reconcile()
    const detach = f.runtime.attach(key, publication, f.attachment)
    f.runtime.setVisible(key, true)
    await f.succeed()
    const frame = f.pool.frames[f.entry().frameId]
    f.runtime.openFullscreen(key)
    detach()
    await f.runtime.reload(key)
    assert.equal(f.runtime.fullscreenArtifactKey.value, key)
    assert.equal(f.probes.length, 2)
    await f.succeed()
    assert.equal(f.pool.frames[f.entry().frameId], frame)
    assert.equal(frame.visible, true)
    assert.equal(frame.zTier, 'fullscreen')
    f.runtime.dispose()
})

test('visibility updates cannot navigate before explicit retry finishes', async () => {
    const f = fixture()
    f.reconcile()
    f.runtime.attach(key, publication, f.attachment)
    f.runtime.setVisible(key, true)
    await f.succeed()
    let retryFinished
    f.adapter.retry = () => new Promise(resolve => { retryFinished = resolve })
    const reload = f.runtime.reload(key)
    f.runtime.setVisible(key, true)
    f.runtime.setActive(true)
    assert.equal(f.probes.length, 1)
    retryFinished()
    await reload
    assert.equal(f.probes.length, 2)
    f.runtime.dispose()
})

test('a failed navigation configuration becomes a retryable error without allocating a frame', async () => {
    const f = fixture()
    f.adapter.brokerConfig = () => { throw new Error('configuration unavailable') }
    f.reconcile()
    f.runtime.attach(key, publication, f.attachment)
    f.runtime.setVisible(key, true)
    await f.succeed()
    assert.equal(f.entry().loadState, 'error')
    assert.equal(f.entry().error, 'document_unavailable')
    assert.equal(f.runtime.loadedEntries.length, 0)
    f.runtime.dispose()
})
