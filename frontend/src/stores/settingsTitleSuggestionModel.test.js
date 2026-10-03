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

test('labels the forced title models like the settings options', () => {
    assert.deepEqual(
        { ...constants.TITLE_SUGGESTION_MODEL_LABELS },
        { haiku: 'Claude Haiku', luna: 'GPT-6 Luna' },
    )
})

test('maps a provider to the title model that runs on it', () => {
    assert.equal(constants.titleModelForProvider('claude_code'), 'haiku')
    assert.equal(constants.titleModelForProvider('codex'), 'luna')
    assert.equal(constants.titleModelForProvider('unknown'), null)
    assert.equal(constants.titleModelForProvider(undefined), null)
})

test('the default title model follows the session provider unless one is forced', () => {
    const resolve = constants.resolveSessionTitleModel

    assert.equal(resolve('provider', 'claude_code'), 'haiku')
    assert.equal(resolve('provider', 'codex'), 'luna')
    assert.equal(resolve('luna', 'claude_code'), 'luna')
    assert.equal(resolve('haiku', 'codex'), 'haiku')
    assert.equal(resolve('provider', 'unknown'), null)
    assert.equal(resolve(undefined, 'codex'), 'luna')
})

test('offers the other enabled title models as alternatives', () => {
    const alternatives = constants.titleModelAlternatives
    const both = ['claude_code', 'codex']

    assert.deepEqual(alternatives('haiku', both), ['luna'])
    assert.deepEqual(alternatives('luna', both), ['haiku'])
    assert.deepEqual(alternatives('luna', new Set(both)), ['haiku'])
    // A disabled provider is not offered.
    assert.deepEqual(alternatives('haiku', ['claude_code']), [])
    assert.deepEqual(alternatives('luna', ['codex']), [])
    assert.deepEqual(alternatives('luna', ['claude_code']), ['haiku'])
    // Nothing displayed yet: every enabled model is an alternative.
    assert.deepEqual(alternatives(null, both), ['haiku', 'luna'])
    assert.deepEqual(alternatives('haiku', undefined), [])
})
