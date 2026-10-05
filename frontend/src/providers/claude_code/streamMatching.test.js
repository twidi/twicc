import test from 'node:test'
import assert from 'node:assert/strict'
import { matchesStreamingBlock } from './streamMatching.js'

const parsed = { message: { id: 'message-A' }, uuid: 'block-A' }
const block = { blockIndex: 0, blockType: 'text', uuid: 'block-A' }

test('claude waits for a matching completed block', () => {
    assert.equal(matchesStreamingBlock(parsed, 'assistant_message', 'message-A', { ...block, uuid: null }), false)
    assert.equal(matchesStreamingBlock(parsed, 'assistant_message', 'message-A', block), true)
    assert.equal(matchesStreamingBlock(parsed, 'assistant_message', 'message-B', block), false)
    assert.equal(matchesStreamingBlock(null, 'assistant_message', 'message-A', block), false)
})
test('claude isolates content blocks', () => {
    assert.equal(matchesStreamingBlock(parsed, 'assistant_message', 'message-A', { ...block, blockIndex: 1, uuid: 'block-B' }), false)
})
test('claude preserves eligible item kinds', () => {
    for (const kind of ['assistant_message', 'content_items', 'reasoning']) assert.equal(matchesStreamingBlock(parsed, kind, 'message-A', block), true)
    for (const kind of ['user_message', 'tool_result', undefined]) assert.equal(matchesStreamingBlock(parsed, kind, 'message-A', block), false)
})
test('matching does not mutate its inputs', () => {
    const snapshot = structuredClone({ parsed, block })
    matchesStreamingBlock(parsed, 'assistant_message', 'message-A', block)
    assert.deepEqual({ parsed, block }, snapshot)
})
