import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { computed, createRenderer, h, nextTick, onMounted, shallowRef, watch } from 'vue'
import { useVirtualScroll } from '../composables/useVirtualScroll.js'
import { useListExit } from '../composables/useListExit.js'
import { initBuffer, feedDelta, flushBuffer, destroySessionBuffers, destroyAllBuffers } from '../utils/streamingBuffer.js'
import { createPinia, defineStore } from 'pinia'
import { getParsedContent, setParsedContent } from '../utils/parsedContent.js'
import { SYNTHETIC_ITEM } from '../constants.js'

// Execute the actual action source. Node cannot resolve the store's extensionless imports.
const source = readFileSync(new URL('./data.js', import.meta.url), 'utf8')
function action(name) {
    const start = source.indexOf(`        ${name}(`)
    assert.notEqual(start, -1, `action ${name} not found`)
    const end = source.indexOf('        /**', start)
    assert.ok(end > start, `action ${name} boundary not found`)
    return source.slice(start, end)
}
const deps = { SYNTHETIC_ITEM, setParsedContent, getParsedContent, initBuffer, flushBuffer, destroySessionBuffers,
    clearBlockInactivityTimer(block) { if (block._inactivityTimer) clearTimeout(block._inactivityTimer); block._inactivityTimer = null } }
