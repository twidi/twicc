// The recompute shortcuts (raw stabilization reads, untimed DEBUG_ONLY lines left out
// of the pipeline, DEBUG_ONLY-only appends that skip their recompute) must render
// exactly what the plain pipeline renders. Each test compares the real store actions
// with an oracle built from the same source, every shortcut turned off.
import { normalizeChatTimestamp, placeSessionEvents, orderCompactItems } from '../utils/chatEvents/placement.js'
import { projectEventSegments } from '../utils/chatEvents/segments.js'
import { mergeSessionEventRows, insertEventDaySeparators } from '../utils/chatEvents/rows.js'
import { jsonValuesEqual } from '../utils/jsonValuesEqual.js'
import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { createPinia, defineStore } from 'pinia'
import { computed, watch, nextTick, toRaw } from 'vue'
import { DISPLAY_LEVEL, DISPLAY_MODE, PROCESS_STATE, SYNTHETIC_ITEM } from '../constants.js'
import { computeVisualItems, visualItemEqual, insertDaySeparators, makeBackgroundWorkStatusItem, markLiveTimestampAnchor } from '../utils/visualItems.js'
import { getParsedContent, setParsedContent, hasContent } from '../utils/parsedContent.js'
import { buildBackgroundWorkStatusLines } from '../utils/backgroundWork.js'
import { isBufferActive } from '../utils/streamingBuffer.js'

const source = readFileSync(new URL('./data.js', import.meta.url), 'utf8')
function section(startText, endText) {
    const start = source.indexOf(startText)
    const end = source.indexOf(endText, start)
    assert.ok(start >= 0 && end > start, startText)
    return source.slice(start, end)
}
const helperSource = section('function getStreamingItemMetadata(', '\n/**') + section('function computeLastToolVisible(', '\n/**')
    + section('function isDebugForced(', '\n/**')
const actionSource = section('        recomputeVisualItems(sessionId)', '\n        /**')
    + section('        addSessionItems(sessionId, newItems, updatedMetadata', '\n        /**')

function patched(...replacements) {
    let body = actionSource
    for (const [from, to] of replacements) {
        assert.equal(body.match(new RegExp(from, 'g'))?.length, 1, `the oracle patch must land: ${from}`)
        body = body.replace(from, to)
    }
    return body
}
// The oracle feeds every item to the pipeline, recomputes after every append and
// stabilizes through the reactive cache, as before the shortcuts.
const ORACLE = patched(
    [/let items = sourceItems\n\s*if \(mode !== DISPLAY_MODE\.DEBUG\) \{[\s\S]*?\n {12}\}\n/, 'let items = sourceItems\n'],
    [/if \(recompute && !debugOnlyAppend\) this\.recomputeVisualItems\(sessionId\)/, 'if (recompute) this.recomputeVisualItems(sessionId)'],
    [/const cache = toRaw\(cacheProxy\) \|\| new Map\(\)/, 'const cache = cacheProxy || new Map()'],
    [/const previousItems = toRaw\(this\.localState\.sessionVisualItems\[sessionId\]\)/, 'const previousItems = this.localState.sessionVisualItems[sessionId]'],
)

