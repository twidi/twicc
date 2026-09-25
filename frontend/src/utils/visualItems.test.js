// Run with: node --test src/utils/visualItems.test.js (from the frontend dir)
import test from 'node:test'
import assert from 'node:assert/strict'

import { DISPLAY_LEVEL, DISPLAY_MODE, SYNTHETIC_ITEM } from '../constants.js'
import { getParsedContent } from './parsedContent.js'
import { computeVisualItems, makeBackgroundWorkStatusItem, visualItemEqual } from './visualItems.js'

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
