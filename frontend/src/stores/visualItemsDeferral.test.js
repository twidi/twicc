// A session no chat view shows (or every session while the page is hidden) defers its
// visual-items recompute until a view shows it again. Each test runs the same random live
// sequence on two stores built from the real data.js actions: one whose view comes and goes
// (and whose page hides and shows), one whose view always shows the session. Every time the
// first one is shown again, both must render the same thing.
import { normalizeChatTimestamp, placeSessionEvents, orderCompactItems } from '../utils/chatEvents/placement.js'
import { projectEventSegments, setEventSegmentExpanded } from '../utils/chatEvents/segments.js'
import { mergeSessionEventRows, insertEventDaySeparators } from '../utils/chatEvents/rows.js'
import { jsonValuesEqual } from '../utils/jsonValuesEqual.js'
import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { createPinia, defineStore } from 'pinia'
import { toRaw, watch, nextTick } from 'vue'
import { i18n, loadLocaleMessages } from '../i18n/index.js'
import { DISPLAY_LEVEL, DISPLAY_MODE, PROCESS_STATE, SYNTHETIC_ITEM } from '../constants.js'
import { computeVisualItems, visualItemEqual, insertDaySeparators, makeBackgroundWorkStatusItem, markLiveTimestampAnchor } from '../utils/visualItems.js'
import { getParsedContent, setParsedContent, hasContent } from '../utils/parsedContent.js'
import { getPrefixSuffixBoundaries } from '../utils/contentVisibility.js'
import { buildBackgroundWorkStatusLines } from '../utils/backgroundWork.js'
import { createStreamPublicationIdentity } from '../utils/streamPublicationRegistry.js'
import { matchesStreamingBlock } from '../providers/claude_code/streamMatching.js'
import { createVisualItemsViews } from '../utils/visualItemsViews.js'

// These rendering tests exercise both catalogs; production loads them on demand.
await loadLocaleMessages('pt-BR')

const source = readFileSync(new URL('./data.js', import.meta.url), 'utf8')
function section(startText, endText) {
    const start = source.indexOf(startText)
    const end = source.indexOf(endText, start)
    assert.ok(start >= 0 && end > start, startText)
    return source.slice(start, end)
}
const helperSource = section('function getStreamingItemMetadata(', '\n/**') + section('function computeLastToolVisible(', '\n/**')
    + section('function isDebugForced(', '\n/**')
const ACTIONS = ['recomputeVisualItems(sessionId)', 'recomputeAllVisualItems()', 'registerVisualItemsView(', 'unregisterVisualItemsView(',
    'flushDeferredVisualItems(', 'flushShownVisualItems(', 'addSessionItems(', '_migrateInternalSuffixToExternal(', 'streamBlockStart(',
    'streamBlockDelta(', '_onBufferDrain(', 'streamBlockStop(', 'streamBlockEnd(', '_findStreamingReplacement(', '_retireStreamingBlocks(',
    '_dropOrphanedStreamingBlocks(', 'toggleEventSegment(', 'toggleExpandedGroup(', 'expandGroup(', 'collapseAllGroups(',
    'toggleInternalExpandedGroup(', 'toggleSessionDebug(']
const actionSource = ACTIONS.map(name => section(`        ${name}`, '\n        /**')).join('\n')

// The streaming buffers of one store (utils/streamingBuffer.js), draining every delta at once.
function instantBuffers() {
    const buffers = new Map()
    const key = (sessionId, blockIndex) => `${sessionId}:${blockIndex}`
    return {
        initBuffer(sessionId, blockIndex, onDrain, { messageId, publicationIdentity }) {
            buffers.set(key(sessionId, blockIndex), { onDrain, messageId, publicationIdentity, text: '' })
        },
        feedDelta(sessionId, blockIndex, text) {
            const buffer = buffers.get(key(sessionId, blockIndex))
            if (!buffer) return
            buffer.text += text
            buffer.onDrain(buffer.text)
        },
        flushBuffer(sessionId, blockIndex) {
            const buffer = buffers.get(key(sessionId, blockIndex))
            buffers.delete(key(sessionId, blockIndex))
            return buffer?.text ?? null
        },
        destroySessionBuffers(sessionId) {
            for (const name of [...buffers.keys()]) if (name.startsWith(`${sessionId}:`)) buffers.delete(name)
        },
        isBufferActive(sessionId, messageId, blockIndex, publicationIdentity) {
            const buffer = buffers.get(key(sessionId, blockIndex))
            return !!buffer && buffer.messageId === messageId && (!publicationIdentity || buffer.publicationIdentity === publicationIdentity)
        },
    }
}

