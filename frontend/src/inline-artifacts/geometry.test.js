import { test } from 'node:test'
import assert from 'node:assert/strict'
import { createInlineGeometryScheduler } from './geometry.js'
import { createPinia, setActivePinia } from 'pinia'
import { useFramePoolStore } from '../stores/framePool.js'

function fixture() {
    setActivePinia(createPinia())
    const pool = useFramePoolStore()
    pool.register('a', { src: 'document', remountKey: 0 })
    pool.patch('a', { visible: true })
    let callback, passes = 0, measures = 0, focuses = 0
    const state = { visible: true, suppressed: false, fullscreen: false,
        rect: { x: 10, y: 20, width: 200, height: 360 } }
    const attachment = {
        placeholderEl: { getBoundingClientRect: () => { measures++; return state.rect } },
        clipEl: { getBoundingClientRect: () => ({ x: 0, y: 0, width: 180, height: 300 }) },
        isSuppressed: () => state.suppressed,
        focusConversation: () => focuses++,
    }
    const scheduler = createInlineGeometryScheduler({ pool,
        requestFrame: fn => { passes++; callback = fn; return passes }, cancelFrame: () => { callback = null },
        viewport: () => ({ x: 0, y: 0, width: 800, height: 600 }),
    })
    const detach = scheduler.attach('a', {
        getAttachment: () => attachment, isVisible: () => state.visible, isFullscreen: () => state.fullscreen,
    })
    const flush = () => { const fn = callback; callback = null; fn?.() }
    return { pool, scheduler, state, flush, detach, measures: () => measures,
        focuses: () => focuses, passes: () => passes }
}

test('scroll bursts coalesce and clip frames to the viewport and conversation', () => {
    const f = fixture()
    for (let i = 0; i < 20; i++) f.scheduler.schedule()
    assert.equal(f.passes(), 1)
    f.flush()
    assert.deepEqual(f.pool.frames.a.rect, { x: 10, y: 20, width: 200, height: 360 })
    assert.deepEqual(f.pool.frames.a.clipRect, { x: 0, y: 0, width: 180, height: 300 })
    assert.equal(f.pool.frames.a.visible, true)
    assert.equal(f.measures(), 1)
})

test('complete clipping and suppression return focus before hiding', () => {
    const f = fixture()
    const document = { activeElement: null }
    const iframe = { ownerDocument: document }
    document.activeElement = iframe
    f.pool.setFrameEl('a', iframe)
    f.state.rect.y = 400
    f.flush()
    assert.equal(f.pool.frames.a.visible, false)
    assert.equal(f.focuses(), 1)
    f.state.rect.y = 20
    f.scheduler.schedule(); f.flush()
    assert.equal(f.pool.frames.a.visible, true)
    f.state.suppressed = true
    f.scheduler.schedule(); f.flush()
    assert.equal(f.pool.frames.a.visible, false)
    assert.equal(f.focuses(), 2)
})

test('detached and background frames have no measurement work; fullscreen survives row detachment', () => {
    const f = fixture()
    f.flush()
    f.state.visible = false
    f.scheduler.schedule(); f.flush()
    assert.equal(f.measures(), 1)
    f.state.visible = true
    f.state.fullscreen = true
    f.scheduler.schedule(); f.flush()
    assert.deepEqual(f.pool.frames.a.rect, { x: 0, y: 0, width: 800, height: 600 })
    assert.equal(f.measures(), 1)
    assert.equal(f.pool.frames.a.clipRect, null)
    assert.equal(f.pool.frames.a.zTier, 'fullscreen')
    f.detach()
    f.scheduler.schedule(); f.flush()
    assert.equal(f.measures(), 1)
    f.scheduler.dispose()
})

test('fullscreen host signals containment, handles parent Escape and disposes listeners', async () => {
    const { installInlineRuntimeHost } = await import('./runtimeHost.js')
    const listeners = new Map(), expansions = []
    let key = 'a', closed = 0, scheduled = 0
    const runtime = { fullscreenArtifactKey: { get value() { return key } },
        closeFullscreen: () => { key = null; closed++ }, geometry: { schedule: () => scheduled++ } }
    const host = installInlineRuntimeHost({ runtime,
        window: { addEventListener: (name, fn) => listeners.set(name, fn), removeEventListener: name => listeners.delete(name) },
        expandPreviewHost: value => expansions.push(value),
    })
    host.syncFullscreen()
    assert.deepEqual(expansions, [true])
    listeners.get('scroll')()
    assert.equal(scheduled, 2)
    let stopped = false
    listeners.get('keydown')({ key: 'Escape', stopPropagation: () => { stopped = true } })
    assert.equal(closed, 1)
    assert.equal(stopped, true)
    host.syncFullscreen()
    assert.deepEqual(expansions, [true, false])
    host.dispose()
    assert.equal(listeners.size, 0)
})

test('failed HEAD chrome measures the current placeholder without allocating an iframe', () => {
    const f = fixture()
    f.pool.unregister('a')
    let chrome = null
    f.scheduler.attach('error', {
        isVisible: () => true,
        getAttachment: () => ({ placeholderEl: { getBoundingClientRect: () => ({ x: 50, y: 80, width: 300, height: 360 }) } }),
        onGeometry: fields => { chrome = fields },
    })
    f.flush()
    assert.deepEqual(chrome.rect, { x: 50, y: 80, width: 300, height: 360 })
    assert.equal(chrome.visible, true)
    assert.deepEqual(Object.keys(f.pool.frames), [])
})

test('focus return makes the conversation scroll container programmatically focusable', async () => {
    const { focusInlineConversation } = await import('./geometry.js')
    const attributes = new Map()
    let focused = false
    const conversation = { isConnected: true, hasAttribute: key => attributes.has(key),
        setAttribute: (key, value) => attributes.set(key, value),
        focus: options => { focused = attributes.has('tabindex') && options.preventScroll } }
    focusInlineConversation(conversation)
    assert.equal(focused, true)
    assert.equal(attributes.get('tabindex'), '-1')
})
