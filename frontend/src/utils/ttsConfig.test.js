import test from 'node:test'
import assert from 'node:assert/strict'
import {
    SPEECH_FIELDS,
    DEFAULT_TEST_TEXT,
    speechFieldsOf,
    sameSpeechFields,
    isConfigValidated,
    createTtsConfig,
    patchTtsConfig,
    duplicateTtsConfig,
    isTtsAvailable,
    speechCacheKey,
    ttsConfigDisplayName,
    ttsConfigAutoName,
    formatTtsModelPrice,
    ttsModelOptions,
    ttsVoiceOptions,
    voiceAfterModelChange,
    openRouterModelUrl,
    ttsTestState,
    shouldAskAfterTest,
    ttsTestStats,
    ttsLastTestFacts,
} from './ttsConfig.js'

const validatedConfig = (id = 'a') => {
    const config = { ...createTtsConfig(id), model: 'm', voice: 'v' }
    return { ...config, validated: speechFieldsOf(config) }
}

test('constants', () => {
    assert.deepEqual(SPEECH_FIELDS, ['model', 'voice', 'response_format', 'speed', 'instructions'])
    assert.equal(DEFAULT_TEST_TEXT, 'Hello! This is a TwiCC text-to-speech test.')
})

test('a new config has the defaults and no validation', () => {
    assert.deepEqual(createTtsConfig('a'), {
        id: 'a',
        name: '',
        model: '',
        voice: '',
        response_format: 'mp3',
        speed: null,
        instructions: '',
        note: '',
        validated: null,
    })
    assert.equal(isConfigValidated(createTtsConfig('a')), false)
})

test('speechFieldsOf keeps only the speech fields', () => {
    const config = { ...createTtsConfig('a'), model: 'm', voice: 'v', speed: 1.5, instructions: 'calm' }
    assert.deepEqual(speechFieldsOf(config), {
        model: 'm', voice: 'v', response_format: 'mp3', speed: 1.5, instructions: 'calm',
    })
})

test('sameSpeechFields compares every speech field and ignores the rest', () => {
    const a = { ...createTtsConfig('a'), model: 'm', voice: 'v' }
    assert.equal(sameSpeechFields(a, { ...a, id: 'b', name: 'other', validated: null }), true)
    for (const field of SPEECH_FIELDS) {
        assert.equal(sameSpeechFields(a, { ...a, [field]: 'changed' }), false, field)
    }
    assert.equal(sameSpeechFields(a, null), false)
    assert.equal(sameSpeechFields(null, a), false)
})

test('a speech field edit clears the validation, a rename keeps it', () => {
    const v = { ...createTtsConfig('a'), model: 'm', voice: 'v' }
    const validated = { ...v, validated: speechFieldsOf(v) }
    assert.equal(isConfigValidated(validated), true)
    assert.equal(patchTtsConfig(validated, { voice: 'w' }).validated, null)
    assert.equal(isConfigValidated(patchTtsConfig(validated, { name: 'x' })), true)
})

test('patchTtsConfig returns a new object and keeps the validation on a same-value patch', () => {
    const config = validatedConfig()
    const patched = patchTtsConfig(config, { voice: 'v' })
    assert.notEqual(patched, config)
    assert.equal(isConfigValidated(patched), true)
    const renamed = patchTtsConfig(config, { name: 'x' })
    assert.equal(renamed.name, 'x')
    assert.equal(config.name, '')
    assert.equal(isConfigValidated(patchTtsConfig(config, { note: 'warm' })), true)
    assert.equal(patchTtsConfig(config, { speed: 1.2 }).speed, 1.2)
})

test('a config whose fields drifted from the validation is not validated', () => {
    const config = { ...validatedConfig(), instructions: 'louder' }
    assert.equal(isConfigValidated(config), false)
    assert.equal(isConfigValidated(null), false)
})

test('availability needs the key, an active config, and its validation', () => {
    const ok = validatedConfig('a')
    const unvalidated = { ...createTtsConfig('b'), model: 'm', voice: 'v' }
    const configs = [ok, unvalidated]
    assert.equal(isTtsAvailable({ keyPresent: true, configs, activeId: 'a' }), true)
    assert.equal(isTtsAvailable({ keyPresent: false, configs, activeId: 'a' }), false)
    assert.equal(isTtsAvailable({ keyPresent: true, configs, activeId: null }), false)
    assert.equal(isTtsAvailable({ keyPresent: true, configs, activeId: 'b' }), false)
    assert.equal(isTtsAvailable({ keyPresent: true, configs, activeId: 'missing' }), false)
    assert.equal(isTtsAvailable({ keyPresent: true, configs: [], activeId: 'a' }), false)
    assert.equal(isTtsAvailable({ keyPresent: true, configs: undefined, activeId: 'a' }), false)
})

test('an unnamed config is duplicated unnamed', () => {
    const copy = duplicateTtsConfig({ ...validatedConfig('a'), name: '' }, 'b')
    assert.equal(copy.name, '')
})

