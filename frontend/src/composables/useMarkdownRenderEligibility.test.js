import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { computed, createRenderer, h, KeepAlive, nextTick, provide, reactive, ref, watch } from 'vue'
import { useMarkdownRenderEligibility } from './useMarkdownRenderEligibility.js'
import { MARKDOWN_RENDER_VIEW_CONTEXT, STREAMING_ROW_CONTEXT } from './streamPublicationKeys.js'
import { createMarkdownRenderCoordinator } from '../utils/markdownRenderCoordinator.js'

const renderer = createRenderer({ createElement: () => ({}), createText: () => ({}), createComment: () => ({}), setText() {}, setElementText() {}, parentNode() {}, nextSibling() {}, patchProp() {}, insert() {}, remove() {} })
function harness({ contexts = true, document = true, coordinator } = {}) {
    const oldDocument = globalThis.document
    const listeners = new Set()
    const fakeDocument = { visibilityState: 'visible', addEventListener(name, fn) { assert.equal(name, 'visibilitychange'); listeners.add(fn) }, removeEventListener(name, fn) { assert.equal(name, 'visibilitychange'); listeners.delete(fn) } }
    if (document) globalThis.document = fakeDocument
    else delete globalThis.document
    const view = ref(true), intersection = ref('unknown'), scrollerActive = ref(true), shown = ref(true)
    let eligible, beforeMount
    const Owner = { setup() {
        eligible = useMarkdownRenderEligibility().eligible
        beforeMount = eligible.value
        if (coordinator) watch(eligible, coordinator.setEligible, { immediate: true, flush: 'sync' })
        return () => h('span')
    } }
    const app = renderer.createApp({ setup() {
        if (contexts) {
            provide(MARKDOWN_RENDER_VIEW_CONTEXT, view)
            provide(STREAMING_ROW_CONTEXT, { intersection: () => intersection.value, scrollerActive })
        }
        return () => h(KeepAlive, null, { default: () => shown.value ? h(Owner) : null })
    } })
    app.mount({})
    return { view, intersection, scrollerActive, shown, eligible, beforeMount, listeners,
        visibility(value) { fakeDocument.visibilityState = value; for (const fn of listeners) fn() },
        cleanup() { app.unmount(); globalThis.document = oldDocument } }
}

test('mounted consumers without contexts or document become eligible', () => {
    const gate = harness({ contexts: false, document: false })
    try { assert.equal(gate.beforeMount, false); assert.equal(gate.eligible.value, true) }
    finally { gate.cleanup() }
    assert.equal(gate.eligible.value, false)
})

test('view, row, document and KeepAlive transitions update eligibility immediately', async () => {
    const gate = harness()
    try {
        assert.equal(gate.beforeMount, false)
        assert.equal(gate.eligible.value, true, 'unknown row allows initial measurement')
        assert.equal(gate.listeners.size, 1)
        for (const value of ['outside', 'inside', 'unknown']) {
            gate.intersection.value = value
            assert.equal(gate.eligible.value, value !== 'outside')
        }
        gate.scrollerActive.value = false; assert.equal(gate.eligible.value, false)
        gate.scrollerActive.value = true; assert.equal(gate.eligible.value, true)
        gate.view.value = false; assert.equal(gate.eligible.value, false)
        gate.view.value = true; assert.equal(gate.eligible.value, true)
        gate.visibility('hidden'); assert.equal(gate.eligible.value, false)
        gate.visibility('visible'); assert.equal(gate.eligible.value, true)
        gate.shown.value = false; await nextTick(); assert.equal(gate.eligible.value, false)
        gate.shown.value = true; await nextTick(); assert.equal(gate.eligible.value, true)
    } finally { gate.cleanup() }
    assert.equal(gate.eligible.value, false)
    assert.equal(gate.listeners.size, 0)
})

test('actual Markdown view provider allows hidden reveal measurement', () => {
    const source = readFileSync(new URL('../components/session/detail/SessionItemsList.vue', import.meta.url), 'utf8')
    const props = reactive({ viewActive: true }), sessionActive = ref(true), isLoading = ref(false), showVirtualScroller = ref(true)
    const reveal = { hidden: ref(true) }
    function expression(key) {
        const start = source.indexOf(`provide(${key}, computed(`)
        assert.ok(start >= 0, `missing ${key} provider`)
        const end = source.indexOf('))', start) + 2
        let result
        new Function('provide', key, 'computed', 'props', 'sessionActive', 'isLoading', 'showVirtualScroller', 'reveal', source.slice(start, end))(
            (_, value) => { result = value }, Symbol(key), computed, props, sessionActive, isLoading, showVirtualScroller, reveal)
        return result
    }
    const markdown = expression('MARKDOWN_RENDER_VIEW_CONTEXT'), streaming = expression('STREAMING_VIEW_CONTEXT')
    assert.equal(markdown.value, true); assert.equal(streaming.value, false)
    props.viewActive = false; assert.equal(markdown.value, false)
    props.viewActive = true
    sessionActive.value = false; assert.equal(markdown.value, false)
    sessionActive.value = true
    isLoading.value = true; assert.equal(markdown.value, false)
    isLoading.value = false
    showVirtualScroller.value = false; assert.equal(markdown.value, false)
})

test('actual eligibility holds one active operation and resumes only latest work', async () => {
    const operations = [], commits = []
    const coordinator = createMarkdownRenderCoordinator({ render(input, context) {
        return new Promise(resolve => operations.push({ input, context, resolve }))
    }, commit(result) { commits.push(result) } })
    const gate = harness({ coordinator })
    const request = source => coordinator.request({ source, theme: 'light', slashTag: false })
    const drain = async () => { await Promise.resolve(); await Promise.resolve(); await Promise.resolve() }
    try {
        request('first'); await drain(); assert.equal(operations.length, 1)
        gate.visibility('hidden'); assert.equal(operations[0].context.isCurrent(), false)
        request('hidden'); gate.visibility('visible'); request('latest')
        await drain(); assert.equal(operations.length, 1)
        operations[0].resolve('obsolete'); await drain()
        assert.deepEqual(commits, []); assert.equal(operations[1].input.source, 'latest')
        gate.shown.value = false; await nextTick(); assert.equal(operations[1].context.isCurrent(), false)
        request('detached'); gate.shown.value = true; await nextTick(); request('reattached')
        await drain(); assert.equal(operations.length, 2)
        operations[1].resolve('obsolete again'); await drain()
        assert.equal(operations[2].input.source, 'reattached')
        operations[2].resolve('complete'); await drain(); assert.deepEqual(commits, ['complete'])
    } finally { gate.cleanup(); coordinator.dispose() }
})
