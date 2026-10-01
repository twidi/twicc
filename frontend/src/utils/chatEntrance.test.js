// Run with: node --test src/utils/chatEntrance.test.js (from the frontend dir)
// Live chat entrances (visual refresh step 5a, docs/plans/2026-09-28-chat-entrances-skeleton-design.md §4):
// which rows of a new visual-item list enter.
import test from 'node:test'
import assert from 'node:assert/strict'

import {
    CHAT_ENTRANCE_MAX_BATCH,
    CHAT_ENTRANCE_STAGGER_MS,
    STREAMED_KINDS,
    isInTurnState,
    keyLineNum,
    planChatEntrances,
    shouldNoteViewChange,
} from './chatEntrance.js'

const getKey = (item) => item.lineNum

/** A real or synthetic row; a whole card by default. */
function row(lineNum, extra = {}) {
    return { lineNum, kind: 'assistant_message', isBlockStart: true, isBlockEnd: true, ...extra }
}
const user = (lineNum, extra = {}) => row(lineNum, { kind: 'user_message', ...extra })
const tool = (lineNum, extra = {}) => row(lineNum, { kind: 'tool_use', ...extra })
const daysep = (lineNum) => ({ lineNum: `daysep-${lineNum}`, isDaySeparator: true })
const streamed = (index) => row(-1000 - index, { kind: undefined, syntheticKind: 'streaming-block' })
const working = () => row(-500, { kind: undefined, syntheticKind: 'working-assistant-message' })
const starting = () => row(-1500, { kind: undefined, syntheticKind: 'starting-assistant-message' })
const optimistic = () => user(-2000, { kind: undefined, syntheticKind: 'optimistic-user-message' })
const failed = (seq = 0) => user(-3000 - seq, { kind: undefined, syntheticKind: 'failed-user-message' })

/** Plan from a previous list to a new one; `live` / `retired` are line numbers. */
function plan(previous, items, options = {}) {
    const previousStreamingKeys = new Set(previous.filter((i) => i.syntheticKind === 'streaming-block').map(getKey))
    return planChatEntrances({
        previousKeys: new Set(previous.map(getKey)),
        items,
        getKey,
        liveLineNums: new Set(options.live ?? []),
        retiredRealLineNums: new Set(options.retired ?? []),
        previousStreamingKeys,
        revealed: options.revealed ?? true,
        viewChanged: options.viewChanged ?? false,
        hasToolBlock: options.hasToolBlock ?? (() => false),
    })
}
const enteringKeys = (result) => result.entering.map((e) => e.key)

test('constants', () => {
    assert.equal(CHAT_ENTRANCE_STAGGER_MS, 70)
    assert.equal(CHAT_ENTRANCE_MAX_BATCH, 6)
    assert.deepEqual([...STREAMED_KINDS].sort(), ['assistant_message', 'content_items', 'reasoning'])
})

test('keyLineNum: number, daysep-N, other strings', () => {
    assert.equal(keyLineNum(12), 12)
    assert.equal(keyLineNum(-500), -500)
    assert.equal(keyLineNum('daysep-12'), 12)
    assert.ok(Number.isNaN(keyLineNum('foo')))
    assert.ok(Number.isNaN(keyLineNum('12')))
    assert.ok(Number.isNaN(keyLineNum(undefined)))
})

test('a key whose keyLineNum is NaN is never a candidate', () => {
    const result = plan([row(1)], [row(1), { lineNum: 'other' }], { live: [1] })
    assert.deepEqual(result.entering, [])
})

test('not revealed, or a view change: nothing enters', () => {
    const before = [row(1)]
    const after = [row(1), row(2), working()]
    assert.deepEqual(plan(before, after, { live: [2], revealed: false }).entering, [])
    assert.deepEqual(plan(before, after, { live: [2], viewChanged: true }).entering, [])
})

test('a new live line enters; a new non-live line and a key present before do not', () => {
    assert.deepEqual(enteringKeys(plan([row(1)], [row(1), row(2)], { live: [2] })), [2])
    // First load, lazy fill, heal, view change revealing an older or hidden live line.
    assert.deepEqual(plan([row(1)], [row(1), row(2)]).entering, [])
    assert.deepEqual(plan([], [row(1), row(2)]).entering, [])
    // Live, but already there.
    assert.deepEqual(plan([row(1), row(2)], [row(1), row(2)], { live: [2] }).entering, [])
})

test('a live line absent at its call, added later with no live lines, does not enter', () => {
    const first = plan([row(1)], [row(1)], { live: [2] })
    assert.deepEqual(first.entering, [])
    const second = plan([row(1)], [row(1), row(2)], { live: [] })
    assert.deepEqual(second.entering, [])
})

test('a retired real line does not enter; the other live lines of the call do', () => {
    const result = plan([row(1), streamed(0)], [row(1), row(2), tool(3)], { live: [2, 3], retired: [2] })
    assert.deepEqual(enteringKeys(result), [3])
})

test('a streamed block still on screen: text-like real rows do not enter, tool rows do', () => {
    const toolLines = new Set([3])
    const before = [row(1), streamed(0)]
    const after = [
        row(1),
        row(2, { kind: 'assistant_message' }),
        row(3, { kind: 'content_items' }),   // a Claude tool_use / tool_result line
        row(4, { kind: 'content_items' }),   // a Claude thinking-only line
        row(5, { kind: 'reasoning' }),
        tool(6),                             // Codex
        streamed(0),
    ]
    const result = plan(before, after, { live: [2, 3, 4, 5, 6], hasToolBlock: (item) => toolLines.has(item.lineNum) })
    assert.deepEqual(enteringKeys(result), [3, 6])
})