test('the display name falls back to the model name and the voice', () => {
    const models = [{ id: 'hexgrad/kokoro-82m', name: 'Kokoro 82M', voices: ['af_bella'] }]
    const base = { ...createTtsConfig('a'), model: 'hexgrad/kokoro-82m', voice: 'af_bella' }
    assert.equal(ttsConfigDisplayName(base, models), 'Kokoro 82M · af_bella')
    assert.equal(ttsConfigDisplayName({ ...base, name: '  Mine  ' }, models), 'Mine')
    assert.equal(ttsConfigDisplayName({ ...base, name: '   ' }, models), 'Kokoro 82M · af_bella')
    // An unknown model shows its id; a missing voice is left out.
    assert.equal(ttsConfigDisplayName({ ...base, model: 'x/y', voice: '' }, models), 'x/y')
    assert.equal(ttsConfigDisplayName(base), 'hexgrad/kokoro-82m · af_bella')
    assert.equal(ttsConfigAutoName(createTtsConfig('a'), models), 'Unnamed config')
})

test('the list price reads per million units', () => {
    assert.equal(formatTtsModelPrice({ prompt: 0.00000062, completion: 0 }), '$0.62/M in')
    assert.equal(formatTtsModelPrice({ prompt: 0.0000005, completion: 0.000006 }), '$0.5/M in · $6/M out')
    assert.equal(formatTtsModelPrice({ prompt: 0.000015, completion: null }), '$15/M in')
    assert.equal(formatTtsModelPrice({ prompt: 0, completion: 0.0025 }), '$2500/M out')
    assert.equal(formatTtsModelPrice({ prompt: 0, completion: 0 }), 'Free')
    assert.equal(formatTtsModelPrice({ prompt: null, completion: null }), '')
    assert.equal(formatTtsModelPrice(undefined), '')
    // One known zero and one unknown price is not "Free".
    assert.equal(formatTtsModelPrice({ prompt: 0, completion: null }), '')
    assert.equal(formatTtsModelPrice({ prompt: null, completion: 0 }), '')
})

const MODELS = [
    { id: 'hexgrad/kokoro-82m', name: 'Kokoro 82M', voices: ['af_bella'], pricing: { prompt: 0.00000062, completion: 0 } },
    { id: 'fish/s1', name: 'Fish S1', voices: [], pricing: { prompt: null, completion: null } },
]

test('model options carry the official name, the price line and the id as keywords', () => {
    assert.deepEqual(ttsModelOptions(MODELS, 'hexgrad/kokoro-82m'), [
        { value: 'fish/s1', label: 'Fish S1', description: 'No list price', keywords: 'fish/s1' },
        { value: 'hexgrad/kokoro-82m', label: 'Kokoro 82M', description: '$0.62/M in', keywords: 'hexgrad/kokoro-82m' },
    ])
    assert.equal(ttsModelOptions(MODELS, '').length, 2)
})

test('model options are sorted by official name, whatever the API order', () => {
    const models = [
        { id: 'z/one', name: 'kokoro 82M', voices: [], pricing: null },
        { id: 'a/two', name: 'ElevenLabs: Eleven v3', voices: [], pricing: null },
        { id: 'm/three', name: 'Google: Gemini TTS', voices: [], pricing: null },
    ]
    assert.deepEqual(ttsModelOptions(models, '').map(o => o.label), ['ElevenLabs: Eleven v3', 'Google: Gemini TTS', 'kokoro 82M'])
    // A stored model missing from the list still comes first.
    assert.equal(ttsModelOptions(models, 'gone/model')[0].value, 'gone/model')
})

test('a stored model missing from the list stays visible first', () => {
    assert.deepEqual(ttsModelOptions(MODELS, 'acme/old')[0], { value: 'acme/old', label: 'acme/old', description: 'Not in the OpenRouter list' })
    // An empty list (not loaded, or OpenRouter unreachable) makes no claim about the model.
    assert.deepEqual(ttsModelOptions([], 'acme/old'), [{ value: 'acme/old', label: 'acme/old', description: '' }])
})

test('voice options keep a stored voice the model does not list', () => {
    assert.deepEqual(ttsVoiceOptions(['a', 'b'], 'b'), [{ value: 'a', label: 'a' }, { value: 'b', label: 'b' }])
    assert.deepEqual(ttsVoiceOptions(['a'], 'z')[0], { value: 'z', label: 'z', description: "Not in this model's list" })
    assert.equal(ttsVoiceOptions(['a'], '').length, 1)
    // A model missing from the loaded list: its voices are unknown, so no claim.
    assert.deepEqual(ttsVoiceOptions([], 'z', { listed: false }), [{ value: 'z', label: 'z', description: '' }])
})

