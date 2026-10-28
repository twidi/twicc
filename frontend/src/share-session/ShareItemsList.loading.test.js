import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import * as Vue from 'vue'
import { hasContent, setParsedContent, clearParsedContent } from '../utils/parsedContent.js'
import * as helpers from '../utils/scrollerLoadWindow.js'
import { makeRenderer, deferred, flush } from '../../tests/helpers/scrollerComponentHarness.js'
import { createVirtualClock } from '../../tests/helpers/virtualClock.js'
const source = readFileSync(new URL('./ShareItemsList.vue', import.meta.url), 'utf8')
const block = source.slice(source.indexOf('async function loadLines('), source.indexOf('function toggleGroup('))
function mount(t, { height = 140 } = {}) {
    const { renderer, root } = makeRenderer()
    const props = Vue.reactive({ projectId: 'share', sessionId: 's', parentSessionId: 'parent' })
    const visualItems = Vue.ref([{ lineNum: 1 }, { lineNum: 2 }]), suspended = Vue.ref(false)
    const initialLoading = Vue.ref(false), preparationPending = Vue.ref(false)
    const viewport = { height }
    const scrollerRef = Vue.ref({ suspended, getScrollState: () => ({ clientHeight: viewport.height }) })
    const calls = [], requests = []
    const store = { loadSessionItemsRanges(...args) { calls.push(args); const r = deferred(); requests.push(r); return r.promise } }
    // The component's timers (its 120 ms gap-load debounce) run on a virtual
    // clock: `debounce(v)` elapses it instead of sleeping (see tests/helpers/virtualClock.js).
    const clock = createVirtualClock({ flush })
    t.after(() => clock.dispose())
    const deps = { ...Vue, ...helpers, hasContent, props, visualItems, initialLoading, preparationPending, scrollerRef, store, BUFFER: 40,
        setTimeout: clock.setTimeout, clearTimeout: clock.clearTimeout }
    let api
    const app = renderer.createApp({ setup() {
        api = new Function(...Object.keys(deps), `${block}; return { onUpdate, scroll: onShareScroll }`)(...Object.values(deps))
        return () => null
    } })
    app.mount(root); t.after(() => app.unmount())
    return { viewport, props, visualItems, suspended, initialLoading, preparationPending, calls, requests, app, api, clock }
}
const range = { visibleStartIndex: 0, visibleEndIndex: 1 }
async function debounce(v) { await v.clock.elapse(150); await flush() }
test('share same-index placeholders load through actual membership and availability watches', async t => {
    const v = mount(t); v.visualItems.value.forEach(item => setParsedContent(item, {}))
    v.api.onUpdate(range); await debounce(v); assert.equal(v.calls.length, 0)
    v.visualItems.value[1] = { lineNum: 2 }; await debounce(v)
    assert.deepEqual(v.calls[0], ['share', 's', [[2, 2]], 'parent'])
    v.requests[0].resolve(); await flush()
})
test('share coalesces running gaps, reconciles partial progress, and stops on no progress', async t => {
    const v = mount(t); v.api.onUpdate(range); await debounce(v)
    v.api.onUpdate(range); await debounce(v); assert.equal(v.calls.length, 1)
    setParsedContent(v.visualItems.value[0], {}); v.requests[0].resolve(); await debounce(v)
    assert.deepEqual(v.calls[1][2], [[2, 2]])
    v.requests[1].resolve(); await debounce(v); assert.equal(v.calls.length, 2)
    v.api.scroll(); await debounce(v); assert.equal(v.calls.length, 3)
    v.requests[2].resolve(); await flush()
})
test('share hide before debounce and unmount during fetch revoke loading', async t => {
    const v = mount(t); v.api.onUpdate(range); v.suspended.value = true
    await debounce(v); assert.equal(v.calls.length, 0)
    v.suspended.value = false; await debounce(v); assert.equal(v.calls.length, 1)
    v.app.unmount(); setParsedContent(v.visualItems.value[0], {}); v.requests[0].resolve(); await debounce(v)
    assert.equal(v.calls.length, 1)
})
test('share preparation and initial loading gate background gaps', async t => {
    const v = mount(t); v.initialLoading.value = true; v.api.onUpdate(range)
    await debounce(v); assert.equal(v.calls.length, 0)
    v.preparationPending.value = true; v.initialLoading.value = false
    await debounce(v); assert.equal(v.calls.length, 0)
})