const actions = new Function(...Object.keys(deps), `return { ${['_onBufferDrain', 'streamBlockStart', '_retireStreamingBlocks'].map(action).join('')} }`)(...Object.values(deps))
let fixtureId = 0
function makeRow(block, lineNum) {
    const row = { lineNum, syntheticKind: SYNTHETIC_ITEM.STREAMING_BLOCK.kind }
    setParsedContent(row, { type: 'assistant', message: { role: 'assistant', content: [block.blockType === 'thinking'
        ? { type: 'thinking', thinking: block.displayedText, streaming: !block.stopped }
        : { type: 'text', text: block.displayedText }] } })
    return row
}
function makeFixture({ blockType = 'text', stopped = false, rowCount = 2 } = {}) {
    const sessionId = `stream-fixture-${++fixtureId}`, blockIndex = 0
    const lineNum = SYNTHETIC_ITEM.STREAMING_BLOCK.baseLineNum - blockIndex
    const block = { blockIndex, blockType, displayedText: '', text: '', stopped, uuid: 'uuid' }
    const row = makeRow(block, lineNum)
    const rows = [...Array.from({ length: rowCount - 1 }, (_, i) => ({ lineNum: i + 1 })), row]
    const store = defineStore(sessionId, {
        state: () => ({ localState: { streamingBlocks: { [sessionId]: { messageId: 'message', blocks: [block] } },
            sessionVisualItems: { [sessionId]: rows }, visualItemCache: { [sessionId]: new Map(rows.map(r => [r.lineNum, r])) },
            sessionExpandedGroups: {}, openDetails: {} } }),
        actions: { ...actions,
            setDetailOpen(id, key, open) { (this.localState.openDetails[id] ??= {})[key] = open },
            isDetailOpen(id, key) { return !!this.localState.openDetails[id]?.[key] },
            recomputeVisualItems() { throw new Error('unexpected structural rebuild') } },
    })(createPinia())
    return { store, sessionId, blockIndex, lineNum, block: store.localState.streamingBlocks[sessionId].blocks[0],
        row: store.localState.visualItemCache[sessionId].get(lineNum), rows: store.localState.sessionVisualItems[sessionId],
        cache: store.localState.visualItemCache[sessionId] }
}
const publish = (f, text = 'latest text') => f.store._onBufferDrain(f.sessionId, f.blockIndex, text)
// Explicit structural fixture replacement; this does not execute production recomputation.
function replaceRows(f, rows) {
    f.store.localState.sessionVisualItems[f.sessionId] = rows
    f.store.localState.visualItemCache[f.sessionId] = new Map(rows.map(r => [r.lineNum, r]))
}
test('text publication preserves row list and cache identity', () => {
    const f = makeFixture(), before = getParsedContent(f.row), untouched = f.rows[0]
    publish(f)
    assert.strictEqual(f.store.localState.sessionVisualItems[f.sessionId], f.rows)
    assert.strictEqual(f.store.localState.visualItemCache[f.sessionId], f.cache)
    assert.strictEqual(f.rows.at(-1), f.row)
    assert.strictEqual(f.cache.get(f.lineNum), f.row)
    assert.strictEqual(f.rows[0], untouched)
    assert.notStrictEqual(getParsedContent(f.row), before)
    assert.equal(getParsedContent(f.row).message.content[0].text, 'latest text')
})
test('thinking publication preserves shape and stopped flag', () => {
    for (const stopped of [false, true]) {
        const f = makeFixture({ blockType: 'thinking', stopped })
        publish(f)
        assert.deepEqual(getParsedContent(f.row).message.content[0], { type: 'thinking', thinking: 'latest text', streaming: !stopped })
    }
})
test('publication does not scan the visual list', () => {
    const f = makeFixture()
    Object.defineProperty(f.rows, 'findIndex', { configurable: true, value() { throw new Error('visual list scan') } })
    publish(f)
})
test('publication does not rebuild the visual list', () => { publish(makeFixture()) })
test('missing stream or block produces no patch', () => {
    for (const missing of ['stream', 'block']) {
        const f = makeFixture(), before = getParsedContent(f.row)
        if (missing === 'stream') delete f.store.localState.streamingBlocks[f.sessionId]
        else f.block.blockIndex = 3
        publish(f)
        assert.strictEqual(getParsedContent(f.row), before)
    }
})
test('missing cache or row retains latest displayed text', () => {
    for (const missing of ['cache', 'row']) {
        const f = makeFixture(), before = getParsedContent(f.row)
        if (missing === 'cache') delete f.store.localState.visualItemCache[f.sessionId]
        else f.cache.delete(f.lineNum)
        publish(f)
        assert.equal(f.block.displayedText, 'latest text')
        assert.strictEqual(getParsedContent(f.row), before)
        assert.strictEqual(f.rows.at(-1), f.row)
    }
})
test('another synthetic kind receives no patch', () => {
    const f = makeFixture(), before = getParsedContent(f.row)
    f.row.syntheticKind = 'other'
    publish(f)
    assert.strictEqual(getParsedContent(f.row), before)
    assert.strictEqual(f.rows.at(-1), f.row)
})
test('publication uses the cache after structural replacement', () => {
    const f = makeFixture(), before = getParsedContent(f.row)
    replaceRows(f, [makeRow(f.block, f.lineNum)])
    publish(f)
    assert.equal(getParsedContent(f.store.localState.visualItemCache[f.sessionId].get(f.lineNum)).message.content[0].text, 'latest text')
    assert.strictEqual(getParsedContent(f.row), before)
})
test('filtered row returns with latest displayed text', () => {
    const f = makeFixture()
    replaceRows(f, [])
    publish(f)
    replaceRows(f, [makeRow(f.block, f.lineNum)])
    assert.equal(getParsedContent(f.store.localState.sessionVisualItems[f.sessionId][0]).message.content[0].text, 'latest text')
})