test('the test state of a config follows the playback of its own test only', () => {
    assert.equal(ttsTestState({ source: 'test', configId: 'a', status: 'loading' }, 'a'), 'loading')
    assert.equal(ttsTestState({ source: 'test', configId: 'a', status: 'playing' }, 'a'), 'playing')
    assert.equal(ttsTestState({ source: 'test', configId: 'a', status: 'playing' }, 'b'), 'idle')
    assert.equal(ttsTestState({ source: 'message', configId: null, status: 'playing' }, 'a'), 'idle')
    assert.equal(ttsTestState({ source: null, configId: null, status: 'idle' }, 'a'), 'idle')
    assert.equal(ttsTestState(undefined, 'a'), 'idle')
})

test('the question after a test is asked only for unchanged, not yet validated fields', () => {
    const fresh = { ...createTtsConfig('a'), model: 'm', voice: 'v' }
    const tested = speechFieldsOf(fresh)
    assert.equal(shouldAskAfterTest(fresh, tested), true)
    // Already validated with these fields: a quick re-listen, no question.
    assert.equal(shouldAskAfterTest({ ...fresh, validated: tested }, tested), false)
    // Validated with older fields: ask again.
    assert.equal(shouldAskAfterTest({ ...fresh, validated: { ...tested, voice: 'old' } }, tested), true)
    // A field edited while the test loaded: no question.
    assert.equal(shouldAskAfterTest({ ...fresh, voice: 'w' }, tested), false)
})

test('a model change keeps only a voice the new model lists', () => {
    assert.equal(voiceAfterModelChange('alloy', ['alloy', 'nova']), 'alloy')
    assert.equal(voiceAfterModelChange('alloy', ['Kore']), '')
    assert.equal(voiceAfterModelChange('alloy', []), '')
    assert.equal(voiceAfterModelChange('', ['alloy']), '')
})

test('the OpenRouter page of a model is its id as a path', () => {
    assert.equal(openRouterModelUrl('hexgrad/kokoro-82m'), 'https://openrouter.ai/hexgrad/kokoro-82m')
    assert.equal(openRouterModelUrl('heygen/heygen-voice-1-turbo:free'), 'https://openrouter.ai/heygen/heygen-voice-1-turbo:free')
})

test('duplicate keeps the validation and is named "(copy)"', () => {
    const config = { ...validatedConfig('a'), name: 'Kore' }
    const copy = duplicateTtsConfig(config, 'b')
    assert.equal(copy.id, 'b')
    assert.equal(copy.name, 'Kore (copy)')
    assert.deepEqual(speechFieldsOf(copy), speechFieldsOf(config))
    assert.equal(isConfigValidated(copy), true)
    assert.deepEqual(copy.validated, config.validated)
    assert.notEqual(copy.validated, config.validated)
    assert.notEqual(copy, config)
    assert.equal(config.id, 'a')
})

test('cache key changes with any speech field', () => {
    const fields = speechFieldsOf({ ...createTtsConfig('a'), model: 'm', voice: 'v' })
    const key = speechCacheKey('hello', fields)
    assert.equal(key, JSON.stringify(['hello', 'm', 'v', 'mp3', null, '']))
    assert.equal(speechCacheKey('hello', { ...fields }), key)
    assert.notEqual(speechCacheKey('hello!', fields), key)
    for (const field of SPEECH_FIELDS) {
        assert.notEqual(speechCacheKey('hello', { ...fields, [field]: 'changed' }), key, field)
    }
})

const RUN = { at: '2026-10-30T10:42:00+00:00', generation_id: 'gen-1', chars: 2000, generation_ms: 2200, audio_seconds: 143, cost: 0.008 }

test('the last test stats compare across texts of any length', () => {
    assert.equal(ttsTestStats(RUN), '0.4¢ / 1k ch · 0.34¢ / mn · 1.1 s / 1k ch')
    assert.equal(ttsTestStats({ ...RUN, audio_seconds: null }), '0.4¢ / 1k ch · 1.1 s / 1k ch')
    assert.equal(ttsTestStats({ ...RUN, cost: null }), 'price pending… · 1.1 s / 1k ch')
    assert.equal(ttsTestStats({ ...RUN, cost: null, generation_id: '' }), 'price unknown · 1.1 s / 1k ch')
    assert.equal(ttsTestStats({ ...RUN, cost: 0.08, chars: 1000, generation_ms: 15000 }), '8¢ / 1k ch · 3.4¢ / mn · 15 s / 1k ch')
    assert.equal(ttsTestStats(null), '')
    assert.equal(ttsTestStats({ ...RUN, chars: 0 }), '')
})

test('the last test facts give the exact figures', () => {
    assert.equal(ttsLastTestFacts(RUN), '2,000 chars · 2 min 23 s of audio · generated in 2.2 s · $0.0080')
    assert.equal(ttsLastTestFacts({ ...RUN, chars: 35, audio_seconds: 3.24, generation_ms: 1760, cost: null }), '35 chars · 3.2 s of audio · generated in 1.8 s · price pending…')
    assert.equal(ttsLastTestFacts(undefined), '')
})
