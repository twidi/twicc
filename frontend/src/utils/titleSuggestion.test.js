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

test('asks the backend not to fall back only when the choice is explicit', () => {
    const base = {
        sessionId: 'session-1',
        provider: 'claude_code',
        systemPrompt: 'Summarize {text}',
        titleSuggestionModel: 'luna',
    }

    const strict = titleSuggestion.buildTitleSuggestionRequest?.({ ...base, noFallback: true })
    const loose = titleSuggestion.buildTitleSuggestionRequest?.(base)

    assert.equal(strict.noFallback, true)
    assert.equal(strict.titleSuggestionModel, 'luna')
    assert.equal('noFallback' in loose, false)
})