function makeStore({ body = actionSource, document = null, onSkip = null } = {}) {
    const settings = { getDisplayMode: DISPLAY_MODE.NORMAL, areMessageTimestampsShown: true }
    const views = createVisualItemsViews({ document })
    if (onSkip) {
        // A deferred recompute either returns at once (stale, never cleared) or runs the
        // event projection first (cleared, then stale again).
        const { defers, clearStale, markStale } = views
        let projected = false
        Object.assign(views, {
            defers(sessionId) { projected = false; return defers(sessionId) },
            clearStale(sessionId) { projected = true; return clearStale(sessionId) },
            markStale(sessionId) { onSkip(!projected); return markStale(sessionId) },
        })
    }
    const deps = { placeSessionEvents, projectEventSegments, setEventSegmentExpanded, mergeSessionEventRows, insertEventDaySeparators,
        jsonValuesEqual, useSettingsStore: () => settings, DISPLAY_LEVEL, DISPLAY_MODE, PROCESS_STATE, SYNTHETIC_ITEM, computeVisualItems,
        visualItemEqual, insertDaySeparators, makeBackgroundWorkStatusItem, markLiveTimestampAnchor, orderCompactItems, getParsedContent,
        setParsedContent, hasContent, getPrefixSuffixBoundaries, buildBackgroundWorkStatusLines, toRaw, normalizeChatTimestamp,
        isLaunchedEphemeral: () => false, getProviderHelpers: () => ({ matchesStreamingBlock }), createStreamPublicationIdentity,
        // Each streamed row drains its text at once (an active buffer that keeps up): the row shows the whole text.
        ...instantBuffers(),
        clearBlockInactivityTimer() {}, STREAM_BLOCK_INACTIVITY_MS: 2000, setTimeout: () => 0,
        visualItemsViews: views }
    const actions = new Function(...Object.keys(deps),
        `const debugAppendSafeSessions = new Set()\n${helperSource}\nreturn { ${body} }`)(...Object.values(deps))
    const store = defineStore(`deferral-${++storeId}`, {
        state: () => ({ sessions: { main: { id: 'main', provider: 'claude_code' } }, sessionItems: { main: [] }, processStates: {},
            localState: { sessionEventSegments: {}, sessionEventRetired: {}, sessionInternalExpandedGroups: {}, failedSends: {},
                optimisticMessages: {}, sessionDebugOverride: {}, sessionExpandedGroups: {}, sessionDetailedBlocks: {}, streamingBlocks: {},
                sessionVisualItems: {}, visualItemCache: {}, openDetails: {}, sessionEventHistory: { main: { snapshot: { events: [], sessions: {} } } } } }),
        actions: { ...actions, getSession(id) { return this.sessions[id] }, resolveInflightSends() {}, ensureSessionItemsCoverage: async () => true,
            setDetailOpen(id, key, open) { (this.localState.openDetails[id] ??= {})[key] = open },
            isDetailOpen(id, key) { return !!this.localState.openDetails[id]?.[key] } },
    })(createPinia())
    return { store, settings, views }
}
let storeId = 0

