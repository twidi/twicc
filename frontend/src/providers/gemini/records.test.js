import test from 'node:test'
import assert from 'node:assert/strict'
import {
    apiErrorData,
    assistantText,
    buildUserRecord,
    finishedToolCalls,
    invokedAgentName,
    isAgentToolName,
    recordThoughts,
    thoughtsMarkdown,
    toolCallFileDiff,
    toolCallOutput,
    userMessageAttachmentCount,
    userMessageMediaBlocks,
    userMessageText,
} from './records.js'
import { attachmentCountForMessage, attachmentMatchKey, messageAttachmentLayout } from '../../utils/attachmentStrip.js'

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

test('optimistic user record carries legacy medias as inlineData parts', () => {
    const attachments = {
        images: [{ type: 'image', source: { type: 'base64', media_type: 'image/png', data: 'AAAA' } }],
        documents: [
            { type: 'document', source: { type: 'base64', media_type: 'application/pdf', data: 'JVBE' } },
            { type: 'document', source: { type: 'text', media_type: 'text/plain', data: 'notes' } },
            { type: 'document', source: { type: 'base64', media_type: 'application/pdf' } },
        ],
    }
    const record = buildUserRecord('look', attachments)
    assert.equal(userMessageText(record), 'look')
    assert.equal(userMessageAttachmentCount(record), 3)
    assert.deepEqual(record.content[1], { inlineData: { mimeType: 'image/png', data: 'AAAA' } })
    assert.deepEqual(buildUserRecord('', undefined), { type: 'user', content: [] })
    assert.deepEqual(buildUserRecord('x', null).content, [{ text: 'x' }])
})

test('media parts map to the shared strip blocks', () => {
    const parsed = {
        type: 'user',
        content: [
            { inlineData: { mimeType: 'image/jpeg', data: 'IMG' } },
            { inlineData: { mimeType: 'application/pdf', data: 'PDF' } },
            { fileData: { mimeType: 'image/png', fileUri: 'gs://bucket/shot.png' } },
            { fileData: { mimeType: 'text/plain', fileUri: '/tmp/notes.txt' } },
            { text: 'hi' },
        ],
    }
    const blocks = userMessageMediaBlocks(parsed)
    assert.deepEqual(blocks.map(b => b.type), ['image', 'document', 'local_image', 'document'])
    assert.equal(blocks[0].source.media_type, 'image/jpeg')
    assert.equal(blocks[2].path, 'gs://bucket/shot.png')
    assert.equal(blocks[3].title, 'notes.txt')
    assert.deepEqual(userMessageMediaBlocks({ type: 'user', content: 'text' }), [])
    assert.deepEqual(userMessageMediaBlocks(null), [])
})

test('stored file attachments render a strip and match the optimistic bubble', () => {
    const stored = {
        type: 'user',
        content: [{ text: '' }],
        twicc_attachments: {
            owner: 'session-1',
            entries: [
                { n: 1, name: 'report.pdf', kind: 'PDF', mode: 'file', artifact_name: 'report.pdf' },
                { n: 2, name: 'shot.png', kind: 'image', mode: 'file', artifact_name: 'shot.png' },
            ],
        },
    }
    const { strip } = messageAttachmentLayout(stored, userMessageMediaBlocks(stored))
    assert.deepEqual(strip.map(item => [item.name, item.mode, item.canOpenArtifact]), [
        ['report.pdf', 'file', true],
        ['shot.png', 'file', true],
    ])
    assert.equal(userMessageText(stored), '')
    // Attachment-only send: the optimistic bubble (composer count) and the
    // stored record (manifest entries) share the same match key.
    const optimistic = { ...buildUserRecord(''), attachmentCount: 2 }
    const key = record => attachmentMatchKey(userMessageText(record), attachmentCountForMessage(record, userMessageAttachmentCount(record)))
    assert.equal(key(optimistic), 'a:2')
    assert.equal(key(stored), 'a:2')
})

test('invoke_agent is the agent tool and carries the agent name', () => {
    assert.equal(isAgentToolName('invoke_agent'), true)
    assert.equal(isAgentToolName('read_file'), false)
    assert.equal(invokedAgentName({ agent_name: ' codebase_investigator ', prompt: 'Map the repo' }), 'codebase_investigator')
    assert.equal(invokedAgentName({ agent_name: '  ' }), null)
    assert.equal(invokedAgentName(null), null)
})
