import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { createPinia, defineStore } from 'pinia'
import { getParsedContent, setParsedContent, clearParsedContent, hasContent } from '../utils/parsedContent.js'
import { initBuffer, feedDelta, flushBuffer, destroySessionBuffers, destroyAllBuffers, isBufferActive } from '../utils/streamingBuffer.js'
import { createStreamPublicationIdentity } from '../utils/streamPublicationRegistry.js'
import { matchesStreamingBlock as codexMatch } from '../providers/codex/streamMatching.js'
import { matchesStreamingBlock as claudeMatch } from '../providers/claude_code/streamMatching.js'
import { SYNTHETIC_ITEM } from '../constants.js'
import { createAsyncQuestionActions } from '../utils/asyncQuestionState.js'
const source = readFileSync(new URL('./data.js', import.meta.url), 'utf8')
function action(name) {
    const starts = [`        ${name}(`, `        async ${name}(`].map(prefix => source.indexOf(prefix)).filter(index => index >= 0)
    assert.ok(starts.length, `action ${name} not found`)
    const start = Math.min(...starts), end = source.indexOf('        /**', start)
    assert.ok(end > start, `action ${name} boundary not found`)
    return source.slice(start, end)
}
let fixtureId = 0
function makeRetirementFixture({ provider = 'codex', fetches = [] } = {}) {
    const sessionId = `retirement-${++fixtureId}`, otherSessionId = `${sessionId}-other`, recomputations = [], retirements = []
    const deferred = []
    const deps = { getParsedContent, setParsedContent, clearParsedContent, hasContent, initBuffer, feedDelta, flushBuffer,
        destroySessionBuffers, isBufferActive, createStreamPublicationIdentity, SYNTHETIC_ITEM, STREAM_BLOCK_INACTIVITY_MS: 1000,
        getProviderHelpers: provider => ({ codex: { matchesStreamingBlock: codexMatch }, claude_code: { matchesStreamingBlock: claudeMatch } })[provider],
        clearBlockInactivityTimer(block) { clearTimeout(block._inactivityTimer); block._inactivityTimer = null },
        isLaunchedEphemeral: () => false,
        apiFetch: () => fetches.length ? Promise.resolve(fetches.shift()) : new Promise((resolve, reject) => deferred.push({ resolve, reject })) }
    const names = ['_onBufferDrain', '_retireStreamingBlocks', 'streamBlockStart', 'streamBlockDelta', 'streamBlockStop', 'streamBlockEnd',
        'updateSessionItemsContent', 'addSessionItems', 'loadSessionItemsRanges', '_dropOrphanedStreamingBlocks']
    names.push('_findStreamingReplacement')
    const actions = new Function(...Object.keys(deps), `return { ${names.map(action).join('')} }`)(...Object.values(deps))
    const store = defineStore(sessionId, { state: () => ({ sessions: { [sessionId]: { provider } }, processStates: {}, sessionItems: {},
        localState: { sessions: {}, streamingBlocks: {}, sessionExpandedGroups: {}, openDetails: {}, optimisticMessages: {}, visualItemCache: {} } }),
        actions: { ...actions, getSession(id) { return this.sessions[id] },
            setDetailOpen(id, key, open) { (this.localState.openDetails[id] ??= {})[key] = open },
            isDetailOpen(id, key) { return !!this.localState.openDetails[id]?.[key] },
            recomputeVisualItems(id) {
                const stream = this.localState.streamingBlocks[id]
                const provider = this.sessions[id]?.provider ?? this.processStates[id]?.provider
                const helper = deps.getProviderHelpers(provider)
                for (const block of stream?.blocks ?? []) {
                    for (const persisted of this.sessionItems[id] ?? []) {
                        assert.equal(helper?.matchesStreamingBlock(getParsedContent(persisted), persisted.kind, stream.messageId, block) ?? false,
                            false, 'persisted replacement must retire before recompute')
                    }
                }
                recomputations.push({ id, blocks: (stream?.blocks ?? []).map(b => ({ ...b })) })
            },
            resolveInflightSends() {}, clearOptimisticMessageIfMatched() {}, ensureSessionItemsCoverage: async () => {} } })(createPinia())
    store.$onAction(({ name, after }) => { if (name === '_retireStreamingBlocks') after(result => retirements.push(result)) })
    return { store, sessionId, otherSessionId, recomputations, retirements, deferred }
}
function item(type = 'text', id = 'A', line_num = 1) {
    return { line_num, kind: type === 'thinking' ? 'reasoning' : 'assistant_message', group_head: line_num,
        content: JSON.stringify(type === 'thinking' ? { type: 'response_item', payload: { type: 'reasoning', id,
            summary: [{ type: 'summary_text', text: 'part one' }, { type: 'summary_text', text: 'part two' }] } }
            : { type: 'event_msg', payload: { type: 'item_completed', item: { type: 'AgentMessage', id, text: 'answer' } } }) }
}
const start = (f, type = 'text', id = 'A') => f.store.streamBlockStart(f.sessionId, id, 0, type)
const current = f => f.store.localState.streamingBlocks[f.sessionId]
test.afterEach(() => destroyAllBuffers())
for (const type of ['text', 'thinking']) {
    test(`${type}: persisted item retires before end`, () => {
        const f = makeRetirementFixture()
        start(f, type)
        f.store.addSessionItems(f.sessionId, [item(type)])
        assert.equal(current(f), undefined)
        assert.deepEqual(f.retirements.at(-1), [{ streamingLineNum: SYNTHETIC_ITEM.STREAMING_BLOCK.baseLineNum, realLineNum: 1 }])
        assert.deepEqual(f.recomputations.at(-1).blocks, [])
    })
    test(`${type}: end waits for persisted replacement`, () => {
        const f = makeRetirementFixture()
        start(f, type)
        f.store.streamBlockEnd(f.sessionId, 'A', 0, 'A')
        assert.ok(current(f))
        f.store.addSessionItems(f.sessionId, [item(type)])
        assert.equal(current(f), undefined)
    })
    test(`${type}: persisted item before start never creates a duplicate`, () => {
        const f = makeRetirementFixture()
        f.store.addSessionItems(f.sessionId, [item(type)])
        start(f, type)
        assert.equal(current(f), undefined)
        assert.equal(flushBuffer(f.sessionId, 0), null)
    })
    test(`${type}: late old start preserves newer streaming`, () => {
        const f = makeRetirementFixture()
        f.store.addSessionItems(f.sessionId, [item(type)])
        start(f, type, 'B')
        f.store.streamBlockDelta(f.sessionId, 'B', 0, 'new text')
        const block = current(f).blocks[0], identity = block.publicationIdentity
        start(f, type)
        assert.equal(current(f).messageId, 'B')
        assert.strictEqual(current(f).blocks[0], block)
        assert.equal(block.text, 'new text')
        assert.equal(block.publicationIdentity, identity)
        f.store.streamBlockDelta(f.sessionId, 'B', 0, ' retained')
        assert.equal(flushBuffer(f.sessionId, 0), 'new text retained')
    })
    test(`${type}: repeated active start is idempotent`, () => {
        const f = makeRetirementFixture()
        start(f, type)
        const block = current(f).blocks[0]
        f.store.streamBlockDelta(f.sessionId, 'A', 0, 'retained')
        start(f, type)
        assert.equal(current(f).blocks.length, 1)
        assert.strictEqual(current(f).blocks[0], block)
        assert.equal(flushBuffer(f.sessionId, 0), 'retained')
    })
    test(`${type}: content-only REST hydration retires streaming`, () => {
        const f = makeRetirementFixture()
        start(f, type)
        f.store.addSessionItems(f.sessionId, [{ line_num: 1 }])
        f.store.streamBlockEnd(f.sessionId, 'A', 0, 'A')
        assert.ok(current(f))
        f.store.updateSessionItemsContent(f.sessionId, [item(type)])
        assert.equal(current(f), undefined)
        assert.deepEqual(f.recomputations.at(-1).blocks, [])
    })
}
test('two messages match independently and forged stream_uuid has no authority', () => {
    const f = makeRetirementFixture()
    start(f)
    f.store.addSessionItems(f.sessionId, [{ ...item('text', 'B', 2), stream_uuid: 'A' }])
    assert.ok(current(f))
    f.store.addSessionItems(f.sessionId, [item()])
    assert.equal(current(f), undefined)
    start(f, 'text', 'B')
    assert.equal(current(f), undefined)
})
test('sessions cannot retire each other', () => {
    const f = makeRetirementFixture()
    start(f)
    f.store.addSessionItems(f.otherSessionId, [item()])
    assert.ok(current(f))
})
test('range REST response retires streaming', async () => {
    const f = makeRetirementFixture()
    start(f)
    const pending = f.store.loadSessionItemsRanges('project', f.sessionId, [1])
    assert.ok(current(f))
    f.deferred[0].resolve({ ok: true, json: async () => [item()] })
    assert.equal(await pending, true)
    assert.equal(current(f), undefined)
    assert.deepEqual(f.recomputations.at(-1).blocks, [])
})
test('failed REST preserves thinking state for retry', async () => {
    const f = makeRetirementFixture()
    start(f, 'thinking')
    const key = `line:${SYNTHETIC_ITEM.STREAMING_BLOCK.baseLineNum}:0`
    f.store.setDetailOpen(f.sessionId, key, true)
    f.store.localState.sessionExpandedGroups[f.sessionId] = [SYNTHETIC_ITEM.STREAMING_BLOCK.baseLineNum]
    f.store.streamBlockEnd(f.sessionId, 'A', 0, 'A')
    const oldError = console.error
    console.error = () => {}
    try {
        for (const rejected of [false, true]) {
            const pending = f.store.loadSessionItemsRanges('p', f.sessionId, [1])
            if (rejected) f.deferred.at(-1).reject(new Error('offline'))
            else f.deferred.at(-1).resolve({ ok: false, status: 503 })
            assert.equal(await pending, false)
            assert.ok(current(f))
            assert.equal(f.store.isDetailOpen(f.sessionId, key), true)
            assert.deepEqual([...f.store.localState.sessionExpandedGroups[f.sessionId]], [SYNTHETIC_ITEM.STREAMING_BLOCK.baseLineNum])
        }
    } finally { console.error = oldError }
    const pending = f.store.loadSessionItemsRanges('p', f.sessionId, [1])
    f.deferred.at(-1).resolve({ ok: true, json: async () => [item('thinking')] })
    assert.equal(await pending, true)
    assert.equal(current(f), undefined)
    assert.equal(f.store.isDetailOpen(f.sessionId, 'line:1:0'), true)
    assert.equal(f.store.isDetailOpen(f.sessionId, key), false)
    assert.deepEqual([...f.store.localState.sessionExpandedGroups[f.sessionId]], [1])
})
test('thinking replacement preserves expanded state and clears stale parsed content', () => {
    const f = makeRetirementFixture()
    start(f, 'thinking')
    const synthetic = SYNTHETIC_ITEM.STREAMING_BLOCK.baseLineNum
    f.store.setDetailOpen(f.sessionId, `line:${synthetic}:0`, true)
    f.store.localState.sessionExpandedGroups[f.sessionId] = [synthetic]
    f.store.addSessionItems(f.sessionId, [{ line_num: 1, kind: 'reasoning' }])
    setParsedContent(f.store.sessionItems[f.sessionId][0], { type: 'response_item', payload: { type: 'reasoning', id: 'wrong' } })
    f.store.updateSessionItemsContent(f.sessionId, [{ ...item('thinking'), group_head: 9 }])
    assert.equal(current(f), undefined)
    assert.equal(f.store.isDetailOpen(f.sessionId, 'line:1:0'), true)
    assert.equal(f.store.isDetailOpen(f.sessionId, `line:${synthetic}:0`), false)
    assert.deepEqual([...f.store.localState.sessionExpandedGroups[f.sessionId]], [9])
    assert.equal(getParsedContent(f.store.sessionItems[f.sessionId][0]).payload.summary.length, 2)
})
test('provider resolves from process state', () => {
    const f = makeRetirementFixture()
    delete f.store.sessions[f.sessionId]
    f.store.processStates[f.sessionId] = { provider: 'codex' }
    start(f)
    f.store.addSessionItems(f.sessionId, [item()])
    assert.equal(current(f), undefined)
})
test('unresolved provider is harmless', () => {
    const f = makeRetirementFixture({ provider: null })
    start(f)
    f.store.addSessionItems(f.sessionId, [item()])
    assert.ok(current(f))
    f.store.processStates[f.sessionId] = { provider: 'codex' }
    f.store.streamBlockEnd(f.sessionId, 'A', 0, 'A')
    assert.equal(current(f), undefined)
})
test('late delta stop and end cannot recreate a retired block', () => {
    const f = makeRetirementFixture()
    start(f)
    f.store.addSessionItems(f.sessionId, [item()])
    start(f, 'text', 'B')
    const block = current(f).blocks[0]
    f.store.streamBlockDelta(f.sessionId, 'A', 0, 'old')
    f.store.streamBlockStop(f.sessionId, 'A', 0)
    f.store.streamBlockEnd(f.sessionId, 'A', 0, 'A')
    assert.strictEqual(current(f).blocks[0], block)
    assert.equal(block.text, '')
    assert.equal(block.uuid, null)
})
test('retirement releases timers and pending publications', () => {
    const f = makeRetirementFixture()
    start(f)
    f.store.streamBlockDelta(f.sessionId, 'A', 0, 'pending')
    const block = current(f).blocks[0]
    assert.ok(block._inactivityTimer)
    f.store.addSessionItems(f.sessionId, [item()])
    assert.equal(block._inactivityTimer, null)
    assert.equal(flushBuffer(f.sessionId, 0), null)
    f.store._onBufferDrain(f.sessionId, 0, 'stale')
    assert.equal(current(f), undefined)
})
test('claude retires only the completed matching block', () => {
    const f = makeRetirementFixture({ provider: 'claude_code' })
    start(f, 'thinking')
    f.store.streamBlockStart(f.sessionId, 'A', 1, 'text')
    const real = { line_num: 1, kind: 'assistant_message', content: JSON.stringify({ uuid: 'completed', message: { id: 'A', content: [{ type: 'thinking' }] } }) }
    f.store.addSessionItems(f.sessionId, [real])
    assert.equal(current(f).blocks.length, 2)
    f.store.streamBlockEnd(f.sessionId, 'A', 0, 'completed')
    assert.equal(current(f).blocks.length, 1)
    assert.equal(current(f).blocks[0].blockIndex, 1)
})
test('interrupted unfinished block still gets orphan cleanup', () => {
    const f = makeRetirementFixture()
    start(f, 'thinking')
    f.store._dropOrphanedStreamingBlocks(f.sessionId)
    assert.equal(current(f), undefined)
    assert.equal(flushBuffer(f.sessionId, 0), null)
})
for (const type of ['text', 'thinking']) {
    test(`${type}: late delta stop and end preserve absent retired state`, () => {
        const f = makeRetirementFixture()
        start(f, type)
        f.store.addSessionItems(f.sessionId, [item(type)])
        const recomputations = f.recomputations.length
        assert.equal(current(f), undefined)
        for (const event of [
            () => f.store.streamBlockDelta(f.sessionId, 'A', 0, 'late'),
            () => f.store.streamBlockStop(f.sessionId, 'A', 0),
            () => f.store.streamBlockEnd(f.sessionId, 'A', 0, 'A'),
        ]) {
            event()
            assert.equal(current(f), undefined)
            assert.equal(flushBuffer(f.sessionId, 0), null)
            assert.equal(f.recomputations.length, recomputations)
        }
    })
}