// What the list renders, and the state its rows read beside the rows (detail and internal
// group toggles), plus the segment state later projections inherit from.
function rendered(store) {
    const rows = (store.localState.sessionVisualItems.main || []).map(row => JSON.stringify([row.lineNum, row.syntheticKind,
        row.workingStatusKey, row.isGroupHead, row.isExpanded, row.groupSize, row.detailToggleFor, row.prefixGroupHead,
        row.prefixExpanded, row.suffixGroupHead, row.suffixExpanded, row.suffixGroupSize, row.segmentRange, row.prefixSegmentRange,
        row.suffixSegmentRange, row.eventInternalControls, row.isBlockStart, row.isBlockEnd, row.isLiveTimestampAnchor,
        row.isDaySeparator ? row.dayKey : null, row.timestamp, getParsedContent(row)]))
    const segments = (store.localState.sessionEventSegments.main || []).map(segment => JSON.stringify(
        [segment.key, segment.members, segment.expanded, segment.internal]))
    return { rows, segments, openDetails: JSON.stringify(store.localState.openDetails.main ?? {}),
        internal: JSON.stringify(store.localState.sessionInternalExpandedGroups.main ?? {}),
        expanded: JSON.stringify(store.localState.sessionExpandedGroups.main ?? []) }
}

function random(seed) {
    let state = seed
    return () => (state = (state * 16807) % 2147483647) / 2147483647
}
const T0 = Date.parse('2026-10-29T08:00:00Z')
const pick = (r, list) => list[Math.floor(r() * list.length)]

