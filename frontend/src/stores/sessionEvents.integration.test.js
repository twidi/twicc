import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { createPinia, defineStore } from 'pinia'
import { toRaw } from 'vue'
import { createSessionEventHistoryActions } from '../utils/chatEvents/historyActions.js'
import { normalizeChatTimestamp, placeSessionEvents, orderCompactItems } from '../utils/chatEvents/placement.js'
import { projectEventSegments, setEventSegmentExpanded } from '../utils/chatEvents/segments.js'
import { createVisualItemsViews } from '../utils/visualItemsViews.js'
import { mergeSessionEventRows, insertEventDaySeparators } from '../utils/chatEvents/rows.js'
import { captureEventScroll, planEventScrollRestore } from '../utils/chatEvents/scroll.js'
import { compareNewestFirst } from '../utils/timeline/eventTime.js'
import { jsonValuesEqual } from '../utils/jsonValuesEqual.js'
import { DISPLAY_LEVEL, DISPLAY_MODE, PROCESS_STATE, SYNTHETIC_ITEM } from '../constants.js'
import { computeVisualItems, visualItemEqual, insertDaySeparators, makeBackgroundWorkStatusItem, markLiveTimestampAnchor } from '../utils/visualItems.js'
import { getParsedContent, setParsedContent, clearParsedContent } from '../utils/parsedContent.js'
import { buildBackgroundWorkStatusLines } from '../utils/backgroundWork.js'
import { isBufferActive, flushBuffer, destroySessionBuffers } from '../utils/streamingBuffer.js'
import { matchesStreamingBlock as codexMatch } from '../providers/codex/streamMatching.js'
import { matchesStreamingBlock as claudeMatch } from '../providers/claude_code/streamMatching.js'
import { collectMissingScrollerLines } from '../utils/scrollerLoadWindow.js'
const source = readFileSync(new URL('./data.js', import.meta.url), 'utf8')
function section(startText, endText) {
    const start = source.indexOf(startText), end = source.indexOf(endText, start)
    assert.ok(start >= 0 && end > start); return source.slice(start, end)
}
const tick = async () => { for (let i = 0; i < 20; i++) await Promise.resolve() }
const time = second => new Date(2026, 9, 27, 0, 0, second).toISOString()
const event = (id, second = 1, extra = {}) => ({ id, session_id: 's', kind: 'settings.changed', occurred_at: time(second), actor: 'user', data: {}, ...extra })
const page = (events, { latest = 250, more = false, ...extra } = {}) => ({ events, sessions: {}, latest_id: latest, has_more: more, truncated: false, ...extra })
function message(line, extra = {}) {
    return { line_num: line, kind: 'assistant_message', display_level: DISPLAY_LEVEL.ALWAYS, timestamp: time(line),
        content: JSON.stringify({ type: 'assistant', message: { role: 'assistant', content: [{ type: 'text', text: `Text ${line}` }] } }), ...extra }
}
function tools(provider) {
    return [message(1, { kind: 'user_message' }), ...Array.from({ length: 20 }, (_, i) => {
        const line = i + 2
        if (provider === 'claude_code' && line === 11) return message(11, { group_head: 2, group_tail: 21,
            content: JSON.stringify({ type: 'assistant', message: { role: 'assistant', content: [
                { type: 'tool_use', id: 'prefix', name: 'Read' }, { type: 'text', text: 'Progress' }, { type: 'tool_use', id: 'suffix', name: 'Read' }] } }) })
        return message(line, { kind: provider === 'codex' ? 'tool_call' : 'assistant_message', display_level: DISPLAY_LEVEL.COLLAPSIBLE,
            group_head: provider === 'claude_code' && line > 11 ? 11 : 2, group_tail: provider === 'claude_code' && line < 11 ? 11 : 21 })
    }), message(22)]
}
let serial = 0
function fixture(t, provider = 'codex', items = tools(provider)) {
    const requests = [], settings = { getDisplayMode: DISPLAY_MODE.SIMPLIFIED, areMessageTimestampsShown: true }
    const history = createSessionEventHistoryActions({ eventTarget: () => null, fetchPage: params => new Promise((resolve, reject) => requests.push({ ...params, resolve, reject })) })
    const dependencies = { placeSessionEvents, orderCompactItems, projectEventSegments, setEventSegmentExpanded, mergeSessionEventRows,
        insertEventDaySeparators, jsonValuesEqual, useSettingsStore: () => settings, DISPLAY_LEVEL, DISPLAY_MODE, PROCESS_STATE, SYNTHETIC_ITEM,
        computeVisualItems, visualItemEqual, insertDaySeparators, makeBackgroundWorkStatusItem, markLiveTimestampAnchor,
        getParsedContent, setParsedContent, buildBackgroundWorkStatusLines, isBufferActive, isLaunchedEphemeral: () => false,
        getProviderHelpers: provider => ({ matchesStreamingBlock: provider === 'codex' ? codexMatch : claudeMatch }),
        flushBuffer, destroySessionBuffers, clearBlockInactivityTimer: () => {}, toRaw, normalizeChatTimestamp,
        debugAppendSafeSessions: new Set(), visualItemsViews: createVisualItemsViews({ document: null }) }
    const helpers = section('function computeLastToolVisible(', '\n/**') + section('function getStreamingItemMetadata(', '\n/**')
    const actionNames = ['recomputeVisualItems', 'toggleExpandedGroup', 'expandGroup', '_retireStreamingBlocks']
    const actions = new Function(...Object.keys(dependencies), `${helpers}; return { ${actionNames.map(name => section(`        ${name}(`, '\n        /**')).join('\n')} }`)(...Object.values(dependencies))
    const store = defineStore(`event-integration-${++serial}`, { state: () => ({ sessions: { s: { id: 's', provider, last_line: items.length } }, sessionItems: { s: items }, processStates: {},
        localState: { sessionEventSegments: {}, sessionEventRetired: {}, sessionInternalExpandedGroups: {}, sessionEventHistory: {}, failedSends: {}, optimisticMessages: {},
            sessionDebugOverride: {}, sessionExpandedGroups: {}, sessionDetailedBlocks: {}, streamingBlocks: {}, sessionVisualItems: {}, visualItemCache: {} } }),
        actions: { ...history, ...actions, getSession(id) { return this.sessions[id] }, isDetailOpen: () => false } })(createPinia())
    store.recomputeVisualItems('s')
    const controller = store.ensureSessionEventHistory('s')
    t.after(() => store.clearSessionEventHistory('s'))
    async function answer(response, error = null) {
        await tick(); const request = requests.shift(); assert.ok(request, 'a production request waits for this response')
        if (error) request.reject(error); else request.resolve(response)
        await tick(); return request
    }
    async function complete(events) {
        const ordered = [...events].sort(compareNewestFirst)
        for (let start = 0; start < ordered.length || start === 0; start += 100) await answer(page(ordered.slice(start, start + 100), { more: start + 100 < ordered.length }))
        await answer(page([])); await controller.idle()
    }
    return { store, settings, controller, answer, complete, requests, rows: () => store.localState.sessionVisualItems.s,
        state: () => store.localState.sessionEventHistory.s }
}
for (const provider of ['claude_code', 'codex']) {
    test(`${provider}: complete pagination, failed truncated recovery and inherited independent segment states`, async t => {
        const f = fixture(t, provider), initial = [...Array.from({ length: 200 }, (_, i) => event(i + 1)), event(201, 16)]
        await f.complete(initial)
        assert.equal(f.state().snapshot.events.length, 201)
        f.store.toggleExpandedGroup('s', 16)
        const snapshot = f.state().snapshot, before = f.rows()
        assert.equal(f.store.localState.sessionEventSegments.s.find(segment => segment.head === 16).expanded, true)
        f.controller.refresh(); await f.answer(page([], { truncated: true }))
        await f.answer(page([...initial].sort(compareNewestFirst).slice(0, 100), { more: true }))
        await f.answer(null, new Error('recovery page failed')); await f.controller.idle()
        assert.equal(f.state().status, 'error'); assert.strictEqual(f.state().snapshot, snapshot); assert.strictEqual(f.rows(), before)
        f.controller.retry(); await f.complete([...initial, event(202, 13)])
        assert.equal(f.rows().filter(row => row.lineNum === 'event:202').length, 1)
        assert.deepEqual(f.store.localState.sessionEventSegments.s.map(segment => [segment.head, segment.expanded]),
            provider === 'claude_code' ? [[2, false], [11, false], [13, false], [16, true]] : [[2, false], [13, false], [16, true]])
        const eventKeys = f.rows().filter(row => row.isSessionEvent).map(row => row.lineNum)
        for (const mode of Object.values(DISPLAY_MODE)) {
            f.settings.getDisplayMode = mode; f.store.recomputeVisualItems('s')
            assert.deepEqual(f.rows().filter(row => row.isSessionEvent).map(row => row.lineNum), eventKeys)
            if (mode === DISPLAY_MODE.CONVERSATION) assert.deepEqual(f.rows().filter(row => row.kind === 'assistant_message').map(row => row.lineNum), [22])
        }
        f.settings.getDisplayMode = DISPLAY_MODE.NORMAL
        const sourceItem = f.store.sessionItems.s[3], content = JSON.stringify(getParsedContent(sourceItem))
        sourceItem.content = null; clearParsedContent(sourceItem); f.store.recomputeVisualItems('s')
        assert.deepEqual(collectMissingScrollerLines(f.rows(), { start: 0, end: f.rows().length }, 0), [4])
        sourceItem.content = content; f.store.recomputeVisualItems('s')
        assert.deepEqual(f.rows().filter(row => row.isSessionEvent).map(row => row.lineNum), eventKeys)
    })
}
test('events-only selection keeps target ownership and ignores disposed late responses', async t => {
    const f = fixture(t, 'codex', [])
    await f.complete([event(1, 1, { actor: 'agent', actor_ref: 'other' }), event(2, 1, { session_id: 'other', actor_ref: 's' }),
        event(3, 1, { kind: 'turn.started' }), event(4, 1, { session_id: null })])
    assert.deepEqual(f.rows().map(row => row.lineNum), ['event:1'])
    f.controller.refresh(); await tick(); const request = f.requests.shift()
    f.store.clearSessionEventHistory('s'); assert.equal(request.signal.aborted, true)
    request.resolve(page([event(5)])); await f.controller.idle()
    assert.equal(f.store.localState.sessionEventHistory.s, undefined); assert.deepEqual(f.rows(), [])
})
test('hidden catch-up removes cached notices before a late response can republish', async t => {
    const f = fixture(t, 'codex', []); await f.complete([event(1)])
    f.controller.refresh(); await f.answer(page([], { hidden_sessions: ['s'] })); await f.controller.idle()
    assert.equal(f.state().snapshot, null); assert.deepEqual(f.rows(), [])
    f.controller.refresh(); await tick(); assert.equal(f.requests.length, 0)
})
test('midnight events, compact correction, timestamps and user scroll cancellation combine', async t => {
    const previous = new Date(2026, 9, 26, 23, 59).toISOString()
    const f = fixture(t, 'claude_code', [message(1, { timestamp: previous }), message(2, { kind: 'compact_summary', timestamp: time(1) }),
        message(3, { kind: 'user_message', timestamp: previous, content: JSON.stringify({ message: { content: '<command-name>/compact</command-name>' } }) }), message(4)])
    f.store.sessions.s.compacted = true
    await f.complete([event(1, 0)])
    const numeric = f.rows().filter(row => typeof row.lineNum === 'number').map(row => row.lineNum)
    assert.deepEqual(numeric, [1, 3, 2, 4]); assert.equal(f.rows().filter(row => row.isDaySeparator).length, 1)
    const oldRows = f.rows(), positions = oldRows.map((row, index) => ({ key: row.lineNum, top: index * 50 }))
    const snapshot = captureEventScroll({ rows: oldRows, intent: 0, scroller: { positions, getScrollAnchor: () => ({ key: 4, index: oldRows.length - 1, offset: 10 }),
        isAtBottom: () => false, getScrollRevision: () => 0 } })
    f.settings.areMessageTimestampsShown = false; f.store.recomputeVisualItems('s')
    assert.equal(f.rows().some(row => row.isDaySeparator), false); assert.equal(f.rows().filter(row => row.isSessionEvent).length, 1)
    assert.equal(planEventScrollRestore({ snapshot, rows: f.rows(), positions: [], intent: 1, revision: 0 }).action, 'none')
})
for (const provider of ['claude_code', 'codex']) {
    test(`${provider}: streaming and optimistic replacement retain one notice and open only the destination`, async t => {
        const f = fixture(t, provider, [message(1, { kind: 'user_message' })]); await f.complete([event(1)])
        const base = SYNTHETIC_ITEM.STREAMING_BLOCK.baseLineNum
        f.store.localState.streamingBlocks.s = { messageId: 'thinking-id', blocks: [{ blockIndex: 0, blockType: 'thinking', uuid: 'thinking-id', stopped: true, text: 'Thinking' }] }
        f.store.recomputeVisualItems('s')
        f.store.toggleExpandedGroup('s', base)
        const real = provider === 'codex' ? message(2, { kind: 'reasoning', display_level: DISPLAY_LEVEL.COLLAPSIBLE, group_head: 2, group_tail: 2,
            content: JSON.stringify({ type: 'response_item', payload: { type: 'reasoning', id: 'thinking-id' } }) })
            : message(2, { content: JSON.stringify({ type: 'assistant', uuid: 'thinking-id', message: { id: 'thinking-id', role: 'assistant', content: [
                { type: 'text', text: 'Result' }, { type: 'thinking', thinking: 'Thinking' }] } }) })
        f.store.sessionItems.s.push(real); f.store._retireStreamingBlocks('s', [real]); f.store.recomputeVisualItems('s')
        assert.equal(f.rows().filter(row => row.isSessionEvent).length, 1)
        if (provider === 'codex') assert.equal(f.store.localState.sessionEventSegments.s.find(segment => segment.head === 2).expanded, true)
        else assert.deepEqual(f.store.localState.sessionInternalExpandedGroups.s[2], [1])
        const optimistic = message(SYNTHETIC_ITEM.OPTIMISTIC_USER_MESSAGE.lineNum, { kind: 'user_message', timestamp: time(3) })
        f.store.localState.optimisticMessages.s = optimistic; f.store.recomputeVisualItems('s')
        delete f.store.localState.optimisticMessages.s; f.store.sessionItems.s.push(message(3, { kind: 'user_message' })); f.store.recomputeVisualItems('s')
        assert.equal(f.rows().filter(row => row.isSessionEvent).length, 1); assert.equal(f.rows().some(row => row.lineNum === optimistic.line_num), false)
    })
}
// Synthetic rows (not yet in the JSONL) stand for what happens now: they render after
// every notice, also the notices later than every real line (a resume: stop, then resume).
for (const provider of ['claude_code', 'codex']) {
    const untimed = (line, extra = {}) => message(line, { timestamp: undefined, ...extra })
    const order = f => f.rows().filter(row => typeof row.lineNum === 'number' || row.isSessionEvent).map(row => row.lineNum)
    const { OPTIMISTIC_USER_MESSAGE: OPTIMISTIC, WORKING_ASSISTANT_MESSAGE: WORKING, STARTING_ASSISTANT_MESSAGE: STARTING,
        STREAMING_BLOCK: STREAMING, FAILED_USER_MESSAGE: FAILED } = SYNTHETIC_ITEM
    test(`${provider}: trailing notices render before the optimistic and working rows`, async t => {
        const f = fixture(t, provider, [message(1, { kind: 'user_message' }), message(2)]); await f.complete([event(1, 10), event(2, 11)])
        assert.deepEqual(order(f), [1, 2, 'event:1', 'event:2'])
        f.store.localState.optimisticMessages.s = untimed(OPTIMISTIC.lineNum, { kind: 'user_message' })
        f.store.processStates.s = { state: PROCESS_STATE.ASSISTANT_TURN }; f.store.recomputeVisualItems('s')
        assert.deepEqual(order(f), [1, 2, 'event:1', 'event:2', OPTIMISTIC.lineNum, WORKING.lineNum])
        // Once the real lines arrive, the notices stay before them.
        delete f.store.localState.optimisticMessages.s
        f.store.sessionItems.s.push(message(12, { kind: 'user_message' }), message(13)); f.store.processStates.s = { state: PROCESS_STATE.USER_TURN }
        f.store.recomputeVisualItems('s')
        assert.deepEqual(order(f), [1, 2, 'event:1', 'event:2', 12, 13])
    })
    test(`${provider}: trailing notices render before the starting row and a failed send`, async t => {
        const f = fixture(t, provider, [message(1, { kind: 'user_message' }), message(2)]); await f.complete([event(1, 10)])
        f.store.processStates.s = { state: PROCESS_STATE.STARTING }; f.store.recomputeVisualItems('s')
        assert.deepEqual(order(f), [1, 2, 'event:1', STARTING.lineNum])
        f.store.processStates.s = { state: PROCESS_STATE.USER_TURN }
        f.store.localState.failedSends.s = { a: { sentAt: 1, item: untimed(FAILED.baseLineNum, { kind: 'user_message' }) } }
        f.store.recomputeVisualItems('s')
        assert.deepEqual(order(f), [1, 2, 'event:1', FAILED.baseLineNum])
    })
    test(`${provider}: trailing notices render before streaming blocks and cut the group they join`, async t => {
        const f = fixture(t, provider, [message(1, { kind: 'user_message' }),
            message(2, { kind: 'tool_call', display_level: DISPLAY_LEVEL.COLLAPSIBLE, group_head: 2, group_tail: 2 })])
        await f.complete([event(1, 10)])
        f.store.processStates.s = { state: PROCESS_STATE.ASSISTANT_TURN }
        f.store.localState.streamingBlocks.s = { messageId: 'm', blocks: [
            { blockIndex: 0, blockType: 'thinking', uuid: 'm', stopped: true, text: 'Thinking' },
            { blockIndex: 1, blockType: 'text', uuid: 'm', stopped: false, text: 'Answer' }] }
        f.store.toggleExpandedGroup('s', 2); f.settings.getDisplayMode = DISPLAY_MODE.NORMAL; f.store.recomputeVisualItems('s')
        assert.deepEqual(order(f), [1, 2, 'event:1', STREAMING.baseLineNum, STREAMING.baseLineNum - 1])
        // The streamed thinking joins group 2, on the other side of the notice: its own segment.
        assert.deepEqual(f.store.localState.sessionEventSegments.s.map(segment => [segment.originalHead, segment.members]),
            [[2, [2]], [2, [STREAMING.baseLineNum]]])
    })
    test(`${provider}: notices bound to real lines keep their place before the synthetic rows`, async t => {
        const f = fixture(t, provider, [message(1, { kind: 'user_message' }), message(5)]); await f.complete([event(1, 3), event(2, 4)])
        assert.deepEqual(order(f), [1, 'event:1', 'event:2', 5])
        f.store.localState.optimisticMessages.s = untimed(OPTIMISTIC.lineNum, { kind: 'user_message' })
        f.store.processStates.s = { state: PROCESS_STATE.ASSISTANT_TURN }; f.store.recomputeVisualItems('s')
        assert.deepEqual(order(f), [1, 'event:1', 'event:2', 5, OPTIMISTIC.lineNum, WORKING.lineNum])
    })
}
