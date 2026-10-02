import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import * as Vue from 'vue'
import { hasContent, setParsedContent } from '../utils/parsedContent.js'
import * as helpers from '../utils/scrollerLoadWindow.js'
import { makeRenderer, deferred, flush } from '../../tests/helpers/scrollerComponentHarness.js'
const source = readFileSync(new URL('./ShareItemsList.vue', import.meta.url), 'utf8')
const block = source.slice(source.indexOf('async function loadLines('), source.indexOf('function toggleGroup('))
function mount(t) {
    const { renderer, root } = makeRenderer()
    const props = Vue.reactive({ projectId: 'share', sessionId: 's', parentSessionId: 'parent' })
    const visualItems = Vue.ref([{ lineNum: 1 }, { lineNum: 2 }]), suspended = Vue.ref(false)
    const initialLoading = Vue.ref(false), preparationPending = Vue.ref(false)
    const viewport = { height: 140 }
    const scrollerRef = Vue.ref({ suspended, getScrollState: () => ({ clientHeight: viewport.height }) })
    const calls = [], requests = []
    const store = { loadSessionItemsRanges(...args) { calls.push(args); const r = deferred(); requests.push(r); return r.promise } }
    const deps = { ...Vue, ...helpers, hasContent, props, visualItems, initialLoading, preparationPending, scrollerRef, store, BUFFER: 40 }
    let api
    const app = renderer.createApp({ setup() {
        api = new Function(...Object.keys(deps), `${block}; return { onUpdate, scroll: onShareScroll }`)(...Object.values(deps))
        return () => null
    } })
    app.mount(root); t.after(() => app.unmount())
    return { viewport, props, visualItems, suspended, initialLoading, preparationPending, calls, requests, app, api }
}
const range = { visibleStartIndex: 0, visibleEndIndex: 1 }
async function debounce() { await new Promise(resolve => setTimeout(resolve, 150)); await flush() }
test('share same-index placeholders load through actual membership and availability watches', async t => {
    const v = mount(t); v.visualItems.value.forEach(item => setParsedContent(item, {}))
    v.api.onUpdate(range); await debounce(); assert.equal(v.calls.length, 0)
    v.visualItems.value[1] = { lineNum: 2 }; await debounce()
    assert.deepEqual(v.calls[0], ['share', 's', [[2, 2]], 'parent'])
    v.requests[0].resolve(); await flush()
})
test('share coalesces running gaps, reconciles partial progress, and stops on no progress', async t => {
    const v = mount(t); v.api.onUpdate(range); await debounce()
    v.api.onUpdate(range); await debounce(); assert.equal(v.calls.length, 1)
    setParsedContent(v.visualItems.value[0], {}); v.requests[0].resolve(); await debounce()
    assert.deepEqual(v.calls[1][2], [[2, 2]])
    v.requests[1].resolve(); await debounce(); assert.equal(v.calls.length, 2)
    v.api.scroll(); await debounce(); assert.equal(v.calls.length, 3)
    v.requests[2].resolve(); await flush()
})
test('share hide before debounce and unmount during fetch revoke loading', async t => {
    const v = mount(t); v.api.onUpdate(range); v.suspended.value = true
    await debounce(); assert.equal(v.calls.length, 0)
    v.suspended.value = false; await debounce(); assert.equal(v.calls.length, 1)
    v.app.unmount(); setParsedContent(v.visualItems.value[0], {}); v.requests[0].resolve(); await debounce()
    assert.equal(v.calls.length, 1)
})
test('share preparation and initial loading gate background gaps', async t => {
    const v = mount(t); v.initialLoading.value = true; v.api.onUpdate(range)
    await debounce(); assert.equal(v.calls.length, 0)
    v.preparationPending.value = true; v.initialLoading.value = false
    await debounce(); assert.equal(v.calls.length, 0)
})

test('share DOM hides before observer delivery: pending gap cannot start', async t => {
    const v = mount(t); v.api.onUpdate(range); await flush()
    v.viewport.height = 0; await debounce(); assert.equal(v.calls.length, 0)
})