// In-memory Vue host. Components run in real scopes with the application's parsed-prop flow.
const renderer = createRenderer({
    createElement: tag => ({ tag, children: [] }), createText: text => ({ text }), createComment: text => ({ text }),
    setText(node, text) { node.text = text }, setElementText(node, text) { node.text = text },
    parentNode: node => node.parent, nextSibling: () => null, patchProp() {},
    insert(node, parent) { node.parent = parent; parent.children.push(node) },
    remove(node) { const children = node.parent?.children; if (children) children.splice(children.indexOf(node), 1) },
})
function mount(setup) {
    const app = renderer.createApp({ setup })
    app.mount({ children: [] })
    return () => app.unmount()
}
function mountGeometry(f) {
    let scroller, keyReads = 0, mounts = 0
    const texts = []
    const Content = { props: ['content'], setup(props) {
        onMounted(() => mounts++)
        watch(() => props.content.message.content[0].text, text => texts.push(text), { immediate: true })
        return () => h('span', props.content.message.content[0].text)
    } }
    const Row = { props: ['row'], setup: (props, { slots }) => () => h('div', slots.default({ row: props.row })) }
    const unmount = mount(() => {
        scroller = useVirtualScroll({ items: computed(() => f.store.localState.sessionVisualItems[f.sessionId]),
            itemKey: row => { keyReads++; return row.lineNum }, containerRef: shallowRef(null), minItemHeight: 20 })
        return () => { scroller.positions.value; return h(Row, { row: f.store.localState.sessionVisualItems[f.sessionId].at(-1) }, { default: ({ row }) => h(Content, { content: getParsedContent(row) }) }) }
    })
    return { scroller, texts, unmount, get mounts() { return mounts }, get keyReads() { return keyReads },
        reset() { keyReads = 0; texts.length = 0 } }
}
test('content publications notify Vue without geometry invalidation', async () => {
    const f = makeFixture({ rowCount: 2001 }), view = mountGeometry(f)
    try {
        await nextTick()
        const positions = view.scroller.positions.value
        view.reset()
        for (let i = 0; i < 60; i++) {
            publish(f, `update-${i}`)
            await nextTick()
            assert.strictEqual(view.scroller.positions.value, positions)
        }
        assert.equal(view.keyReads, 0)
        assert.equal(view.mounts, 1)
        assert.equal(view.texts.length, 60)
        assert.equal(view.texts.at(-1), 'update-59')
    } finally { view.unmount() }
})
test('actual height changes still update geometry', async () => {
    const f = makeFixture({ rowCount: 2001 }), view = mountGeometry(f)
    try {
        await nextTick()
        const before = view.scroller.positions.value
        view.scroller.batchUpdateItemHeights(new Map([[1, 80], [f.lineNum, 120]]))
        await nextTick()
        const after = view.scroller.positions.value
        assert.notStrictEqual(after, before)
        assert.equal(after[1].top, 80)
        assert.equal(after.at(-1).height, 120)
        assert.equal(view.scroller.totalHeight.value, 2001 * 20 + 60 + 100)
    } finally { view.unmount() }
})
test('exit-only streaming rows stay unchanged and reopening uses current text', async () => {
    const f = makeFixture(), timers = new Map()
    let exits
    const unmount = mount(() => {
        exits = useListExit({ items: computed(() => f.store.localState.sessionVisualItems[f.sessionId]),
            getKey: row => row.lineNum, isEligible: () => true, scopeKey: () => f.sessionId,
            getVisibleRange: () => ({ start: 0, end: 2 }), getHeight: () => 40,
            env: { setTimeout(fn) { const id = {}; timers.set(id, fn); return id }, clearTimeout(id) { timers.delete(id) } } })
        return () => h('span')
    })
    try {
        const before = getParsedContent(f.row)
        replaceRows(f, [f.rows[0]])
        await nextTick()
        assert.ok(exits.displayItems.value.some(row => row === f.row))
        publish(f, 'hidden latest')
        assert.strictEqual(getParsedContent(f.row), before)
        assert.equal(f.block.displayedText, 'hidden latest')
        replaceRows(f, [f.rows[0], makeRow(f.block, f.lineNum)])
        await nextTick()
        assert.equal(exits.displayItems.value.filter(row => row.lineNum === f.lineNum).length, 1)
        const current = f.store.localState.visualItemCache[f.sessionId].get(f.lineNum)
        assert.equal(getParsedContent(current).message.content[0].text, 'hidden latest')
        publish(f, 'reopened latest')
        assert.equal(getParsedContent(current).message.content[0].text, 'reopened latest')
        assert.strictEqual(getParsedContent(f.row), before)
    } finally { unmount(); assert.equal(timers.size, 0) }
})
function withFrames(run) {
    const oldRequest = globalThis.requestAnimationFrame, oldCancel = globalThis.cancelAnimationFrame
    const frames = new Map(), cancelled = []
    let id = 0, time = performance.now()
    globalThis.requestAnimationFrame = callback => { frames.set(++id, callback); return id }
    globalThis.cancelAnimationFrame = handle => { cancelled.push(handle); frames.delete(handle) }
    try { run({ frames, cancelled, drain() {
        for (let count = 0; frames.size && count < 1000; count++) {
            const [handle, callback] = frames.entries().next().value
            frames.delete(handle); callback(time += 100)
        }
        assert.equal(frames.size, 0)
    } }) } finally {
        destroyAllBuffers()
        globalThis.requestAnimationFrame = oldRequest; globalThis.cancelAnimationFrame = oldCancel
    }
}
for (const [provider, thinking, name] of [
    ['claude', false, 'retirement flushes the latest text before removing a Claude block'],
    ['codex', true, 'retirement flushes Codex stream_uuid and transfers thinking state'],
]) test(name, () => withFrames(({ frames }) => {
    const f = makeFixture({ blockType: thinking ? 'thinking' : 'text' }), observed = []
    initBuffer(f.sessionId, f.blockIndex, text => {
        assert.equal(f.store.localState.streamingBlocks[f.sessionId].blocks.length, 1)
        publish(f, text); observed.push(getParsedContent(f.row).message.content[0])
    })
    feedDelta(f.sessionId, f.blockIndex, 'complete pending text')
    assert.equal(frames.size, 1)
    const item = { line_num: 42, kind: thinking ? 'reasoning' : 'assistant_message', group_head: 42 }
    setParsedContent(item, { uuid: 'uuid', message: { id: 'message', content: [{ type: thinking ? 'thinking' : 'text' }] } })
    if (provider === 'codex') item.stream_uuid = 'uuid'
    if (thinking) {
        f.store.setDetailOpen(f.sessionId, `line:${f.lineNum}:0`, true)
        f.store.localState.sessionExpandedGroups[f.sessionId] = [f.lineNum]
    }
    assert.deepEqual(f.store._retireStreamingBlocks(f.sessionId, [item]), [{ streamingLineNum: f.lineNum, realLineNum: 42 }])
    assert.equal(observed.at(-1)[thinking ? 'thinking' : 'text'], 'complete pending text')
    assert.equal(f.store.localState.streamingBlocks[f.sessionId], undefined)
    assert.equal(frames.size, 0)
    if (thinking) {
        assert.equal(f.store.isDetailOpen(f.sessionId, 'line:42:0'), true)
        assert.equal(f.store.isDetailOpen(f.sessionId, `line:${f.lineNum}:0`), false)
        assert.deepEqual([...f.store.localState.sessionExpandedGroups[f.sessionId]], [42])
    }
}))
test('a new message replaces the buffer at a reused synthetic index', () => withFrames(({ frames, cancelled, drain }) => {
    const f = makeFixture()
    // This dependency is fixture structural rebuilding, not production recomputeVisualItems.
    f.store.recomputeVisualItems = () => {
        const block = f.store.localState.streamingBlocks[f.sessionId].blocks[0]
        replaceRows(f, [makeRow(block, f.lineNum)])
    }
    f.store.streamBlockStart(f.sessionId, 'A', 0, 'text')
    feedDelta(f.sessionId, 0, 'old text')
    const oldHandle = frames.keys().next().value
    f.store.streamBlockStart(f.sessionId, 'B', 0, 'text')
    assert.ok(cancelled.includes(oldHandle))
    assert.equal(frames.size, 0)
    feedDelta(f.sessionId, 0, 'new text')
    drain()
    assert.equal(f.store.localState.streamingBlocks[f.sessionId].messageId, 'B')
    assert.equal(getParsedContent(f.store.localState.visualItemCache[f.sessionId].get(f.lineNum)).message.content[0].text, 'new text')
}))
