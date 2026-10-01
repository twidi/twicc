// Run with: node --test src/utils/visualItems.test.js (from the frontend dir)
import test from 'node:test'
import assert from 'node:assert/strict'

import { DISPLAY_LEVEL, DISPLAY_MODE, SYNTHETIC_ITEM } from '../constants.js'
import { getParsedContent } from './parsedContent.js'
import { computeVisualItems, makeBackgroundWorkStatusItem, markLiveTimestampAnchor, visualItemEqual } from './visualItems.js'

function makeItem(lineNum, displayLevel, groupHead = null) {
    return {
        line_num: lineNum,
        content: JSON.stringify({
            type: 'assistant',
            message: { content: [{ type: 'thinking', thinking: 'Working…' }] },
        }),
        kind: displayLevel === DISPLAY_LEVEL.ALWAYS ? 'assistant_message' : 'content_items',
        display_level: displayLevel,
        group_head: groupHead,
        group_tail: null,
    }
}

test('simplified visual items identify every externally grouped item', () => {
    const items = [
        makeItem(1, DISPLAY_LEVEL.COLLAPSIBLE, 1),
        makeItem(2, DISPLAY_LEVEL.COLLAPSIBLE, 1),
        makeItem(3, DISPLAY_LEVEL.ALWAYS),
    ]

    const visualItems = computeVisualItems(items, DISPLAY_MODE.SIMPLIFIED, [1])

    assert.deepEqual(
        visualItems.map(item => [item.lineNum, item.externallyGrouped]),
        [[1, true], [2, true], [3, false]],
    )
    assert.equal(visualItems[0].isGroupHead, true)
})

test('background work status item: none without lines', () => {
    assert.equal(makeBackgroundWorkStatusItem([], { kind: 'assistant_message' }), null)
    assert.equal(makeBackgroundWorkStatusItem(null, null), null)
})

test('background work status item: synthetic, last-row flags, lines in parsed content', () => {
    const lines = [{ kind: 'shells', text: '1 background shell still running' }]
    const item = makeBackgroundWorkStatusItem(lines, { kind: 'assistant_message', isBlockEnd: true })

    assert.equal(item.lineNum, SYNTHETIC_ITEM.BACKGROUND_WORK_STATUS.lineNum)
    assert.equal(item.syntheticKind, SYNTHETIC_ITEM.BACKGROUND_WORK_STATUS.kind)
    assert.equal(item.isBlockStart, false)
    assert.equal(item.isBlockEnd, true)
    assert.deepEqual(getParsedContent(item).lines, lines)

    // Right after a user message (or with nothing above), it opens its own card.
    assert.equal(makeBackgroundWorkStatusItem(lines, { kind: 'user_message' }).isBlockStart, true)
    assert.equal(makeBackgroundWorkStatusItem(lines, null).isBlockStart, true)
})

test('background work status item: the stabilizer sees a count change', () => {
    const one = [{ kind: 'shells', text: '1 background shell still running' }]
    const two = [{ kind: 'shells', text: '2 background shells still running' }]
    const previous = { kind: 'assistant_message' }

    assert.equal(visualItemEqual(makeBackgroundWorkStatusItem(one, previous), makeBackgroundWorkStatusItem(one, previous)), true)
    assert.equal(visualItemEqual(makeBackgroundWorkStatusItem(one, previous), makeBackgroundWorkStatusItem(two, previous)), false)
})

function vi(lineNum, kind = 'assistant_message', extra = {}) {
    return { lineNum, kind, isBlockStart: false, isBlockEnd: false, ...extra }
}

function anchors(items) {
    return items.filter(item => item.isLiveTimestampAnchor).map(item => item.lineNum)
}

test('live timestamp anchor: last real item before the working placeholder', () => {
    const working = SYNTHETIC_ITEM.WORKING_ASSISTANT_MESSAGE
    const items = [
        vi(1, 'user_message', { isBlockStart: true, isBlockEnd: true }),
        vi(2, 'assistant_message', { isBlockStart: true }),
        vi(3),
        vi(working.lineNum, 'assistant_message', { syntheticKind: working.kind, isBlockEnd: true }),
    ]
    markLiveTimestampAnchor(items)
    assert.deepEqual(anchors(items), [3])
})

test('live timestamp anchor: skips streaming blocks and collapsed group heads', () => {
    const streaming = SYNTHETIC_ITEM.STREAMING_BLOCK
    const items = [
        vi(2, 'assistant_message', { isBlockStart: true }),
        vi(3, 'content_items', { isGroupHead: true, isExpanded: false }),
        vi(streaming.baseLineNum, 'assistant_message', { syntheticKind: streaming.kind, isBlockEnd: true }),
    ]
    markLiveTimestampAnchor(items)
    assert.deepEqual(anchors(items), [2])
})

test('live timestamp anchor: an expanded group head can carry the time', () => {
    const working = SYNTHETIC_ITEM.WORKING_ASSISTANT_MESSAGE
    const items = [
        vi(2, 'assistant_message', { isBlockStart: true }),
        vi(3, 'content_items', { isGroupHead: true, isExpanded: true }),
        vi(working.lineNum, 'assistant_message', { syntheticKind: working.kind, isBlockEnd: true }),
    ]
    markLiveTimestampAnchor(items)
    assert.deepEqual(anchors(items), [3])
})

test('live timestamp anchor: never crosses the user message', () => {
    const starting = SYNTHETIC_ITEM.STARTING_ASSISTANT_MESSAGE
    const items = [
        vi(1, 'user_message', { isBlockStart: true, isBlockEnd: true }),
        vi(starting.lineNum, 'assistant_message', { syntheticKind: starting.kind, isBlockStart: true, isBlockEnd: true }),
    ]
    markLiveTimestampAnchor(items)
    assert.deepEqual(anchors(items), [])
})

test('live timestamp anchor: none when the block ends with a real item', () => {
    const items = [
        vi(2, 'assistant_message', { isBlockStart: true }),
        vi(3, 'assistant_message', { isBlockEnd: true }),
    ]
    markLiveTimestampAnchor(items)
    assert.deepEqual(anchors(items), [])
})

test('live timestamp anchor: none after an ephemeral result', () => {
    const result = SYNTHETIC_ITEM.EPHEMERAL_RESULT
    const items = [
        vi(2, 'assistant_message', { isBlockStart: true }),
        vi(result.lineNum, 'assistant_message', { syntheticKind: result.kind, isBlockEnd: true }),
    ]
    markLiveTimestampAnchor(items)
    assert.deepEqual(anchors(items), [])
})