// A Claude-shaped live session: user messages, assistant messages whose tools form a prefix,
// a suffix or an internal group, tool-only messages in collapsible groups (their tails grow
// with metadata updates), DEBUG_ONLY lines, streamed thinking and text blocks retired by their
// persisted lines (before or after their end), and events placed anywhere in time.
function createLiveModel(r) {
    let line = 0, time = T0, open = null, suffixHead = null, messageSeq = 0, stream = null, eventSeq = 0
    let events = []
    const stamp = () => {
        if (r() < 0.08) return null
        time += r() < 0.1 ? -20_000 : Math.floor(r() * 40_000)
        return new Date(time).toISOString()
    }
    const assistant = (content, extra = {}) => JSON.stringify({ type: 'assistant', uuid: extra.uuid ?? `u${line}`,
        message: { id: extra.id ?? `m${line}`, role: 'assistant', content } })
    // A collapsible line joins the open group (or the suffix of the last assistant message).
    function collapsible(fields, updates) {
        const item = { line_num: ++line, display_level: DISPLAY_LEVEL.COLLAPSIBLE, timestamp: stamp(), ...fields }
        if (!open) {
            open = { head: suffixHead ?? item.line_num, members: suffixHead ? [suffixHead] : [] }
            suffixHead = null
        }
        open.members.push(item.line_num)
        item.group_head = open.head
        item.group_tail = item.line_num
        for (const member of open.members.slice(0, -1)) {
            const old = model.items.get(member)
            updates.push({ line_num: member, display_level: old.display_level, group_head: old.group_head,
                group_tail: item.line_num, kind: old.kind })
            old.group_tail = item.line_num
        }
        return item
    }
    function always(fields) {
        const item = { line_num: ++line, display_level: DISPLAY_LEVEL.ALWAYS, timestamp: stamp(), group_head: null, group_tail: null, ...fields }
        open = null
        suffixHead = null
        return item
    }
    function nextLine(updates) {
        const roll = r()
        if (roll < 0.36) {
            return { line_num: ++line, kind: 'system', display_level: DISPLAY_LEVEL.DEBUG_ONLY, group_head: null, group_tail: null,
                timestamp: r() < 0.5 ? null : stamp(), content: '{}' }
        }
        if (roll < 0.62) return collapsible({ kind: 'assistant_message', content: assistant([{ type: 'tool_use', id: `t${line + 1}`, name: 'Bash' }]) }, updates)
        if (roll < 0.86) {
            const prefixHead = open && r() < 0.6 ? open.head : null
            const parts = []
            if (prefixHead !== null) parts.push({ type: 'tool_use', id: `p${line + 1}`, name: 'Read' })
            parts.push({ type: 'text', text: `Text ${line + 1}` })
            if (r() < 0.3) parts.push({ type: 'tool_use', id: `i${line + 1}`, name: 'Grep' }, { type: 'text', text: 'more' })
            const suffix = r() < 0.4
            if (suffix) parts.push({ type: 'tool_use', id: `s${line + 1}`, name: 'Edit' })
            const item = always({ kind: 'assistant_message', group_head: prefixHead, content: assistant(parts) })
            if (suffix) suffixHead = item.line_num
            return item
        }
        return always({ kind: 'user_message', content: JSON.stringify({ type: 'user', message: { content: `Ask ${line + 1}` } }) })
    }
    const model = {
        items: new Map(),
        events: () => events,
        lastLine: () => line,
        /** The next live operation, applied the same way to both stores. */
        next(reference) {
            const roll = r()
            if (stream && roll < 0.3) return model.streamStep()
            if (!stream && roll < 0.12) {
                const id = `stream${++messageSeq}`
                stream = { id, blocks: r() < 0.7 ? ['thinking'] : r() < 0.5 ? ['text'] : ['thinking', 'text'], started: 0, ended: 0, persisted: 0 }
                return model.streamStep()
            }
            if (roll < 0.62) {
                const updates = [], lines = []
                for (let n = 1 + Math.floor(r() * 3); n > 0; n--) {
                    const item = nextLine(updates)
                    model.items.set(item.line_num, item)
                    lines.push(item)
                }
                return { kind: 'append', lines, updates }
            }
            if (roll < 0.72) {
                if (events.length && r() < 0.3) events = events.filter((_, i) => i !== Math.floor(r() * events.length))
                else events = [...events, { id: ++eventSeq, kind: 'title.changed', session_id: 'main', data: {},
                    occurred_at: new Date(T0 + Math.floor(r() * (time - T0 + 120_000))).toISOString() }]
                return { kind: 'snapshot', events }
            }
            if (roll < 0.8) return { kind: 'state', state: pick(r, [PROCESS_STATE.STARTING, PROCESS_STATE.ASSISTANT_TURN, PROCESS_STATE.ASSISTANT_TURN, PROCESS_STATE.USER_TURN]) }
            if (roll < 0.9) {
                // A user toggle, on what the always-shown list shows.
                const segments = reference.localState.sessionEventSegments.main || []
                const heads = [...new Set((reference.localState.sessionVisualItems.main || []).filter(row => row.isGroupHead || row.suffixGroupHead != null)
                    .map(row => row.isGroupHead ? row.lineNum : row.suffixGroupHead))]
                const internals = segments.filter(segment => segment.internal)
                // Opening a streamed row's segment is what its retirement carries over.
                const streamed = segments.filter(segment => segment.members.some(line => line < 0))
                const what = r()
                if (what < 0.3 && streamed.length) return { kind: 'toggleSegment', key: pick(r, streamed).key }
                if (what < 0.45 && segments.length) return { kind: 'toggleSegment', key: pick(r, segments).key }
                if (what < 0.8 && heads.length) return { kind: 'toggleGroup', head: pick(r, heads) }
                if (what < 0.9 && internals.length) {
                    const owner = model.items.get(internals[0].head)
                    const content = owner && getParsedContent({ content: owner.content })?.message?.content
                    if (Array.isArray(content)) return { kind: 'toggleInternal', line: owner.line_num, start: content.findIndex(part => part.type !== 'text') }
                }
                return { kind: 'expandAll', heads }
            }
            if (r() < 0.25) return { kind: 'debug' }
            return { kind: 'mode', mode: pick(r, [DISPLAY_MODE.CONVERSATION, DISPLAY_MODE.SIMPLIFIED, DISPLAY_MODE.NORMAL, DISPLAY_MODE.NORMAL,
                DISPLAY_MODE.DEBUG]) }
        },
        // Start, feed, stop, end and persist the blocks of the current streamed message.
        streamStep() {
            const index = stream.started
            if (stream.started < stream.blocks.length && (stream.started === stream.ended || r() < 0.3)) {
                stream.started++
                return { kind: 'streamStart', id: stream.id, index, type: stream.blocks[index] }
            }
            const open = stream.ended
            const roll = r()
            if (open < stream.started && roll < 0.4) return { kind: 'streamDelta', id: stream.id, index: open, text: `d${line} ` }
            if (open < stream.started && roll < 0.55) return { kind: 'streamStop', id: stream.id, index: open }
            const uuid = `${stream.id}-${open}`
            const persist = () => {
                const updates = []
                const type = stream.blocks[stream.persisted]
                // A thinking block lands alone (collapsible), or inside a message's internal group.
                const inside = type === 'thinking' && r() < 0.4
                const thinking = { type: 'thinking', thinking: 'reasoned' }
                const content = assistant(type === 'text' ? [{ type: 'text', text: 'answer' }]
                    : inside ? [{ type: 'text', text: 'before' }, thinking, { type: 'text', text: 'after' }] : [thinking],
                { id: stream.id, uuid: `${stream.id}-${stream.persisted}` })
                const item = type === 'thinking' && !inside ? collapsible({ kind: 'assistant_message', content }, updates)
                    : always({ kind: 'assistant_message', content })
                model.items.set(item.line_num, item)
                stream.persisted++
                return { kind: 'append', lines: [item], updates }
            }
            // The persisted line lands before or after the block's end.
            if (stream.persisted < stream.ended || (stream.persisted === open && open < stream.started && roll < 0.75)) {
                const op = persist()
                if (stream.persisted === stream.blocks.length && stream.ended === stream.blocks.length) stream = null
                return op
            }
            if (open < stream.started) {
                stream.ended++
                const op = { kind: 'streamEnd', id: stream.id, index: open, uuid }
                if (stream.persisted === stream.blocks.length && stream.ended === stream.blocks.length) stream = null
                return op
            }
            // Every started block ended: persist the rest, or cut the turn (orphans).
            if (stream.persisted < stream.ended) return persist()
            stream = null
            return { kind: 'state', state: PROCESS_STATE.USER_TURN }
        },
    }
    return model
}

