import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
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
const actions = new Function('SYNTHETIC_ITEM', 'setParsedContent', `return { ${action('_onBufferDrain')} }`)(SYNTHETIC_ITEM, setParsedContent)
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
        actions: { ...actions, recomputeVisualItems() { throw new Error('unexpected structural rebuild') } },
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
