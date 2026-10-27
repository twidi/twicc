import test from 'node:test'
import assert from 'node:assert/strict'
import { h, ref, reactive, KeepAlive } from 'vue'
import { useSessionEventHistory } from './useSessionEventHistory.js'
import { makeRenderer, flush } from '../../tests/helpers/scrollerComponentHarness.js'
import { createSessionEventHistoryActions } from '../utils/chatEvents/historyActions.js'

function lifecycleFixture(t) {
    const requests = [], target = new EventTarget()
    let latest = 1
    const actions = createSessionEventHistoryActions({ eventTarget: () => target, fetchPage: async params => {
        requests.push(params)
        return { events: [], sessions: {}, latest_id: latest, has_more: false, truncated: false }
    } })
    const store = reactive({ sessions: { s: { id: 's' }, other: { id: 'other' } },
        localState: { sessionEventHistory: {}, sessionEventSegments: {}, sessionEventRetired: {} },
        recomputeVisualItems() {}, ...actions })
    const apps = []
    t.after(() => { for (const app of apps) app.unmount(); for (const id of Object.keys(store.sessions)) store.clearSessionEventHistory(id) })
    function mount({ sessionId = ref('s'), active = ref(true), eligible = ref(true), keepAlive = false } = {}) {
        const { renderer, root } = makeRenderer()
        const shown = ref(true)
        const Owner = { setup() {
            useSessionEventHistory({ store, sessionId, eligible, active })
            return () => h('div')
        } }
        const app = renderer.createApp({ render: () => keepAlive
            ? h(KeepAlive, null, { default: () => shown.value ? h(Owner) : null })
            : h(Owner) })
        app.mount(root); apps.push(app)
        return { active, eligible, sessionId, shown, unmount() { app.unmount(); apps.splice(apps.indexOf(app), 1) } }
    }
    async function signal() {
        latest++
        target.dispatchEvent(new CustomEvent('twicc:timeline-changed', { detail: { latestId: latest } }))
        await flush()
    }
    return { store, requests, mount, signal }
}

test('a destroyed chat releases its history and sends no requests for later global events', async t => {
    const f = lifecycleFixture(t), view = f.mount()
    await flush(); assert.equal(f.requests.length, 2)
    view.unmount(); await flush()
    assert.equal(f.store.localState.sessionEventHistory.s, undefined)
    await f.signal(); assert.equal(f.requests.length, 2)
})

test('an inactive chat retains its cache without requests and catches up when shown again', async t => {
    const f = lifecycleFixture(t), view = f.mount()
    await flush(); const snapshot = f.store.localState.sessionEventHistory.s.snapshot
    view.active.value = false; await flush()
    await f.signal(); assert.equal(f.requests.length, 2)
    assert.strictEqual(f.store.localState.sessionEventHistory.s.snapshot, snapshot)
    view.active.value = true; await flush(); assert.equal(f.requests.length, 3)
    assert.equal(f.requests.at(-1).afterId, 1)
})

test('shared views keep one subscription until the last active view pauses or unmounts', async t => {
    const f = lifecycleFixture(t), first = f.mount(), second = f.mount()
    await flush(); assert.equal(f.requests.length, 2)
    first.active.value = false; await flush(); await f.signal()
    assert.equal(f.requests.length, 3)
    second.unmount(); await flush(); await f.signal()
    assert.equal(f.requests.length, 3)
    assert.ok(f.store.localState.sessionEventHistory.s)
    first.unmount(); assert.equal(f.store.localState.sessionEventHistory.s, undefined)
})

test('reusing a chat for another session releases the old subscription', async t => {
    const f = lifecycleFixture(t), view = f.mount()
    await flush(); view.sessionId.value = 'other'; await flush()
    assert.equal(f.store.localState.sessionEventHistory.s, undefined)
    assert.ok(f.store.localState.sessionEventHistory.other)
    const count = f.requests.length
    await f.signal(); assert.equal(f.requests.length, count + 1)
    assert.equal(f.requests.at(-1).sessionId, 'other')
})

test('an initially inactive chat defers loading until it becomes visible', async t => {
    const f = lifecycleFixture(t), view = f.mount({ active: ref(false) })
    await flush(); await f.signal(); assert.equal(f.requests.length, 0)
    view.active.value = true; await flush()
    assert.equal(f.requests.length, 2)
    assert.equal(f.store.localState.sessionEventHistory.s.status, 'ready')
})

test('KeepAlive pauses a cached chat even when its active prop remains true', async t => {
    const f = lifecycleFixture(t), view = f.mount({ keepAlive: true })
    await flush(); assert.equal(f.requests.length, 2)
    view.shown.value = false; await flush(); await f.signal()
    assert.equal(f.requests.length, 2)
    assert.ok(f.store.localState.sessionEventHistory.s)
    view.shown.value = true; await flush()
    assert.equal(f.requests.length, 3)
    view.unmount(); await f.signal()
    assert.equal(f.requests.length, 3)
    assert.equal(f.store.localState.sessionEventHistory.s, undefined)
})

test('chat activation catches up a cached history and recreates an unloaded controller', async t => {
    const requests = [], target = new EventTarget()
    const actions = createSessionEventHistoryActions({ eventTarget: () => target, fetchPage: async params => {
        requests.push(params); return { events: [], sessions: {}, latest_id: 1, has_more: false, truncated: false }
    } })
    const store = reactive({ sessions: { s: { id: 's' } }, localState: { sessionEventHistory: {}, sessionEventSegments: {}, sessionEventRetired: {} }, recomputeVisualItems() {}, ...actions })
    const sessionId = ref('s'), eligible = ref(false), active = ref(true)
    let api; const { renderer, root } = makeRenderer()
    const app = renderer.createApp({ setup() { api = useSessionEventHistory({ store, sessionId, eligible, active }); return () => h('div') } })
    app.mount(root); t.after(() => { app.unmount(); store.clearSessionEventHistory('s') })
    await flush(); assert.equal(requests.length, 0)
    eligible.value = true; await flush(); assert.equal(requests.length, 2); assert.equal(api.state.value.status, 'ready')
    active.value = false; await flush(); active.value = true; await flush(); assert.equal(requests.length, 3)
    active.value = false; await flush(); store.clearSessionEventHistory('s'); active.value = true; await flush()
    assert.equal(requests.length, 5); assert.equal(api.state.value.status, 'ready')
})
