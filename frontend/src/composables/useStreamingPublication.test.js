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

test('actual SessionItem identity resolver rejects retained exit-only and real JSONL rows', async () => {
    const { readFileSync } = await import('node:fs')
    const { reactive } = await import('vue')
    const { SYNTHETIC_ITEM } = await import('../constants.js')
    const source = readFileSync(new URL('../components/session/detail/SessionItem.vue', import.meta.url), 'utf8')
    const begin = source.indexOf('const liveBlock = computed('), end = source.indexOf('const publicationIdentity =', begin)
    assert.ok(begin >= 0 && end > begin)
    const old = createStreamPublicationIdentity('session', 'message', 0), next = createStreamPublicationIdentity('session', 'message', 0)
    const props = reactive({ sessionId: 'session', syntheticKind: SYNTHETIC_ITEM.STREAMING_BLOCK.kind, publicationIdentity: old })
    const block = reactive({ publicationIdentity: next, blockIndex: 0, blockType: 'text', text: 'current canonical text' })
    const dataStore = reactive({ localState: { streamingBlocks: { session: { messageId: 'message', blocks: [block] } } } })
    // Execute the actual SFC resolver, rather than deriving ownership from its reused line number.
    const resolve = new Function('computed', 'props', 'dataStore', 'SYNTHETIC_ITEM', `${source.slice(begin, end)}; return liveBlock`)
    const liveBlock = resolve(computed, props, dataStore, SYNTHETIC_ITEM)
    assert.equal(liveBlock.value, null)
    props.publicationIdentity = next
    assert.strictEqual(liveBlock.value, block)
    assert.equal(liveBlock.value.text, 'current canonical text')
    props.syntheticKind = null
    assert.equal(liveBlock.value, null)
})
