import test from 'node:test'
import assert from 'node:assert/strict'
import { h, ref, reactive } from 'vue'
import { useSessionEventHistory } from './useSessionEventHistory.js'
import { makeRenderer, flush } from '../../tests/helpers/scrollerComponentHarness.js'
import { createSessionEventHistoryActions } from '../utils/chatEvents/historyActions.js'

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
