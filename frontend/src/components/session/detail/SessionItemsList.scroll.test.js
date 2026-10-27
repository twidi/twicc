import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { ref } from 'vue'

const source = readFileSync(new URL('./SessionItemsList.vue', import.meta.url), 'utf8')
const start = source.indexOf('function resolveStability()')
const end = source.indexOf('/**\n * Convert an array', start)
const block = source.slice(start, end)

function setup() {
    const jumps = [], frames = []
    let revision = 0
    const scroller = {
        getScrollRevision: () => revision,
        scrollToBottom: () => { jumps.push('bottom'); revision++ },
        scrollToTop: () => { jumps.push('top'); revision++ },
    }
    const dependencies = { scrollerRef: ref(scroller), sessionActive: ref(true),
        isAutoScrollingToBottom: ref(false), isInitialScrolling: ref(false),
        STABILITY_DEBOUNCE_MS: 100, MAX_STABILITY_WAIT_MS: 1000,
        requestAnimationFrame: callback => frames.push(callback), noteScrollIntent: () => {} }
    const api = new Function(...Object.keys(dependencies), `
        let edgeScrollOperation = null, onStabilizedCallback = null;
        let stabilityTimeoutId = null, stabilityMaxWaitId = null;
        let pendingScrollToBottom = null;
        ${block}
        return { scrollToEdgeUntilStable, resolveStability,
            userScroll: () => typeof onUserScroll === 'function' && onUserScroll() };
    `)(...Object.values(dependencies))
    return { api, jumps, frames, dependencies, changeIntent: () => revision++ }
}

for (const edge of ['top', 'bottom']) {
    test(`pending ${edge} positioning does not override a newer scroll intent`, async () => {
        const v = setup()
        const operation = v.api.scrollToEdgeUntilStable(edge)
        assert.deepEqual(v.jumps, [edge])
        v.changeIntent()
        v.api.resolveStability()
        await operation
        assert.deepEqual(v.jumps, [edge])
        assert.equal(v.dependencies.isAutoScrollingToBottom.value, false)
        assert.equal(v.dependencies.isInitialScrolling.value, false)
    })
}
test('unchanged bottom positioning still performs its final correction', async () => {
    const v = setup()
    const operation = v.api.scrollToEdgeUntilStable('bottom')
    v.api.resolveStability(); await operation
    assert.deepEqual(v.jumps, ['bottom', 'bottom'])
})
test('user input immediately releases pending bottom following', async () => {
    const v = setup()
    const operation = v.api.scrollToEdgeUntilStable('bottom')
    v.changeIntent(); v.api.userScroll()
    assert.equal(v.dependencies.isAutoScrollingToBottom.value, false)
    v.api.resolveStability(); await operation
    assert.deepEqual(v.jumps, ['bottom'])
})


test('opposite-edge navigation takes ownership from a pending bottom operation', async () => {
    const v = setup()
    const bottom = v.api.scrollToEdgeUntilStable('bottom')
    const top = v.api.scrollToEdgeUntilStable('top')
    v.api.resolveStability(); await bottom; await Promise.resolve()
    assert.deepEqual(v.jumps, ['bottom', 'top'])
    v.api.resolveStability(); await top
})

// Execute the parent pre-flush transaction with the scroller's old/new geometry.
import * as eventScroll from '../../../utils/chatEvents/scroll.js'
import { watch, nextTick, shallowRef, unref, effectScope } from 'vue'
function structuralView(t, { bottom = false, suspended = false } = {}) {
    const start = source.indexOf('// Event structural scroll transaction.')
    const end = source.indexOf('// End event structural scroll transaction.', start)
    assert.ok(start >= 0 && end > start, 'structural transaction exists')
    const rows = shallowRef([{ lineNum: 1 }, { lineNum: 2 }]), jumps = []
    let positions = [{ key: 1, top: 0 }, { key: 2, top: 100 }]
    const scroller = { get positions() { return positions }, getScrollAnchor: () => ({ key: 2, index: 1, offset: 10 }),
        getScrollRevision: () => 0, isAtBottom: () => bottom, suspended, scrollToAnchor: anchor => jumps.push(anchor),
        getScrollState: () => ({ clientHeight: 100 }) }
    const dependencies = { ...eventScroll, watch, nextTick, unref, displayItems: rows, scrollerRef: shallowRef(scroller),
        sessionActive: ref(true), props: { sessionId: 's', viewActive: true }, scrollToBottomUntilStable: () => jumps.push('bottom') }
    const scope = effectScope(); t.after(() => scope.stop())
    const api = scope.run(() => new Function(...Object.keys(dependencies), `let scrollIntent=0; ${source.slice(start, end)};
        return { navigate: () => scrollIntent++ }`)(...Object.values(dependencies)))
    scope.run(() => watch(rows, () => { positions = rows.value.map((row, i) => ({ key: row.lineNum, top: i * 100 })) }, { flush: 'post' }))
    return { rows, jumps, api, dependencies }
}
test('parent transaction captures old geometry and restores once after a coalesced publication', async t => {
    const view = structuralView(t)
    view.rows.value = [{ lineNum: 1 }, { lineNum: 'event:7', isSessionEvent: true }, { lineNum: 2 }]
    view.rows.value = [...view.rows.value.slice(0, 1), { lineNum: 'event:8', isSessionEvent: true }, ...view.rows.value.slice(1)]
    await nextTick(); await nextTick()
    assert.equal(view.jumps.length, 1); assert.equal(view.jumps[0].key, 2); assert.equal(view.jumps[0].offset, 10)
})
test('new navigation after capture cancels; a suspended pane defers to resume', async t => {
    for (const suspended of [false, true]) {
        const view = structuralView(t, { suspended })
        view.rows.value = [{ lineNum: 'event:7', isSessionEvent: true }, ...view.rows.value]
        await nextTick(); view.api.navigate(); await nextTick()
        assert.deepEqual(view.jumps, [])
    }
})
test('parent bottom publication delegates once to the existing stability flow', async t => {
    const view = structuralView(t, { bottom: true })
    view.rows.value = [...view.rows.value, { lineNum: 'event:7', isSessionEvent: true }]
    await nextTick(); await nextTick(); assert.deepEqual(view.jumps, ['bottom'])
})