let id = 0
function makeStore(body, { items, events = [], mode = DISPLAY_MODE.NORMAL, processState = null, expanded = [], detailed = [],
    compacted = false, optimistic = false, streaming = false }) {
    const settings = { getDisplayMode: mode, areMessageTimestampsShown: true }
    const deps = { placeSessionEvents, projectEventSegments, mergeSessionEventRows, insertEventDaySeparators, jsonValuesEqual,
        useSettingsStore: () => settings, DISPLAY_LEVEL, DISPLAY_MODE, PROCESS_STATE, SYNTHETIC_ITEM, computeVisualItems, visualItemEqual,
        insertDaySeparators, makeBackgroundWorkStatusItem, markLiveTimestampAnchor, orderCompactItems, getParsedContent, setParsedContent,
        hasContent, buildBackgroundWorkStatusLines, isBufferActive, isLaunchedEphemeral: () => false, toRaw, normalizeChatTimestamp,
        getProviderHelpers: () => ({ matchesStreamingBlock: () => false }) }
    const actions = new Function(...Object.keys(deps),
        `const debugAppendSafeSessions = new Set()\n${helperSource}\nreturn { ${body} }`)(...Object.values(deps))
    const optimisticItem = optimistic ? { line_num: SYNTHETIC_ITEM.OPTIMISTIC_USER_MESSAGE.lineNum, content: null, kind: 'user_message',
        syntheticKind: SYNTHETIC_ITEM.OPTIMISTIC_USER_MESSAGE.kind, display_level: DISPLAY_LEVEL.ALWAYS, group_head: null, group_tail: null } : null
    if (optimisticItem) setParsedContent(optimisticItem, { type: 'user', message: { content: 'next' } })
    return defineStore(`shortcuts-${++id}`, {
        state: () => ({ sessions: { main: { id: 'main', provider: 'codex', compacted } }, sessionItems: { main: items },
            processStates: processState ? { main: processState } : {},
            localState: { sessionEventSegments: {}, sessionEventRetired: {}, sessionInternalExpandedGroups: {}, failedSends: {},
                optimisticMessages: optimisticItem ? { main: optimisticItem } : {}, sessionDebugOverride: {},
                sessionExpandedGroups: { main: expanded }, sessionDetailedBlocks: { main: detailed },
                streamingBlocks: streaming ? { main: { messageId: 'm', blocks: [{ blockIndex: 0, blockType: 'text', text: 'Part',
                    displayedText: 'Part', stopped: false, publicationIdentity: 'p' }] } } : {},
                sessionVisualItems: {}, visualItemCache: {},
                sessionEventHistory: { main: { snapshot: { events, sessions: {} } } } } }),
        actions: { ...actions, resolveInflightSends() {}, ensureSessionItemsCoverage: async () => true,
            _retireStreamingBlocks: () => [], _migrateInternalSuffixToExternal() {} },
    })(createPinia())
}

// What the list renders: row keys and every flag or value the rows display.
const rendered = store => (store.localState.sessionVisualItems.main || []).map(row => JSON.stringify([row.lineNum,
    row.syntheticKind, row.workingStatusKey, row.isGroupHead, row.isExpanded, row.groupSize, row.detailToggleFor,
    row.prefixGroupHead, row.prefixExpanded, row.suffixGroupHead, row.suffixExpanded, row.suffixGroupSize, row.segmentRange,
    row.prefixSegmentRange, row.suffixSegmentRange, row.isBlockStart, row.isBlockEnd, row.isLiveTimestampAnchor,
    row.isDaySeparator ? row.dayKey : null, row.timestamp, getParsedContent(row)]))

function random(seed) {
    let state = seed
    return () => (state = (state * 16807) % 2147483647) / 2147483647
}
const T0 = Date.parse('2026-10-28T08:00:00Z')
const stub = line_num => ({ line_num, display_level: DISPLAY_LEVEL.DEBUG_ONLY, group_head: null, group_tail: null, kind: null, timestamp: null, content: null })