for (const order of ['snapshot-first', 'transcript-first']) {
    test(`async source retirement and ready snapshot remain independent: ${order}`, async () => {
        const f = makeRetirementFixture()
        const state = f.store.localState
        Object.assign(state, { asyncQuestionSnapshots: {}, asyncQuestionDrafts: {},
            asyncQuestionNotices: {}, draftMessages: {}, draftAppendSignals: {} })
        Object.assign(f.store, createAsyncQuestionActions({
            recover: async () => {}, pendingSends: () => ({}), cancelDraftSave: () => {},
        }))
        const batch = { item_id: 'A', status: 'ready', questions: [{ index: 0, title: 'Keep the menu?', options: ['Yes'] }] }
        const snapshot = { revision: 1, widget_enabled: true, batches: [batch], resolutions: {} }
        const persisted = item()
        const parsed = getParsedContent(persisted)
        Object.assign(parsed.payload.item, { delivery: 'async', phase: 'final_answer', questions: batch.questions })
        // Preserve the canonical payload through normal content hydration.
        persisted.content = JSON.stringify(parsed)
        clearParsedContent(persisted)
        f.store.processStates[f.sessionId] = { provider: 'codex', state: 'assistant_turn', pending_requests: [] }
        const beforeProcess = JSON.parse(JSON.stringify(f.store.processStates[f.sessionId]))
        start(f)
        f.store.streamBlockDelta(f.sessionId, 'A', 0, 'Keep the menu?')
        if (order === 'snapshot-first') await f.store.applyAsyncQuestionSnapshot(f.sessionId, snapshot)
        f.store.addSessionItems(f.sessionId, [persisted])
        assert.equal(current(f), undefined)
        assert.equal(f.store.sessionItems[f.sessionId].length, 1)
        if (order === 'transcript-first') {
            assert.equal(state.asyncQuestionSnapshots[f.sessionId], undefined)
            await f.store.applyAsyncQuestionSnapshot(f.sessionId, snapshot)
        }
        assert.deepEqual(state.asyncQuestionSnapshots[f.sessionId].batches, [batch])
        assert.deepEqual(f.store.processStates[f.sessionId], beforeProcess)
        f.store.streamBlockEnd(f.sessionId, 'A', 0, 'A')
        assert.equal(current(f), undefined)
        assert.deepEqual(state.asyncQuestionSnapshots[f.sessionId].resolutions, {})
    })
}