const copy = value => JSON.parse(JSON.stringify(value))
function apply({ store, settings }, op) {
    switch (op.kind) {
        case 'append': return store.addSessionItems('main', copy(op.lines), copy(op.updates))
        case 'snapshot':
            store.localState.sessionEventHistory.main.snapshot = { events: copy(op.events), sessions: {} }
            return store.recomputeVisualItems('main')
        case 'state': {
            const was = store.processStates.main?.state
            store.processStates.main = { state: op.state, label: null, tools: [] }
            if (was === PROCESS_STATE.ASSISTANT_TURN && op.state !== PROCESS_STATE.ASSISTANT_TURN) store._dropOrphanedStreamingBlocks('main')
            return store.recomputeVisualItems('main')
        }
        case 'streamStart': return store.streamBlockStart('main', op.id, op.index, op.type)
        case 'streamDelta': return store.streamBlockDelta('main', op.id, op.index, op.text)
        case 'streamStop': return store.streamBlockStop('main', op.id, op.index)
        case 'streamEnd': return store.streamBlockEnd('main', op.id, op.index, op.uuid)
        case 'toggleSegment': return store.toggleEventSegment('main', op.key)
        case 'toggleGroup': return store.toggleExpandedGroup('main', op.head)
        case 'toggleInternal': return store.toggleInternalExpandedGroup('main', op.line, op.start)
        case 'expandAll':
            for (const head of op.heads) store.expandGroup('main', head)
            return store.recomputeVisualItems('main')
        case 'mode':
            settings.getDisplayMode = op.mode
            return store.recomputeAllVisualItems()
        case 'debug': return store.toggleSessionDebug('main')
    }
    throw new Error(`unknown op ${op.kind}`)
}

/**
 * Run `seeds` random sequences. The deferred store's view hides and shows, and its page hides
 * and shows (`pageHides`); both stores render the same thing whenever that view shows the
 * session on a visible page. Returns how often the deferred store skipped work.
 */
