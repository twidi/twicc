import test from 'node:test'
import assert from 'node:assert/strict'
import {
    apiErrorData,
    assistantText,
    buildUserRecord,
    finishedToolCalls,
    recordThoughts,
    thoughtsMarkdown,
    toolCallFileDiff,
    toolCallOutput,
    userMessageAttachmentCount,
    userMessageText,
} from './records.js'

test('user text joins text parts and ignores tool responses and media', () => {
    const parsed = { type: 'user', content: [{ text: 'Read ' }, { functionResponse: { id: 'x' } }, { text: 'note.txt' }, { inlineData: { mimeType: 'image/png' } }] }
    assert.equal(userMessageText(parsed), 'Read note.txt')
    assert.equal(userMessageAttachmentCount(parsed), 1)
    assert.equal(userMessageText({ type: 'user', content: 'plain' }), 'plain')
    assert.equal(userMessageText(buildUserRecord('hello')), 'hello')
    for (const bad of [null, undefined, 'x', [], { content: 7 }]) {
        assert.equal(userMessageText(bad), '')
        assert.equal(userMessageAttachmentCount(bad), 0)
    }
})

test('assistant text, thoughts and finished tool calls', () => {
    const parsed = {
        type: 'gemini',
        content: 'Done.',
        thoughts: [{ subject: 'Plan', description: 'Read then write.' }, { subject: '', description: '  ' }, 'bad'],
        toolCalls: [
            { id: 'a', name: 'read_file', status: 'success' },
            { id: 'b', name: 'write_file', status: 'executing' },
            { id: 'c', name: 'replace', status: 'cancelled' },
        ],
    }
    assert.equal(assistantText(parsed), 'Done.')
    assert.equal(assistantText({ type: 'user', content: 'x' }), '')
    assert.deepEqual(recordThoughts(parsed).map(t => t.subject), ['Plan'])
    assert.equal(thoughtsMarkdown(recordThoughts(parsed)), '**Plan**\n\nRead then write.')
    assert.deepEqual(finishedToolCalls(parsed).map(c => c.id), ['a', 'c'])
    assert.deepEqual(finishedToolCalls({ toolCalls: 'x' }), [])
})

test('tool call output, error and file diff', () => {
    const ok = { result: [{ functionResponse: { response: { output: 'Successfully created out.txt' } } }] }
    assert.deepEqual(toolCallOutput(ok), { output: 'Successfully created out.txt', error: null })
    const failed = { result: [{ functionResponse: { response: { error: 'File not found' } } }] }
    assert.deepEqual(toolCallOutput(failed), { output: null, error: 'File not found' })
    assert.deepEqual(toolCallOutput({ resultDisplay: 'shown' }), { output: 'shown', error: null })
    assert.deepEqual(toolCallOutput({}), { output: null, error: null })
    const edit = { resultDisplay: { fileDiff: '@@ -0,0 +1 @@\n+bye\n', fileName: 'out.txt', filePath: '/x/out.txt', originalContent: '', newContent: 'bye\n' } }
    assert.equal(toolCallFileDiff(edit).newContent, 'bye\n')
    assert.equal(toolCallFileDiff({ resultDisplay: 'text' }), null)
})

test('error records become generic provider errors', () => {
    const data = apiErrorData({ id: 'e', type: 'error', content: 'API key is missing' })
    assert.equal(data.type, 'twicc_provider_error')
    assert.equal(data.error.message, 'API key is missing')
    const generic = { type: 'twicc_provider_error', error: { message: 'x' } }
    assert.equal(apiErrorData(generic), generic)
})