test('share DOM hides before observer delivery: pending gap cannot start', async t => {
    const v = mount(t); v.api.onUpdate(range); await flush()
    v.viewport.height = 0; await debounce(v); assert.equal(v.calls.length, 0)
})

test('share successful fill can reload the same key after its content becomes missing again', async t => {
    const v = mount(t)
    v.visualItems.value = [{ lineNum: 1 }]
    const item = v.visualItems.value[0]
    v.api.onUpdate(range); await debounce(v)
    assert.deepEqual(v.calls[0][2], [[1, 1]])
    setParsedContent(item, { text: 'loaded' })
    v.requests[0].resolve(); await debounce(v)
    assert.equal(v.calls.length, 1)
    clearParsedContent(item); await debounce(v)
    assert.deepEqual(v.calls[1]?.[2], [[1, 1]])
    v.requests[1].resolve(); await debounce(v)
    assert.equal(v.calls.length, 2, 'unchanged no-progress settlement must still stop')
})


for (const opportunity of ['measured update', 'scroll']) {
    test('share zero-height readiness recovers on ' + opportunity + ' with unchanged range', async t => {
        const v = mount(t, { height: 0 })
        v.api.onUpdate(range)
        await debounce(v); assert.equal(v.calls.length, 0)
        // DOM dimensions change without invalidating any Vue ref used by the readiness computed.
        v.viewport.height = 421
        if (opportunity === 'measured update') v.api.onUpdate({ ...range })
        else v.api.scroll()
        await debounce(v)
        assert.equal(v.calls.length, 1, 'a real measured opportunity refreshes cached false readiness')
        v.requests[0].resolve(); await debounce(v)
        assert.equal(v.calls.length, 1, 'no-progress settlement still cannot self-loop')
    })
}

const initialBlock = source.slice(source.indexOf('async function loadInitial('), source.indexOf('onMounted(loadInitial)'))
for (const metadataFirst of [true, false]) {
    test(`share opening loads the content window once and recomputes once (metadata ${metadataFirst ? 'first' : 'last'})`, async () => {
        const metadata = deferred(), content = deferred(), calls = []
        const store = {
            loadSessionMetadata: () => metadata.promise,
            loadSessionItemsRanges: (...args) => { calls.push(['ranges', ...args]); return content.promise },
            fetchToolStates: async () => {},
            initSessionItemsFromMetadata: (...args) => calls.push(['init', ...args]),
            recomputeVisualItems: (...args) => calls.push(['recompute', ...args]),
        }
        const initialLoading = Vue.ref(true), preparationPending = Vue.ref(false)
        const props = { projectId: 'share', sessionId: 's', parentSessionId: null, lastLine: 500 }
        const loadInitial = new Function('props', 'store', 'initialLoading', 'preparationPending', 'isSessionNotReadyError', 'INITIAL',
            `${initialBlock}; return loadInitial`)(props, store, initialLoading, preparationPending, () => false, 100)
        const done = loadInitial()
        if (metadataFirst) { metadata.resolve([{ line_num: 1 }]); await flush(); content.resolve(true) }
        else { content.resolve(true); await flush(); metadata.resolve([{ line_num: 1 }]) }
        await done
        assert.deepEqual(calls, [
            ['ranges', 'share', 's', [[401, 500]], null, { recompute: false }],
            ['init', 's', [{ line_num: 1 }], { recompute: false }],
            ['recompute', 's'],
        ])
        assert.equal(initialLoading.value, false)
    })
}
