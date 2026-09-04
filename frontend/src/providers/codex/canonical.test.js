import assert from 'node:assert/strict'
import test from 'node:test'

import {
    agentMessageText,
    buildOptimisticUserMessage,
    completedItem,
    fileChangeItem,
    imageGeneration,
    mcpToolCallItem,
    userMessageAttachmentCount,
    userMessageImages,
    userMessageText,
} from './canonical.js'

function completed(item) {
    return {
        timestamp: '2026-08-31T10:00:00.000Z',
        type: 'event_msg',
        payload: {
            type: 'item_completed',
            thread_id: 'thread-1',
            turn_id: 'turn-1',
            item,
            completed_at_ms: 1_788_171_200_000,
        },
    }
}

test('canonical accessors accept completed items and preserve source order', () => {
    const user = completed({
        type: 'UserMessage',
        id: 'u1',
        content: [
            { type: 'text', text: 'hello', text_elements: [] },
            { type: 'skill', name: 'review', path: '/skills/review/SKILL.md' },
            { type: 'image', image_url: 'data:image/png;base64,AA' },
            { type: 'text', text: ' world', text_elements: [] },
            { type: 'local_image', path: '/tmp/a.png' },
            { type: 'mention', name: 'calendar', path: 'app://calendar' },
        ],
    })
    const agent = completed({
        type: 'AgentMessage',
        id: 'a1',
        content: [{ type: 'Text', text: 'one' }, { type: 'Text', text: ' two' }],
    })

    assert.equal(completedItem(user).id, 'u1')
    assert.equal(userMessageText(user), 'hello world')
    assert.deepEqual(userMessageImages(user), [
        { type: 'image', value: 'data:image/png;base64,AA' },
        { type: 'local_image', value: '/tmp/a.png' },
    ])
    assert.equal(userMessageAttachmentCount(user), 2)
    assert.equal(agentMessageText(agent), 'one two')
})

test('structured result accessors reject unsupported completed items', () => {
    const patch = completed({ type: 'FileChange', id: 'p1', changes: {}, status: 'completed' })
    const mcp = completed({ type: 'McpToolCall', id: 'm1', result: { isError: true }, status: 'failed' })
    const web = completed({ type: 'WebSearch', id: 'w1' })

    assert.equal(fileChangeItem(patch).id, 'p1')
    assert.equal(mcpToolCallItem(mcp).id, 'm1')
    assert.equal(fileChangeItem(web), null)
    assert.equal(mcpToolCallItem(web), null)
})

test('image generation normalizes native and extension field casing', () => {
    assert.deepEqual(imageGeneration(completed({
        type: 'ImageGeneration', id: 'n1', status: 'completed', revised_prompt: 'p', result: 'r', saved_path: '/n',
    })), {
        id: 'n1', status: 'completed', revisedPrompt: 'p', result: 'r', savedPath: '/n',
        transparentBackground: null, failure: null,
    })
    assert.deepEqual(imageGeneration(completed({
        type: 'Extension', kind: 'image_gen.generation', id: 'e1', status: 'failed', revisedPrompt: 'p2', result: '',
        savedPath: '/e', transparentBackground: true, failure: { type: 'usageLimitExceeded' },
    })), {
        id: 'e1', status: 'failed', revisedPrompt: 'p2', result: '', savedPath: '/e',
        transparentBackground: true, failure: { type: 'usageLimitExceeded' },
    })
})

test('optimistic user messages use the canonical UserMessage shape', () => {
    assert.deepEqual(buildOptimisticUserMessage('hello', [
        { type: 'image', image_url: 'data:image/png;base64,AA' },
        { type: 'local_image', path: '/tmp/a.png' },
    ]), {
        type: 'event_msg',
        payload: {
            type: 'item_completed',
            item: {
                type: 'UserMessage',
                id: 'twicc-optimistic',
                content: [
                    { type: 'text', text: 'hello', text_elements: [] },
                    { type: 'image', image_url: 'data:image/png;base64,AA' },
                    { type: 'local_image', path: '/tmp/a.png' },
                ],
            },
        },
    })
})