function runEquivalence({ seeds, steps = 70, pageHides = true, body = actionSource }) {
    const counts = { skipped: 0, projected: 0, compared: 0, notified: 0, transfers: 0 }
    for (let seed = 1; seed <= seeds; seed++) {
        const r = random(seed)
        const page = { hidden: false, listeners: [], addEventListener(type, listener) { this.listeners.push(listener) } }
        const deferred = makeStore({ body, document: page, onSkip: skip => { counts[skip ? 'skipped' : 'projected']++ } })
        const reference = makeStore()
        const view = { onDeferredRecompute: () => counts.notified++ }, referenceView = {}
        deferred.store.registerVisualItemsView('main', view)
        reference.store.registerVisualItemsView('main', referenceView)
        let shown = true
        const model = createLiveModel(r)
        const check = label => {
            counts.compared++
            assert.deepEqual(rendered(deferred.store), rendered(reference.store), `seed ${seed}, ${label}`)
        }
        for (let step = 0; step < steps; step++) {
            const roll = r()
            if (roll < 0.08) {
                if (shown) deferred.store.unregisterVisualItemsView('main', view)
                else deferred.store.registerVisualItemsView('main', view)
                shown = !shown
                if (shown && !page.hidden) check(`step ${step}, view shown again`)
                continue
            }
            if (pageHides && roll < 0.13) {
                page.hidden = !page.hidden
                for (const listener of page.listeners) listener()
                if (shown && !page.hidden) check(`step ${step}, page shown again`)
                continue
            }
            const op = model.next(reference.store)
            const internalBefore = JSON.stringify(reference.store.localState.sessionInternalExpandedGroups.main ?? {})
            apply(deferred, op)
            apply(reference, op)
            if (op.kind !== 'toggleInternal' && JSON.stringify(reference.store.localState.sessionInternalExpandedGroups.main ?? {}) !== internalBefore) {
                counts.transfers++
            }
            if (shown && !page.hidden) check(`step ${step}, ${op.kind}`)
        }
        if (!shown) deferred.store.registerVisualItemsView('main', view)
        if (page.hidden) {
            page.hidden = false
            for (const listener of page.listeners) listener()
        }
        check('end')
    }
    return counts
}

test('a session shown again renders what an always-shown session renders', () => {
    const counts = runEquivalence({ seeds: 120 })
    // The sequences must exercise both deferrals: a whole skip, and the projection-only run.
    assert.ok(counts.skipped > 300, `whole recomputes skipped: ${counts.skipped}`)
    assert.ok(counts.projected > 1000, `projection-only recomputes: ${counts.projected}`)
    assert.ok(counts.notified > 40, `views told after the page showed again: ${counts.notified}`)
    // Retired thinking blocks that open an internal group of their persisted message.
    assert.ok(counts.transfers > 5, `internal transfers: ${counts.transfers}`)
})

test('a hidden page defers the shown session and catches up when it shows again', () => {
    const page = { hidden: false, listeners: [], addEventListener(type, listener) { this.listeners.push(listener) } }
    let skips = 0
    const { store } = makeStore({ document: page, onSkip: () => { skips++ } })
    const notified = []
    const view = { onDeferredRecompute: () => notified.push('view') }
    store.registerVisualItemsView('main', view)
    const user = { line_num: 1, kind: 'user_message', display_level: DISPLAY_LEVEL.ALWAYS, group_head: null, group_tail: null,
        timestamp: new Date(T0).toISOString(), content: JSON.stringify({ type: 'user', message: { content: 'Hi' } }) }
    store.addSessionItems('main', [user])
    assert.deepEqual(store.localState.sessionVisualItems.main.map(row => row.lineNum), [1])

    page.hidden = true
    for (const listener of page.listeners) listener()
    store.addSessionItems('main', [{ ...user, line_num: 2, timestamp: new Date(T0 + 1000).toISOString() }])
    assert.equal(skips, 1, 'the shown session waits while the page is hidden')
    assert.deepEqual(store.localState.sessionVisualItems.main.map(row => row.lineNum), [1])
    assert.deepEqual(notified, [])

    page.hidden = false
    for (const listener of page.listeners) listener()
    assert.deepEqual(store.localState.sessionVisualItems.main.map(row => row.lineNum), [1, 2], 'the page shows again: rows catch up')
    assert.deepEqual(notified, ['view'], 'and the view is told')
    store.addSessionItems('main', [{ ...user, line_num: 3, timestamp: new Date(T0 + 2000).toISOString() }])
    assert.deepEqual(store.localState.sessionVisualItems.main.map(row => row.lineNum), [1, 2, 3], 'a visible page recomputes at once')
})

