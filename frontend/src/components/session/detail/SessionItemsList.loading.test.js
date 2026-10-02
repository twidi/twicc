import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import * as Vue from 'vue'
import { hasContent, setParsedContent, clearParsedContent } from '../../../utils/parsedContent.js'
import { makeRenderer, flush, deferred } from '../../../../tests/helpers/scrollerComponentHarness.js'
import * as helpers from '../../../utils/scrollerLoadWindow.js'
const source = readFileSync(new URL('./SessionItemsList.vue', import.meta.url), 'utf8')
const start = source.indexOf('// Gap loading ownership')
const end = source.indexOf('/**\n * Toggle a group', start)
const block = source.slice(start, end)
function mount(t) {
    const { renderer, root } = makeRenderer()
    const props = Vue.reactive({ projectId: 'p', sessionId: 's', parentSessionId: null, viewActive: true })
    const visualItems = Vue.ref([{ lineNum: 1 }, { lineNum: 2 }]), sessionActive = Vue.ref(true)
    const suspended = Vue.ref(false), calls = [], requests = [], corrections = []
    const viewport = { height: 140 }
    const scroller = { suspended, getScrollState: () => ({ clientHeight: viewport.height, scrollTop: 0, scrollHeight: 400 }), isAtBottom: () => true }
    const scrollerRef = Vue.ref(scroller)
    const store = { loadSessionItemsRanges(...args) { calls.push(args); const request = deferred(); requests.push(request); return request.promise } }
    let api
    const dependencies = { ...Vue, ...helpers, hasContent, props, visualItems, sessionActive,
        scrollerRef, store, LOAD_BUFFER: 50, LOAD_DEBOUNCE_MS: 150,
        scrollToBottomUntilStable: () => corrections.push(true) }
    const app = renderer.createApp({ setup() {
        api = new Function(...Object.keys(dependencies), `${block}; return { onScrollerUpdate, executePendingLoad, onGapScroll }`)(...Object.values(dependencies))
        return () => null
    } })
    app.mount(root)
    t.after(() => app.unmount())
    return { viewport, props, visualItems, sessionActive, suspended, calls, requests, corrections, api, app }
}
const range = { startIndex: 0, endIndex: 2, visibleStartIndex: 0, visibleEndIndex: 1 }
async function debounce() { await new Promise(resolve => setTimeout(resolve, 180)); await flush() }

test('actual gap watcher loads equal-index replacement and content removal without geometry events', async t => {
    const v = mount(t)
    v.visualItems.value.forEach(item => setParsedContent(item, {}))
    v.api.onScrollerUpdate(range); await debounce(); assert.equal(v.calls.length, 0)
    v.visualItems.value[1] = { lineNum: 2 }; await debounce()
    assert.deepEqual(v.calls[0], ['p', 's', [[2, 2]], null])
    setParsedContent(v.visualItems.value[1], {}); v.requests[0].resolve(); await flush()
    clearParsedContent(v.visualItems.value[0]); await debounce()
    assert.deepEqual(v.calls[1][2], [[1, 1]])
    v.requests[1].resolve(); await flush()
})
test('same gap coalesces, partial progress reconciles, no progress stops until real scroll', async t => {
    const v = mount(t)
    v.api.onScrollerUpdate(range); await debounce()
    v.api.onScrollerUpdate(range); await debounce(); assert.equal(v.calls.length, 1)
    setParsedContent(v.visualItems.value[0], {}); v.requests[0].resolve(); await debounce()
    assert.deepEqual(v.calls[1][2], [[2, 2]])
    v.requests[1].resolve(); await debounce(); assert.equal(v.calls.length, 2)
    v.api.onScrollerUpdate({ ...range, visibleStartIndex: 1 }); await debounce()
    assert.equal(v.calls.length, 3)
    v.requests[2].resolve(); await flush()
})
test('hide before debounce blocks fetch; hide during await blocks correction and follow-up', async t => {
    const v = mount(t)
    v.api.onScrollerUpdate(range); v.props.viewActive = false
    await debounce(); assert.equal(v.calls.length, 0)
    v.props.viewActive = true; await debounce(); assert.equal(v.calls.length, 1)
    v.suspended.value = true
    setParsedContent(v.visualItems.value[0], {}); v.requests[0].resolve(); await debounce()
    assert.equal(v.calls.length, 1); assert.equal(v.corrections.length, 0)
})
test('unmount cancels a pending debounce', async t => {
    const v = mount(t); v.api.onScrollerUpdate(range); v.app.unmount()
    await debounce(); assert.equal(v.calls.length, 0)
})
test('scope change revokes old request correction', async t => {
    const v = mount(t); v.api.onScrollerUpdate(range); await debounce()
    v.props.sessionId = 'other'; v.requests[0].resolve(); await flush()
    assert.equal(v.corrections.length, 0)
})

test('failure stops until an explicit scroll retry', async t => {
    const v = mount(t); v.api.onScrollerUpdate(range); await debounce()
    v.requests[0].reject(new Error('network')); await debounce()
    assert.equal(v.calls.length, 1)
    v.api.onGapScroll(); await debounce(); assert.equal(v.calls.length, 2)
    v.requests[1].resolve(); await flush()
})
test('bounded membership change retries an unchanged missing candidate after no progress', async t => {
    const v = mount(t); v.api.onScrollerUpdate(range); await debounce()
    v.requests[0].resolve(); await debounce(); assert.equal(v.calls.length, 1)
    v.visualItems.value = [{ lineNum: 'day', isDaySeparator: true }, ...v.visualItems.value]
    await debounce(); assert.equal(v.calls.length, 2)
    v.requests[1].resolve(); await flush()
})
test('equal geometry events neither scan content nor restart the pending debounce', async t => {
    const v = mount(t)
    let reads = 0
    v.visualItems.value = [{ lineNum: 1, get content() { reads++; return null } }]
    v.api.onScrollerUpdate(range); await flush()
    const initialReads = reads
    await new Promise(resolve => setTimeout(resolve, 90))
    v.api.onScrollerUpdate({ ...range, endIndex: 100 }); await flush()
    assert.equal(reads, initialReads)
    await new Promise(resolve => setTimeout(resolve, 90)); await flush()
    assert.equal(v.calls.length, 1)
    v.requests[0].resolve(); await flush()
})

test('DOM hides before ResizeObserver delivery: pending gap still cannot start', async t => {
    const v = mount(t); v.api.onScrollerUpdate(range); await flush()
    v.viewport.height = 0; await debounce(); assert.equal(v.calls.length, 0)
})