// A session with blocks, collapsible groups, DEBUG_ONLY stubs and timed DEBUG_ONLY
// lines, bare placeholders, compactions, leading or stub-only lines, and times that
// sometimes go missing or move backwards.
function randomSession(r) {
    const items = []
    let time = T0
    // Some sessions have no time at all (placement then anchors every event on line 1).
    const untimed = r() < 0.12
    const stamp = () => {
        const roll = r()
        if (untimed || roll < 0.12) return null
        time += roll < 0.2 ? -40_000 : Math.floor(r() * 90_000)
        return new Date(time).toISOString()
    }
    const push = fields => { items.push({ line_num: items.length + 1, group_head: null, group_tail: null, content: null, ...fields }); return items.at(-1) }
    const stubs = n => { for (let k = 0; k < n; k++) items.push(stub(items.length + 1)) }
    if (r() < 0.3) stubs(1 + Math.floor(r() * 3))
    const stubOnly = r() < 0.08
    for (let block = 0; !stubOnly && block < 5; block++) {
        push({ kind: 'user_message', display_level: DISPLAY_LEVEL.ALWAYS, timestamp: stamp() })
        let group = []
        const close = () => { for (const member of group) member.group_tail = group.at(-1).line_num; group = [] }
        for (let k = 2 + Math.floor(r() * 9); k > 0; k--) {
            const roll = r()
            if (roll < 0.38) {
                if (r() < 0.6) stubs(1)
                else push({ kind: 'system', display_level: DISPLAY_LEVEL.DEBUG_ONLY, timestamp: stamp() })
            } else if (roll < 0.43) push({})  // bare placeholder (a lost broadcast)
            else if (roll < 0.7) {
                const member = push({ kind: 'tool_use', display_level: DISPLAY_LEVEL.COLLAPSIBLE, timestamp: stamp() })
                member.group_head = group[0]?.line_num ?? member.line_num
                group.push(member)
            } else if (roll < 0.76) {
                // A compaction: its summary, a few DEBUG_ONLY lines, then the /compact command.
                close()
                push({ kind: 'compact_summary', display_level: DISPLAY_LEVEL.ALWAYS, timestamp: stamp() })
                stubs(Math.floor(r() * 12))
                push({ kind: 'user_message', display_level: DISPLAY_LEVEL.ALWAYS, timestamp: stamp(),
                    content: JSON.stringify({ message: { content: '<command-name>/compact</command-name>' } }) })
                // Rarely, a second command further on: each move shifts the summary's window.
                if (r() < 0.3) {
                    stubs(Math.floor(r() * 11))
                    push({ kind: 'user_message', display_level: DISPLAY_LEVEL.ALWAYS, timestamp: stamp(),
                        content: JSON.stringify({ message: { content: '<command-name>/compact</command-name>' } }) })
                }
            } else {
                close()
                push({ kind: 'assistant_message', display_level: DISPLAY_LEVEL.ALWAYS, timestamp: stamp() })
            }
        }
        close()
    }
    if (stubOnly) stubs(2 + Math.floor(r() * 4))
    const events = Array.from({ length: Math.floor(r() * 6) }, (_, i) => ({
        id: i + 1, kind: 'title.changed', session_id: 'main', data: {},
        occurred_at: new Date(T0 + Math.floor(r() * (time - T0 + 200_000))).toISOString(),
    }))
    return { items, events, time }
}
const copy = value => JSON.parse(JSON.stringify(value))
function randomOptions(r) {
    const roll = r()
    const processState = roll < 0.35 ? { state: PROCESS_STATE.ASSISTANT_TURN, label: 'working', tools: [{ id: 't', name: 'Bash' }], lastStartedToolId: 't' }
        : roll < 0.45 ? { state: PROCESS_STATE.STARTING } : null
    return { processState, compacted: r() < 0.5, optimistic: r() < 0.2, streaming: !!processState && r() < 0.3,
        expanded: [3, 5, 9, 12].filter(() => r() < 0.5), detailed: r() < 0.3 ? [1] : [] }
}

test('leaving untimed DEBUG_ONLY lines out of the pipeline renders what the full pipeline renders', () => {
    for (let seed = 1; seed <= 200; seed++) {
        const r = random(seed)
        const { items, events } = randomSession(r)
        for (const mode of [DISPLAY_MODE.CONVERSATION, DISPLAY_MODE.SIMPLIFIED, DISPLAY_MODE.NORMAL]) {
            const options = { ...randomOptions(r), events, mode }
            const real = makeStore(actionSource, { ...options, items: copy(items) })
            const oracle = makeStore(ORACLE, { ...options, items: copy(items) })
            real.recomputeVisualItems('main')
            oracle.recomputeVisualItems('main')
            assert.deepEqual(rendered(real), rendered(oracle), `seed ${seed}, ${mode}`)
        }
    }
})

test('a DEBUG_ONLY-only append that skips its recompute leaves the list as a recompute would', () => {
    let skipped = 0
    for (let seed = 1; seed <= 150; seed++) {
        const r = random(seed)
        const session = randomSession(r)
        // Some sessions get an event after every line: it trails, so appends must recompute.
        const events = r() < 0.5 ? session.events
            : [...session.events, { id: 99, kind: 'title.changed', session_id: 'main', data: {}, occurred_at: new Date(session.time + 500_000).toISOString() }]
        const mode = [DISPLAY_MODE.CONVERSATION, DISPLAY_MODE.SIMPLIFIED, DISPLAY_MODE.NORMAL][seed % 3]
        const options = { ...randomOptions(r), events, mode }
        const real = makeStore(actionSource, { ...options, items: copy(session.items) })
        const oracle = makeStore(ORACLE, { ...options, items: copy(session.items) })
        let realCalls = 0
        const recompute = real.recomputeVisualItems
        real.recomputeVisualItems = function (sessionId) { realCalls++; return recompute.call(this, sessionId) }
        real.recomputeVisualItems('main')
        oracle.recomputeVisualItems('main')
        let time = session.time
        for (let batch = 0; batch < 12; batch++) {
            const next = real.sessionItems.main.length + 1
            const debugOnly = r() < 0.7
            const lines = Array.from({ length: 1 + Math.floor(r() * 3) }, (_, i) => {
                time += Math.floor(r() * 60_000) - (r() < 0.15 ? 90_000 : 0)
                const timestamp = r() < 0.1 ? null : new Date(time).toISOString()
                return debugOnly || r() < 0.5
                    ? { line_num: next + i, kind: 'system', display_level: DISPLAY_LEVEL.DEBUG_ONLY, group_head: null, group_tail: null, timestamp, content: '{}' }
                    : { line_num: next + i, kind: 'assistant_message', display_level: DISPLAY_LEVEL.ALWAYS, group_head: null, group_tail: null, timestamp, content: '{}' }
            })
            const before = realCalls
            real.addSessionItems('main', copy(lines))
            oracle.addSessionItems('main', copy(lines))
            if (realCalls === before) skipped++
            assert.deepEqual(rendered(real), rendered(oracle), `seed ${seed}, batch ${batch}`)
        }
    }
    assert.ok(skipped > 100, `the shortcut must actually skip recomputes (skipped ${skipped})`)
})