test('only a session a view showed waits; a view shown again catches up before it reads its rows', () => {
    const { store } = makeStore()
    const user = line => ({ line_num: line, kind: 'user_message', display_level: DISPLAY_LEVEL.ALWAYS, group_head: null, group_tail: null,
        timestamp: new Date(T0 + line * 1000).toISOString(), content: JSON.stringify({ type: 'user', message: { content: `Hi ${line}` } }) })
    const lines = () => store.localState.sessionVisualItems.main.map(row => row.lineNum)
    store.addSessionItems('main', [user(1)])
    store.addSessionItems('main', [user(2)])
    assert.deepEqual(lines(), [1, 2], 'a session no view ever showed recomputes as before')

    const first = {}, second = {}
    assert.equal(store.registerVisualItemsView('main', first), false, 'nothing to catch up')
    store.registerVisualItemsView('main', second)
    store.unregisterVisualItemsView('main', first)
    store.addSessionItems('main', [user(3)])
    assert.deepEqual(lines(), [1, 2, 3], 'one view still shows it')

    store.unregisterVisualItemsView('main', second)
    store.addSessionItems('main', [user(4)])
    assert.deepEqual(lines(), [1, 2, 3], 'no view shows it: the rows wait')
    assert.equal(store.registerVisualItemsView('main', first), true, 'the view shown again catches up')
    assert.deepEqual(lines(), [1, 2, 3, 4])
    assert.equal(store.flushDeferredVisualItems('main'), false)

    store.unregisterVisualItemsView('main', first)
    store.addSessionItems('main', [user(5)])
    assert.equal(store.flushDeferredVisualItems('main'), true, 'a cached view about to show catches up without registering')
    assert.deepEqual(lines(), [1, 2, 3, 4, 5])

    // Rows dropped (an unload, a retry) are rebuilt at once: callers test that they exist.
    delete store.localState.sessionVisualItems.main
    store.addSessionItems('main', [user(6)])
    assert.deepEqual(lines(), [1, 2, 3, 4, 5, 6])
})

