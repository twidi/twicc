import test from 'node:test'
import assert from 'node:assert/strict'

const titleSuggestion = await import('./titleSuggestion.js').catch(() => ({}))

test('builds a provider-routed request when the model setting is absent', () => {
    const payload = titleSuggestion.buildTitleSuggestionRequest?.({
        sessionId: 'session-1',
        provider: 'codex',
        systemPrompt: 'Summarize {text}',
    })

    assert.deepEqual(payload, {
        type: 'suggest_title',
        sessionId: 'session-1',
        provider: 'codex',
        systemPrompt: 'Summarize {text}',
        titleSuggestionModel: 'provider',
    })
})

test('keeps a fixed model and an explicit prompt in the request', () => {
    const payload = titleSuggestion.buildTitleSuggestionRequest?.({
        sessionId: 'session-1',
        provider: 'claude_code',
        systemPrompt: 'Summarize {text}',
        prompt: 'A user message',
        titleSuggestionModel: 'luna',
    })

    assert.deepEqual(payload, {
        type: 'suggest_title',
        sessionId: 'session-1',
        provider: 'claude_code',
        systemPrompt: 'Summarize {text}',
        prompt: 'A user message',
        titleSuggestionModel: 'luna',
    })
})