test('the /compact reorder sees the same window after it moves a command', () => {
    // Moving the first command shifts the summary by one: its next window reaches one
    // line further (line 13 here), past 9 stubs.
    const command = JSON.stringify({ message: { content: '<command-name>/compact</command-name>' } })
    const at = seconds => new Date(T0 + seconds * 1000).toISOString()
    const items = [
        { line_num: 1, kind: 'user_message', display_level: DISPLAY_LEVEL.ALWAYS, group_head: null, group_tail: null, timestamp: at(0), content: null },
        { line_num: 2, kind: 'compact_summary', display_level: DISPLAY_LEVEL.ALWAYS, group_head: null, group_tail: null, timestamp: at(3), content: null },
        { line_num: 3, kind: 'user_message', display_level: DISPLAY_LEVEL.ALWAYS, group_head: null, group_tail: null, timestamp: at(2), content: command },
        ...Array.from({ length: 9 }, (_, i) => stub(4 + i)),
        { line_num: 13, kind: 'user_message', display_level: DISPLAY_LEVEL.ALWAYS, group_head: null, group_tail: null, timestamp: at(2.2), content: command },
        { line_num: 14, kind: 'assistant_message', display_level: DISPLAY_LEVEL.ALWAYS, group_head: null, group_tail: null, timestamp: at(9), content: null },
    ]
    const events = [{ id: 1, kind: 'title.changed', session_id: 'main', data: {}, occurred_at: at(2.5) }]
    for (const mode of [DISPLAY_MODE.CONVERSATION, DISPLAY_MODE.SIMPLIFIED, DISPLAY_MODE.NORMAL]) {
        const real = makeStore(actionSource, { items: copy(items), events, mode, compacted: true })
        const oracle = makeStore(ORACLE, { items: copy(items), events, mode, compacted: true })
        real.recomputeVisualItems('main')
        oracle.recomputeVisualItems('main')
        assert.deepEqual(rendered(real), rendered(oracle), mode)
    }
})

test('a session whose lines are all DEBUG_ONLY stubs still shows the working row', () => {
    const items = [stub(1), stub(2), stub(3)]
    for (const mode of [DISPLAY_MODE.SIMPLIFIED, DISPLAY_MODE.NORMAL]) {
        const store = makeStore(actionSource, { items, mode, processState: { state: PROCESS_STATE.ASSISTANT_TURN, label: null, tools: [] } })
        store.recomputeVisualItems('main')
        assert.deepEqual(store.localState.sessionVisualItems.main.map(row => row.syntheticKind),
            [SYNTHETIC_ITEM.WORKING_ASSISTANT_MESSAGE.kind], mode)
    }
})

test('a stabilized row still re-renders when only its parsed content changes', async () => {
    const store = makeStore(actionSource, { streaming: true, items: [{ line_num: 1, kind: 'user_message', display_level: DISPLAY_LEVEL.ALWAYS,
        group_head: null, group_tail: null, timestamp: '2026-10-28T08:00:00Z', content: null }] })
    store.recomputeVisualItems('main')
    const streamingRow = () => store.localState.sessionVisualItems.main.find(row => row.syntheticKind === SYNTHETIC_ITEM.STREAMING_BLOCK.kind)
    const firstRow = streamingRow()
    const text = computed(() => getParsedContent(streamingRow())?.message?.content?.[0]?.text)
    const seen = []
    watch(text, value => seen.push(value))
    assert.equal(text.value, 'Part')
    store.localState.streamingBlocks.main.blocks[0].text = 'Part two'
    store.recomputeVisualItems('main')
    await nextTick()
    assert.equal(toRaw(streamingRow()), toRaw(firstRow), 'the row object is reused (same keys)')
    assert.deepEqual(seen, ['Part two'], 'its new parsed content reaches the render')
})
