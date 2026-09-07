import test from 'node:test'
import assert from 'node:assert/strict'
import { ephemeralPromptText, buildEphemeralResultContent } from './ephemeralContent.js'
import { agentMessageText } from './codex/canonical.js'

test('attachment-only prompts remain visible without attachment bytes', () => {
    const result = ephemeralPromptText('', [{ kind: 'image', name: 'chart.png', media_type: 'image/png', data: 'SECRET_BYTES' }])
    assert.equal(result, 'Attachment: chart.png (image/png)')
    assert.ok(!result.includes('SECRET_BYTES'))
})

test('received final answers use native provider envelopes', () => {
    const text = '# Result\n\n```js\nconst answer = 42\n```'
    assert.equal(agentMessageText(buildEphemeralResultContent('codex', text)), text)
    assert.deepEqual(buildEphemeralResultContent('claude_code', text).message.content, [{ type: 'text', text }])
})

test('the explainer acknowledgement belongs to synchronized settings', async () => {
    const { SYNCED_SETTINGS_KEYS } = await import('../constants.js')
    assert.ok(SYNCED_SETTINGS_KEYS.has('ephemeralExplainerSeen'))
})
