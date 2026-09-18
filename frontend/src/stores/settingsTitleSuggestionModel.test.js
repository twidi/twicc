import test from 'node:test'
import assert from 'node:assert/strict'

import * as constants from '../constants.js'

test('syncs the title suggestion model across clients', () => {
    assert.ok(constants.SYNCED_SETTINGS_KEYS.has('titleSuggestionModel'))
})

test('uses provider routing when the setting is missing or invalid', () => {
    const resolve = constants.resolveTitleSuggestionModel

    assert.equal(resolve?.(undefined), 'provider')
    assert.equal(resolve?.(null), 'provider')
    assert.equal(resolve?.('unknown'), 'provider')
    assert.equal(resolve?.('haiku'), 'haiku')
    assert.equal(resolve?.('luna'), 'luna')
})

test('keeps the stored model when its provider is enabled', () => {
    const resolve = constants.resolveEffectiveTitleSuggestionModel
    const both = ['claude_code', 'codex']

    assert.equal(resolve('haiku', both), 'haiku')
    assert.equal(resolve('luna', both), 'luna')
    assert.equal(resolve('provider', both), 'provider')
    // The store getter hands over an array; a Set is accepted too.
    assert.equal(resolve('haiku', new Set(both)), 'haiku')
})

test('shows the other provider model when the stored one is disabled', () => {
    const resolve = constants.resolveEffectiveTitleSuggestionModel

    assert.equal(resolve('haiku', ['codex']), 'luna')
    assert.equal(resolve('luna', ['claude_code']), 'haiku')
})

test('never resolves away the mode that follows the session provider', () => {
    const resolve = constants.resolveEffectiveTitleSuggestionModel

    assert.equal(resolve('provider', ['codex']), 'provider')
    assert.equal(resolve('provider', []), 'provider')
})

test('falls back to the session provider mode when nothing is enabled', () => {
    const resolve = constants.resolveEffectiveTitleSuggestionModel

    assert.equal(resolve('haiku', []), 'provider')
    assert.equal(resolve('luna', undefined), 'provider')
})

test('resolves an invalid stored value before anything else', () => {
    const resolve = constants.resolveEffectiveTitleSuggestionModel

    assert.equal(resolve('unknown', ['codex']), 'provider')
})

test('maps each forced model to its provider', () => {
    assert.deepEqual({ ...constants.TITLE_SUGGESTION_MODEL_PROVIDERS }, {
        haiku: 'claude_code',
        luna: 'codex',
    })
})
