import test from 'node:test'
import assert from 'node:assert/strict'
import { computed, createRenderer, h, provide, ref } from 'vue'
import { useStreamingPublication } from './useStreamingPublication.js'
import { STREAMING_VIEW_CONTEXT, STREAMING_ROW_CONTEXT } from './streamPublicationKeys.js'
import { createStreamPublicationIdentity, streamPublicationRegistry } from '../utils/streamPublicationRegistry.js'
const renderer = createRenderer({ createElement: () => ({}), createText: () => ({}), createComment: () => ({}), setText() {}, setElementText() {}, parentNode() {}, nextSibling() {}, patchProp() {}, insert() {}, remove() {} })
test('scoped owner follows view body height and generation with synchronous loss', () => {
    const identity = ref(createStreamPublicationIdentity('session', 'message', 0))
    const view = ref(true), body = ref(true), intersection = ref('inside'), scrollerActive = ref(false)
    const transitions = [], snapshots = []
    const unbind = streamPublicationRegistry.bindBlock(identity.value, { setActive: value => transitions.push(value), snapshot: () => snapshots.push(1) })
    const Owner = { setup() { useStreamingPublication({ identity, bodyActive: body }); return () => h('span') } }
    const app = renderer.createApp({ setup() {
        provide(STREAMING_VIEW_CONTEXT, computed(() => view.value))
        provide(STREAMING_ROW_CONTEXT, { intersection, scrollerActive })
        return () => h(Owner)
    } })
    app.mount({})
    assert.deepEqual(transitions, [])
    assert.deepEqual(snapshots, [])
    scrollerActive.value = true
    assert.deepEqual(transitions, [true])
    body.value = false
    assert.deepEqual(transitions, [true, false])
    body.value = true
    view.value = false
    assert.deepEqual(transitions, [true, false, true, false])
    view.value = true
    const next = createStreamPublicationIdentity('session', 'message', 0)
    const nextTransitions = []
    const unbindNext = streamPublicationRegistry.bindBlock(next, { setActive: value => nextTransitions.push(value), snapshot() {} })
    identity.value = next
    assert.deepEqual(nextTransitions, [true])
    app.unmount()
    assert.deepEqual(nextTransitions, [true, false])
    unbind(); unbindNext()
})
test('missing context never owns a live block', () => {
    const identity = createStreamPublicationIdentity('session', 'message', 1), events = []
    const unbind = streamPublicationRegistry.bindBlock(identity, { setActive: value => events.push(value), snapshot: () => events.push('snapshot') })
    const app = renderer.createApp({ setup() { useStreamingPublication({ identity: () => identity, bodyActive: () => true }); return () => h('span') } })
    app.mount({}); app.unmount(); unbind()
    assert.deepEqual(events, [])
})
