import test from 'node:test'
import assert from 'node:assert/strict'
import { matchesStreamingBlock } from './streamMatching.js'

const LONG = 'I am now reading the note file to find what it asks, then I will write the requested output file with the answer.'

function assistant(content = 'Done: wrote **out.txt**.', thoughts = []) {
    return { id: 'r1', type: 'gemini', content, thoughts }
}
const thought = (description = LONG, subject = 'Reading the Note') => ({ subject, description, timestamp: 't' })
const block = (blockType, text) => ({ blockIndex: 0, blockType, text, displayedText: '', uuid: null })

test('gemini text blocks match the assistant record by content', () => {
    assert.equal(matchesStreamingBlock(assistant(), 'assistant_message', 'gemini-x', block('text', 'Done: wrote **out.txt**.')), true)
    // Whitespace differences and a partial stream (prefix) still match.
    assert.equal(matchesStreamingBlock(assistant('Line one\n\nLine two'), 'assistant_message', 'gemini-x', block('text', '  Line one\nLine two ')), true)
    assert.equal(matchesStreamingBlock(assistant(), 'assistant_message', 'gemini-x', block('text', 'Done: wrote')), true)
    assert.equal(matchesStreamingBlock(assistant('Done'), 'assistant_message', 'gemini-x', block('text', 'Done: wrote more')), true)
})

test('gemini thinking blocks match any record kind carrying the first thought', () => {
    const streamed = `**Reading the Note**\n${LONG}\n**Writing**\nNext.`
    for (const kind of ['reasoning', 'assistant_message', 'tool_use']) {
        assert.equal(matchesStreamingBlock(assistant('', [thought()]), kind, 'gemini-x', block('thinking', streamed)), true, kind)
    }
    // A thought without description falls back to its subject.
    assert.equal(matchesStreamingBlock(assistant('', [thought('', 'Subject Only')]), 'reasoning', 'gemini-x', block('thinking', '**Subject Only**\n')), true)
})

test('gemini rejects mismatched content, kinds and block types', () => {
    const cases = [
        [assistant(), 'assistant_message', 'gemini-x', block('text', 'Something else')],
        [assistant(), 'reasoning', 'gemini-x', block('text', 'Done: wrote **out.txt**.')],
        [assistant(), 'user_message', 'gemini-x', block('text', 'Done: wrote **out.txt**.')],
        [assistant(''), 'assistant_message', 'gemini-x', block('text', 'Done')],
        [assistant('', [thought()]), 'reasoning', 'gemini-x', block('thinking', '**Other**\nunrelated')],
        [assistant('', [thought()]), 'user_message', 'gemini-x', block('thinking', LONG)],
        [assistant('x', []), 'assistant_message', 'gemini-x', block('thinking', 'x')],
        [assistant(), 'assistant_message', 'gemini-x', block('image', 'Done: wrote **out.txt**.')],
        [{ type: 'user', content: [{ text: 'Done' }] }, 'assistant_message', 'gemini-x', block('text', 'Done')],
    ]
    for (const args of cases) assert.equal(matchesStreamingBlock(...args), false)
})

test('an empty block never matches (stream start scans every item)', () => {
    assert.equal(matchesStreamingBlock(assistant(), 'assistant_message', 'gemini-x', block('text', '')), false)
    assert.equal(matchesStreamingBlock(assistant('', [thought()]), 'reasoning', 'gemini-x', { blockIndex: 0, blockType: 'thinking', uuid: null }), false)
})

test('malformed inputs are rejected without throwing', () => {
    for (const parsed of [null, undefined, {}, [], 'gemini', { type: 'gemini', content: 7, thoughts: 'x' }]) {
        assert.equal(matchesStreamingBlock(parsed, 'assistant_message', 'gemini-x', block('text', 'Done')), false)
        assert.equal(matchesStreamingBlock(parsed, 'reasoning', 'gemini-x', block('thinking', 'Done')), false)
    }
    for (const streaming of [null, undefined, {}, { blockType: 'text', text: 7 }]) {
        assert.equal(matchesStreamingBlock(assistant(), 'assistant_message', 'gemini-x', streaming), false)
    }
})

test('matching does not mutate its inputs', () => {
    const parsed = assistant('Done', [thought()])
    const streaming = block('text', 'Done')
    const snapshot = structuredClone({ parsed, streaming })
    matchesStreamingBlock(parsed, 'assistant_message', 'gemini-x', streaming)
    matchesStreamingBlock(parsed, 'reasoning', 'gemini-x', { ...streaming, blockType: 'thinking' })
    assert.deepEqual({ parsed, streaming }, snapshot)
})
