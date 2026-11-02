import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

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
    assert.equal(resolve?.('openrouter'), 'openrouter')
    // The name before the rename to "openrouter": unknown, so the default.
    assert.equal(resolve?.('qwen'), 'provider')
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
        { haiku: 'Claude Haiku 5.5', luna: 'GPT-6 Luna', openrouter: 'OpenRouter' },
    )
})

test('names the model behind the OpenRouter choice in one constant', () => {
    assert.equal(constants.OPENROUTER_MODEL_DESCRIPTION, 'Qwen3.8 27B on Cerebras')
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

test('keeps an OpenRouter choice while the OpenRouter key is set, with or without an agent', () => {
    const resolve = constants.resolveEffectiveTitleSuggestionModel

    assert.equal(resolve('openrouter', ['claude_code'], true), 'openrouter')
    // OpenRouter needs no Claude Code or Codex provider.
    assert.equal(resolve('openrouter', [], true), 'openrouter')
})

test('an OpenRouter choice works as the session provider mode without the key', () => {
    const resolve = constants.resolveEffectiveTitleSuggestionModel

    assert.equal(resolve('openrouter', ['claude_code'], false), 'provider')
    // The key flag defaults to absent.
    assert.equal(resolve('openrouter', ['claude_code', 'codex']), 'provider')
})

test('a displaced agent model gives way to the other agent, not to OpenRouter', () => {
    const resolve = constants.resolveEffectiveTitleSuggestionModel

    assert.equal(resolve('haiku', ['codex'], true), 'luna')
    assert.equal(resolve('luna', ['claude_code'], true), 'haiku')
})

test('offers OpenRouter as an alternative only while the OpenRouter key is set', () => {
    const alternatives = constants.titleModelAlternatives
    const both = ['claude_code', 'codex']

    assert.deepEqual(alternatives('haiku', both, true), ['luna', 'openrouter'])
    assert.deepEqual(alternatives('openrouter', both, true), ['haiku', 'luna'])
    assert.deepEqual(alternatives('haiku', both, false), ['luna'])
    assert.deepEqual(alternatives('haiku', both), ['luna'])
    // OpenRouter needs no agent.
    assert.deepEqual(alternatives(null, [], true), ['openrouter'])
})

const settingsStoreSource = readFileSync(new URL('./settings.js', import.meta.url), 'utf8')

test('the effective title model getter reads the OpenRouter key from the speech store', () => {
    assert.match(
        settingsStoreSource,
        /resolveEffectiveTitleSuggestionModel\(\s*state\.titleSuggestionModel,\s*this\.enabledProviders,\s*useTextToSpeechStore\(\)\.keyPresent,?\s*\)/,
    )
    assert.match(settingsStoreSource, /import \{ useTextToSpeechStore \} from '\.\/textToSpeech'/)
})

test('no write of the stored title model depends on the OpenRouter key', () => {
    const writes = settingsStoreSource.split('\n').filter(line => /titleSuggestionModel\s*=[^=]/.test(line))
    assert.ok(writes.length > 0, 'the setter writes the stored model')
    for (const line of writes) assert.doesNotMatch(line, /keyPresent|TextToSpeech/)
    const setter = settingsStoreSource.slice(
        settingsStoreSource.indexOf('setTitleSuggestionModel(model) {'),
        settingsStoreSource.indexOf('setTitleSystemPrompt('),
    )
    assert.doesNotMatch(setter, /keyPresent|TextToSpeech/)
})