test('a streamed block gone from the list: the twin rule does not apply', () => {
    const result = plan([row(1), streamed(0)], [row(1), row(2)], { live: [2] })
    assert.deepEqual(enteringKeys(result), [2])
})

test('the starting message is not a streamed block', () => {
    const result = plan([row(1), starting()], [row(1), row(2), starting()], { live: [2] })
    assert.deepEqual(enteringKeys(result), [2])
})

test('the working bubble turning into streamed text: nothing enters', () => {
    assert.deepEqual(plan([row(1), working()], [row(1), streamed(0)]).entering, [])
    assert.deepEqual(plan([row(1), starting()], [row(1), streamed(0), working()]).entering, [])
})

test('optimistic message replaced by the real one: neither it nor its separator enters; a tool row does', () => {
    const result = plan([row(1), optimistic()], [row(1), daysep(2), user(2), tool(3)], { live: [2, 3] })
    assert.deepEqual(enteringKeys(result), [3])
})

test('failed bubble replaced by the real message: it does not enter', () => {
    assert.deepEqual(plan([row(1), failed()], [row(1), user(2)], { live: [2] }).entering, [])
})

test('optimistic ↔ failed swaps: nothing enters', () => {
    assert.deepEqual(plan([row(1), optimistic()], [row(1), failed()]).entering, [])
    assert.deepEqual(plan([row(1), failed()], [row(1), optimistic()]).entering, [])
})

test('starting → working: nothing enters; working alone enters', () => {
    assert.deepEqual(plan([row(1), starting()], [row(1), working()]).entering, [])
    assert.deepEqual(enteringKeys(plan([row(1)], [row(1), working()])), [-500])
})

test('variants and indexes', () => {
    const before = [row(1, { isBlockEnd: true }), row(10, { isBlockStart: true, isBlockEnd: false })]
    const after = [
        row(1),
        user(2),                                                     // user
        daysep(3),
        row(3),                                                      // whole card
        row(4, { isBlockStart: true, isBlockEnd: false }),           // new block start, the block goes on
        row(10, { isBlockStart: false, isBlockEnd: false }),         // existing row of the same block
        row(11, { isBlockStart: false, isBlockEnd: false }),         // inner row
        row(12, { isBlockStart: false, isBlockEnd: true }),
    ]
    const result = plan(before, after, { live: [2, 3, 4, 11] })
    assert.deepEqual(result.entering, [
        { key: 2, index: 0, variant: 'user' },
        { key: 'daysep-3', index: 1, variant: 'card' },
        { key: 3, index: 2, variant: 'card' },
        { key: 4, index: 3, variant: 'slice' },
        { key: 11, index: 4, variant: 'slice' },
    ])
    // A separator takes the variant of its row.
    const userSep = plan([row(1)], [row(1), daysep(2), user(2)], { live: [2] })
    assert.deepEqual(userSep.entering.map((e) => e.variant), ['user', 'user'])
})

// Every arrival of tools (display level 2, `externallyGrouped`: tool calls, thinking...) follows the group
// reveal's animation (a curtain and a slide from outside the chat), whatever the display mode.
test('tool rows (collapsible level) enter as tools; an opened group\'s head enters as a tool head; a closed one fades', () => {
    const result = plan([row(1)], [
        row(1),
        row(2, { externallyGrouped: true }),
        row(3, { externallyGrouped: true, isGroupHead: true, isExpanded: true }),
        row(4, { externallyGrouped: true, isGroupHead: true, isExpanded: false, isBlockStart: false, isBlockEnd: false }),
        row(5),
    ], { live: [2, 3, 4, 5] })
    assert.deepEqual(result.entering.map((e) => [e.key, e.variant]), [[2, 'tool'], [3, 'tool-head'], [4, 'slice'], [5, 'card']])
})

test('more than the batch cap: nothing enters; exactly the cap: all enter', () => {
    const lines = [2, 3, 4, 5, 6, 7, 8]
    const seven = plan([row(1)], [row(1), ...lines.map((l) => tool(l))], { live: lines })
    assert.deepEqual(seven.entering, [])
    const six = plan([row(1)], [row(1), ...lines.slice(0, 6).map((l) => tool(l))], { live: lines })
    assert.deepEqual(six.entering.map((e) => e.index), [0, 1, 2, 3, 4, 5])
})

test('keys and streamingKeys are returned, even when nothing enters', () => {
    const items = [row(1), streamed(0), streamed(1), working()]
    const result = plan([], items, { revealed: false })
    assert.deepEqual([...result.keys], [1, -1000, -1001, -500])
    assert.deepEqual([...result.streamingKeys], [-1000, -1001])
})

test('isInTurnState', () => {
    assert.equal(isInTurnState('assistant_turn'), true)
    assert.equal(isInTurnState('starting'), true)
    assert.equal(isInTurnState('user_turn'), false)
    assert.equal(isInTurnState('dead'), false)
    assert.equal(isInTurnState(undefined), false)
})

test('shouldNoteViewChange', () => {
    const list = []
    assert.equal(shouldNoteViewChange({ listBefore: list, listAfter: list }), false)
    assert.equal(shouldNoteViewChange({ listBefore: list, listAfter: [] }), true)
    assert.equal(shouldNoteViewChange({ listBefore: list, listAfter: list, inTurnBefore: false, inTurnAfter: true }), true)
    // user_turn → no process: both false.
    assert.equal(shouldNoteViewChange({ listBefore: list, listAfter: list, inTurnBefore: false, inTurnAfter: false }), false)
})
