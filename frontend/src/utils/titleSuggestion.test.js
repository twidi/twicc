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

test('sends a Qwen choice as is', () => {
    const payload = titleSuggestion.buildTitleSuggestionRequest?.({
        sessionId: 'session-1',
        provider: 'claude_code',
        systemPrompt: 'Summarize {text}',
        titleSuggestionModel: 'qwen',
    })

    assert.equal(payload.titleSuggestionModel, 'qwen')
})

test('names the model that made the suggestion', () => {
    const suggestionModel = titleSuggestion.suggestionModel

    assert.equal(suggestionModel?.({ titleModel: 'qwen', titleProvider: null }), 'qwen')
    assert.equal(suggestionModel?.({ titleModel: 'luna', titleProvider: 'codex' }), 'luna')
    // A reply without the model field: the model of the provider that made it.
    assert.equal(suggestionModel?.({ titleModel: null, titleProvider: 'codex' }), 'luna')
    assert.equal(suggestionModel?.({ titleProvider: 'claude_code' }), 'haiku')
    assert.equal(suggestionModel?.({}), null)
    assert.equal(suggestionModel?.(null), null)
})

test('a suggestion made by another model than the one asked for is a fallback', () => {
    const isFallbackEntry = titleSuggestion.isFallbackEntry

    // Qwen failed, Haiku made the title: same provider fields, different models.
    assert.equal(isFallbackEntry?.({
        suggestion: 't', requestedModel: 'qwen', titleModel: 'haiku',
        requestedProvider: 'claude_code', titleProvider: 'claude_code',
    }), true)
    assert.equal(isFallbackEntry?.({ suggestion: 't', requestedModel: 'qwen', titleModel: 'haiku' }), true)
    assert.equal(isFallbackEntry?.({
        suggestion: 't', requestedModel: 'qwen', titleModel: 'qwen',
        requestedProvider: 'claude_code', titleProvider: null,
    }), false)
    assert.equal(isFallbackEntry?.({ suggestion: 't', requestedModel: 'luna', titleModel: 'luna' }), false)
})

test('without a model field, a fallback compares the providers as before', () => {
    const isFallbackEntry = titleSuggestion.isFallbackEntry

    assert.equal(isFallbackEntry?.({ suggestion: 't', requestedProvider: 'claude_code', titleProvider: 'codex' }), true)
    assert.equal(isFallbackEntry?.({ suggestion: 't', requestedProvider: 'codex', titleProvider: 'codex' }), false)
    assert.equal(isFallbackEntry?.({
        suggestion: 't', requestedModel: null, titleModel: 'luna',
        requestedProvider: 'claude_code', titleProvider: 'codex',
    }), true)
    assert.equal(isFallbackEntry?.({ suggestion: 't', requestedProvider: 'claude_code', titleProvider: null }), false)
})

test('no suggestion is never a fallback', () => {
    const isFallbackEntry = titleSuggestion.isFallbackEntry

    assert.equal(isFallbackEntry?.({ suggestion: null, requestedModel: 'qwen', titleModel: 'haiku' }), false)
    assert.equal(isFallbackEntry?.({ requestedProvider: 'claude_code', titleProvider: 'codex' }), false)
    assert.equal(isFallbackEntry?.(null), false)
})
