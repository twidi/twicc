import test from 'node:test'
import assert from 'node:assert/strict'
import { matchesStreamingBlock } from './streamMatching.js'

function text(id = 'item-A') {
    return { type: 'event_msg', payload: { type: 'item_completed', item: { type: 'AgentMessage', id } } }
}
function reasoning(id = 'item-A') {
    return { type: 'response_item', payload: { type: 'reasoning', id } }
}
const block = (blockType = 'text', uuid = null) => ({ blockIndex: 0, blockType, uuid })

test('codex matches persisted text before and after end', () => {
    for (const uuid of [null, 'item-A']) assert.equal(matchesStreamingBlock(text(), 'assistant_message', 'item-A', block('text', uuid)), true)
})
test('codex matches the visible reasoning row', () => {
    for (const uuid of [null, 'item-A']) assert.equal(matchesStreamingBlock(reasoning(), 'reasoning', 'item-A', block('thinking', uuid)), true)
    assert.equal(matchesStreamingBlock(reasoning(), 'reasoning', 'item-A', block()), false)
})
test('codex rejects canonical reasoning duplicates', () => {
    const parsed = text()
    parsed.payload.item.type = 'Reasoning'
    assert.equal(matchesStreamingBlock(parsed, 'reasoning', 'item-A', block('thinking')), false)
})
test('codex rejects inconsistent identity and type', () => {
    const cases = [
        [text(), 'reasoning', 'item-A', block()],
        [text(), 'content_items', 'item-A', block()],
        [text(), 'assistant_message', 'item-A', block('thinking')],
        [reasoning(), 'assistant_message', 'item-A', block('thinking')],
        [text(), 'assistant_message', 'item-B', block()],
        [text(), 'assistant_message', 'item-A', block('text', 'item-B')],
        [reasoning(), 'reasoning', 'item-A', block('thinking', 'item-B')],
        [{ ...text(), type: 'response_item' }, 'assistant_message', 'item-A', block()],
        [{ ...reasoning(), type: 'event_msg' }, 'reasoning', 'item-A', block('thinking')],
    ]
    for (const args of cases) assert.equal(matchesStreamingBlock(...args), false)
})
test('codex rejects malformed identifiers without throwing', () => {
    for (const id of [undefined, '', 7, [], {}]) {
        assert.equal(matchesStreamingBlock({ ...text(), payload: { ...text().payload, item: { ...text().payload.item, id } } }, 'assistant_message', 'item-A', block()), false)
        assert.equal(matchesStreamingBlock({ ...reasoning(), payload: { ...reasoning().payload, id } }, 'reasoning', 'item-A', block('thinking')), false)
        assert.equal(matchesStreamingBlock(text(), 'assistant_message', id, block()), false)
        assert.equal(matchesStreamingBlock(text(), 'assistant_message', 'item-A', { ...block(), uuid: id }), id === undefined)
    }
    for (const parsed of [null, {}, [], { type: 'event_msg', payload: null }]) {
        assert.equal(matchesStreamingBlock(parsed, 'assistant_message', 'item-A', block()), false)
    }
})
test('matching does not mutate its inputs', () => {
    const parsed = text()
    const streaming = block()
    const snapshot = structuredClone({ parsed, streaming })
    matchesStreamingBlock(parsed, 'assistant_message', 'item-A', streaming)
    assert.deepEqual({ parsed, streaming }, snapshot)
})