for (const landing of ['own group', 'internal group']) test(`a stream that starts and retires off screen carries its expanded state as on screen (${landing})`, () => {
    // The user opened the tool group (an event cuts the list, so the state lives in its
    // segment). Off screen, a thinking block streams into that group, and its persisted line
    // lands in a group of its own, or inside an internal group of an assistant message: the
    // retirement carries the open state over, reading the segment of the streamed line, which
    // only a projection run after the stream started has.
    const at = second => new Date(T0 + second * 1000).toISOString()
    const tool = line => ({ line_num: line, kind: 'assistant_message', display_level: DISPLAY_LEVEL.COLLAPSIBLE, group_head: 2, group_tail: 3,
        timestamp: at(line), content: JSON.stringify({ type: 'assistant', uuid: `u${line}`, message: { id: `m${line}`, content: [{ type: 'tool_use', id: `t${line}` }] } }) })
    const lines = [{ line_num: 1, kind: 'user_message', display_level: DISPLAY_LEVEL.ALWAYS, group_head: null, group_tail: null, timestamp: at(1),
        content: JSON.stringify({ type: 'user', message: { content: 'Go' } }) }, tool(2), tool(3)]
    const thinking = { type: 'thinking', thinking: 'Reasoned' }
    const persisted = landing === 'own group'
        ? { line_num: 4, kind: 'assistant_message', display_level: DISPLAY_LEVEL.COLLAPSIBLE, group_head: 4, group_tail: 4, timestamp: at(4),
            content: JSON.stringify({ type: 'assistant', uuid: 'uA', message: { id: 'msgA', content: [thinking] } }) }
        : { line_num: 4, kind: 'assistant_message', display_level: DISPLAY_LEVEL.ALWAYS, group_head: null, group_tail: null, timestamp: at(4),
            content: JSON.stringify({ type: 'assistant', uuid: 'uA', message: { id: 'msgA',
                content: [{ type: 'text', text: 'Before' }, thinking, { type: 'text', text: 'After' }] } }) }
    const run = shownAll => {
        const { store } = makeStore()
        const view = {}
        store.registerVisualItemsView('main', view)
        store.localState.sessionEventHistory.main.snapshot = { events: [{ id: 1, kind: 'title.changed', session_id: 'main', data: {},
            occurred_at: new Date(T0 + 1500).toISOString() }], sessions: {} }
        store.addSessionItems('main', copy(lines))
        store.toggleEventSegment('main', 'tools:2:2')
        if (!shownAll) store.unregisterVisualItemsView('main', view)
        store.processStates.main = { state: PROCESS_STATE.ASSISTANT_TURN, label: null, tools: [] }
        store.recomputeVisualItems('main')
        store.streamBlockStart('main', 'msgA', 0, 'thinking')
        store.streamBlockDelta('main', 'msgA', 0, 'Reasoned')
        store.streamBlockStop('main', 'msgA', 0)
        store.streamBlockEnd('main', 'msgA', 0, 'uA')
        store.addSessionItems('main', [copy(persisted)])
        if (!shownAll) store.registerVisualItemsView('main', view)
        return rendered(store)
    }
    const shown = run(true)
    if (landing === 'own group') assert.ok(shown.segments.includes('["tools:4:4",[4],true,false]'), `the persisted line opens: ${shown.segments}`)
    else assert.equal(shown.internal, '{"4":[1]}', 'the internal group holding the thinking opens')
    assert.deepEqual(run(false), shown)
})


test('locale refresh updates loaded day labels and preserves deferred rows until the view returns', async context => {
    context.after(() => { i18n.global.locale.value = 'en' })
    i18n.global.locale.value = 'en'
    const { store } = makeStore()
    const view = {}
    store.registerVisualItemsView('main', view)
    const row = (line, kind, timestamp) => ({ line_num: line, kind, display_level: DISPLAY_LEVEL.ALWAYS,
        group_head: null, group_tail: null, timestamp,
        content: JSON.stringify({ type: 'user', message: { content: 'Unchanged original source' } }) })
    store.addSessionItems('main', [row(1, 'user_message', '2020-01-02T12:00:00Z'),
        row(2, 'assistant_message', '2020-01-03T12:00:00Z')])
    const original = store.localState.sessionVisualItems.main
    const label = original.find(row => row.isDaySeparator).dayLabel
    store.unregisterVisualItemsView('main', view)
    store.sessionItems.otherLoaded = []
    store.localState.sessionEventHistory.eventOnly = { snapshot: null }
    store.localState.optimisticMessages.optimisticOnly = {}
    const calls = [], recompute = store.recomputeVisualItems
    store.recomputeVisualItems = id => { calls.push(id); if (id === 'main') recompute(id) }
    store.refreshLocalizedSyntheticContent = () => {}
    const localeStart = source.indexOf('watch(i18n.global.locale, () => {')
    const localeEnd = source.indexOf('\n    })', localeStart) + '\n    })'.length
    const stop = new Function('watch', 'i18n', 'store', 'return ' + source.slice(localeStart, localeEnd))(watch, i18n, store)
    context.after(stop)
    i18n.global.locale.value = 'pt-BR'
    await nextTick()
    assert.deepEqual(new Set(calls), new Set(['main', 'otherLoaded', 'eventOnly', 'optimisticOnly']))
    assert.equal(store.localState.sessionVisualItems.main, original, 'hidden session preserves its deferred rows')
    store.registerVisualItemsView('main', view)
    assert.notEqual(store.localState.sessionVisualItems.main.find(row => row.isDaySeparator).dayLabel, label)
    assert.equal(store.sessionItems.main[0].content, row(1, 'user_message', '2020-01-02T12:00:00Z').content)
})
