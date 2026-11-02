import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import {
    SPEECH_FIELDS,
    SCRIPT_FIELDS,
    MAX_PCM_RATE,
    MAX_PCM_CHANNELS,
    SCRIPT_OUTPUT_OPTIONS,
    SCRIPT_FORMAT_OPTIONS,
    DEFAULT_TEST_TEXT,
    kindOf,
    createScriptConfig,
    scriptStreamingNote,
    requestedFormat,
    canTestScript,
    positiveIntegerInput,
    speechFieldsOf,
    sameSpeechFields,
    isConfigValidated,
    createTtsConfig,
    patchTtsConfig,
    duplicateTtsConfig,
    isTtsAvailable,
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
    ttsModelFacts,
    ttsStreamingLabel,
    ttsFormatOptions,
    formatAfterModelChange,
    ttsStreamingNote,
    ADAPT_PROVIDERS,
    ADAPT_PROVIDER_LABELS,
    QWEN_ADAPT,
    normalizeAdapt,
    effectiveAdapt,
    adaptOptions,
    adaptHint,
    ttsAdaptationFacts,
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
        kind: 'openrouter',
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
        kind: 'openrouter', model: 'm', voice: 'v', response_format: 'mp3', speed: 1.5, instructions: 'calm',
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

test('the last test facts show the parts of a split run', () => {
    assert.equal(ttsLastTestFacts({ ...RUN, parts: 4 }), '2,000 chars · 4 parts · 2 min 23 s of audio · generated in 2.2 s · $0.0080')
    assert.equal(ttsLastTestFacts({ ...RUN, parts: 1 }), '2,000 chars · 2 min 23 s of audio · generated in 2.2 s · $0.0080')
    assert.equal(ttsLastTestFacts({ ...RUN, parts: null }), '2,000 chars · 2 min 23 s of audio · generated in 2.2 s · $0.0080')
})

test('stats and facts show the first audio', () => {
    const run = { ...RUN, first_audio_ms: 1160 }
    assert.equal(ttsTestStats(run), '0.4¢ / 1k ch · 0.34¢ / mn · 1.1 s / 1k ch · 1st audio 1.2 s')
    assert.equal(ttsLastTestFacts(run), '2,000 chars · 2 min 23 s of audio · generated in 2.2 s · first audio after 1.2 s · $0.0080')
})

test('old records without first_audio_ms show no figure', () => {
    assert.equal(ttsTestStats({ ...RUN, first_audio_ms: null }), '0.4¢ / 1k ch · 0.34¢ / mn · 1.1 s / 1k ch')
    assert.equal(ttsLastTestFacts({ ...RUN, first_audio_ms: null }), '2,000 chars · 2 min 23 s of audio · generated in 2.2 s · $0.0080')
    assert.equal(ttsTestStats(RUN), '0.4¢ / 1k ch · 0.34¢ / mn · 1.1 s / 1k ch')
    assert.equal(ttsLastTestFacts(RUN), '2,000 chars · 2 min 23 s of audio · generated in 2.2 s · $0.0080')
})

// ── Known model facts ────────────────────────────────────────────

const HEYGEN = { id: 'heygen/v', name: 'HeyGen', formats: ['pcm'], streams: ['pcm'], max_chars: 5000, pricing: {}, voices: [] }
const ELEVEN = { id: 'eleven/v', name: 'Eleven', formats: ['mp3', 'pcm'], streams: [], max_chars: 10000, pricing: {}, voices: [] }
const GROK = { id: 'grok/v', name: 'Grok', formats: ['mp3', 'pcm'], streams: ['mp3', 'pcm'], max_chars: 15000, pricing: {}, voices: [] }
const PARTIAL = { id: 'p/v', name: 'Partial', formats: ['mp3', 'pcm'], streams: ['pcm'], max_chars: 2000, pricing: {}, voices: [] }
const UNKNOWN = { id: 'new/v', name: 'New', formats: null, streams: null, max_chars: 2000, pricing: {}, voices: [] }

test('ttsModelFacts finds a listed model, else null', () => {
    assert.equal(ttsModelFacts([HEYGEN, ELEVEN], 'eleven/v'), ELEVEN)
    assert.equal(ttsModelFacts([HEYGEN], 'gone/v'), null)
})

test('ttsStreamingLabel says whether the model streams, nothing when unknown', () => {
    assert.equal(ttsStreamingLabel(GROK), 'Streaming')
    assert.equal(ttsStreamingLabel(HEYGEN), 'Streaming')
    assert.equal(ttsStreamingLabel(ELEVEN), 'No streaming')
    assert.equal(ttsStreamingLabel(PARTIAL), 'Streaming in PCM')
    assert.equal(ttsStreamingLabel(UNKNOWN), '')
    assert.equal(ttsStreamingLabel(null), '')
})

test('the model picker line carries the price and the streaming support', () => {
    const priced = { ...ELEVEN, pricing: { prompt: 0.00004, completion: 0 } }
    assert.equal(ttsModelOptions([priced], '')[0].description, '$40/M in · No streaming')
    assert.equal(ttsModelOptions([UNKNOWN], '')[0].description, 'No list price')
})

test('ttsFormatOptions disables a format the model refuses, keeps both when unknown', () => {
    assert.deepEqual(ttsFormatOptions(HEYGEN), [
        { value: 'mp3', label: 'MP3', disabled: true, title: 'MP3: not supported by this model' },
        { value: 'pcm', label: 'PCM' },
    ])
    assert.deepEqual(ttsFormatOptions(ELEVEN), [{ value: 'mp3', label: 'MP3' }, { value: 'pcm', label: 'PCM' }])
    assert.deepEqual(ttsFormatOptions(UNKNOWN), [{ value: 'mp3', label: 'MP3' }, { value: 'pcm', label: 'PCM' }])
    assert.deepEqual(ttsFormatOptions(null), [{ value: 'mp3', label: 'MP3' }, { value: 'pcm', label: 'PCM' }])
})

test('formatAfterModelChange switches to an accepted format only when needed', () => {
    assert.equal(formatAfterModelChange('mp3', HEYGEN), 'pcm')
    assert.equal(formatAfterModelChange('pcm', HEYGEN), 'pcm')
    assert.equal(formatAfterModelChange('pcm', ELEVEN), 'pcm')
    assert.equal(formatAfterModelChange('mp3', UNKNOWN), 'mp3')
    assert.equal(formatAfterModelChange('mp3', null), 'mp3')
})

test('ttsStreamingNote describes the chosen format, nothing when unknown', () => {
    assert.equal(ttsStreamingNote(PARTIAL, 'pcm'), 'Streaming: the audio starts while it is generated.')
    assert.equal(ttsStreamingNote(PARTIAL, 'mp3'), 'No streaming: the audio starts once its first part is generated.')
    const whole = { ...ELEVEN, whole_formats: ['mp3'] }
    assert.equal(ttsStreamingNote(whole, 'mp3'), 'No streaming: the audio starts once it is fully generated.')
    assert.equal(ttsStreamingNote(whole, 'pcm'), 'No streaming: the audio starts once its first part is generated.')
    assert.equal(ttsStreamingNote(UNKNOWN, 'mp3'), '')
})

// ── Script configs ───────────────────────────────────────────────

const scriptConfig = (id = 's', overrides = {}) => ({ ...createScriptConfig(id), command: 'piper --model x', ...overrides })

const validatedScript = (id = 's', overrides = {}) => {
    const config = scriptConfig(id, overrides)
    // The backend stores the snapshot without its kind.
    const { kind: _kind, ...snapshot } = speechFieldsOf(config)
    return { ...config, validated: snapshot }
}

test('script constants', () => {
    assert.deepEqual(SCRIPT_FIELDS, ['command', 'output', 'audio_format', 'pcm_rate', 'pcm_channels', 'max_chars'])
    assert.equal(MAX_PCM_RATE, 384000)
    assert.equal(MAX_PCM_CHANNELS, 8)
    assert.deepEqual(SCRIPT_OUTPUT_OPTIONS, [
        { value: 'stream', label: 'Audio stream' },
        { value: 'whole', label: 'Whole audio' },
        { value: 'path', label: 'File path' },
    ])
    assert.deepEqual(SCRIPT_FORMAT_OPTIONS, [
        { value: 'mp3', label: 'MP3' },
        { value: 'wav', label: 'WAV' },
        { value: 'pcm', label: 'PCM' },
    ])
})

test('kindOf is script only for a script kind', () => {
    assert.equal(kindOf({ kind: 'script' }), 'script')
    assert.equal(kindOf({ kind: 'openrouter' }), 'openrouter')
    assert.equal(kindOf({}), 'openrouter')
    assert.equal(kindOf({ kind: 'other' }), 'openrouter')
})

test('a new script config has the defaults and no validation', () => {
    assert.deepEqual(createScriptConfig('s'), {
        id: 's',
        kind: 'script',
        name: '',
        command: '',
        output: 'stream',
        audio_format: 'mp3',
        pcm_rate: null,
        pcm_channels: 1,
        max_chars: null,
        delete_file: true,
        note: '',
        validated: null,
    })
    assert.equal(isConfigValidated(createScriptConfig('s')), false)
})

test('delete_file is not a speech field: a change keeps the validation', () => {
    const script = validatedScript('s', { output: 'path' })
    assert.equal(SCRIPT_FIELDS.includes('delete_file'), false)
    assert.equal('delete_file' in speechFieldsOf(script), false)
    const patched = patchTtsConfig(script, { delete_file: false })
    assert.equal(patched.delete_file, false)
    assert.deepEqual(patched.validated, script.validated)
    assert.equal(isConfigValidated(patched), true)
})

test('speechFieldsOf keeps the kind and the fields of that kind', () => {
    const config = scriptConfig('s', { audio_format: 'pcm', pcm_rate: 22050, max_chars: 500, name: 'n', note: 'x' })
    assert.deepEqual(speechFieldsOf(config), {
        kind: 'script', command: 'piper --model x', output: 'stream', audio_format: 'pcm', pcm_rate: 22050, pcm_channels: 1, max_chars: 500,
    })
    // A stored OpenRouter config without a kind is an OpenRouter config.
    const { kind: _kind, ...legacy } = createTtsConfig('a')
    assert.equal(speechFieldsOf(legacy).kind, 'openrouter')
})

test('sameSpeechFields compares the fields of the kind of either argument', () => {
    // An OpenRouter snapshot without kind still validates.
    const openrouter = validatedConfig('a')
    const { kind: _kind, ...snapshot } = openrouter.validated
    assert.equal(isConfigValidated({ ...openrouter, validated: snapshot }), true)
    // A script snapshot without kind passed first (as shouldAskAfterTest does): compared on the script fields.
    const script = validatedScript('s')
    assert.equal(sameSpeechFields(script.validated, speechFieldsOf(script)), true)
    assert.equal(sameSpeechFields(script.validated, speechFieldsOf({ ...script, command: 'say' })), false)
    assert.equal(isConfigValidated(script), true)
    for (const field of SCRIPT_FIELDS) {
        assert.equal(sameSpeechFields(script, { ...script, [field]: 'changed' }), false, field)
    }
    assert.equal(sameSpeechFields(script, null), false)
    assert.equal(sameSpeechFields(undefined, script), false)
})

test('sameSpeechFields: a script config and an OpenRouter config are never the same', () => {
    const script = scriptConfig('s')
    const openrouter = validatedConfig('a')
    assert.equal(sameSpeechFields(script, openrouter), false)
    assert.equal(sameSpeechFields(openrouter, script), false)
    // Same field values, different kinds: still not the same.
    assert.equal(sameSpeechFields(speechFieldsOf(script), { ...speechFieldsOf(script), kind: 'openrouter' }), false)
    assert.equal(sameSpeechFields({ ...speechFieldsOf(openrouter), kind: 'script' }, speechFieldsOf(openrouter)), false)
})

test('the question after a script test follows the script fields', () => {
    const script = validatedScript('s')
    const tested = speechFieldsOf(script)
    assert.equal(shouldAskAfterTest(script, tested), false)
    const edited = { ...script, command: 'say' }
    assert.equal(shouldAskAfterTest(edited, speechFieldsOf(edited)), true)
})

test('a script field edit drops the validation, a rename keeps it', () => {
    const script = validatedScript('s')
    assert.equal(patchTtsConfig(script, { command: 'say' }).validated, null)
    assert.equal(patchTtsConfig(script, { pcm_channels: 2 }).validated, null)
    assert.equal(isConfigValidated(patchTtsConfig(script, { name: 'Piper' })), true)
    assert.equal(isConfigValidated(patchTtsConfig(script, { note: 'calm' })), true)
})

test('a duplicate keeps the kind and the script fields', () => {
    const script = validatedScript('s', { audio_format: 'wav', max_chars: 400, name: 'Piper' })
    const copy = duplicateTtsConfig(script, 't')
    assert.equal(copy.kind, 'script')
    assert.equal(copy.name, 'Piper (copy)')
    assert.deepEqual(speechFieldsOf(copy), speechFieldsOf(script))
    assert.equal(isConfigValidated(copy), true)
})

test('a validated script config is available without the key', () => {
    const configs = [validatedScript('s'), scriptConfig('u'), validatedConfig('a')]
    assert.equal(isTtsAvailable({ keyPresent: false, configs, activeId: 's' }), true)
    assert.equal(isTtsAvailable({ keyPresent: true, configs, activeId: 's' }), true)
    assert.equal(isTtsAvailable({ keyPresent: false, configs, activeId: 'u' }), false)
    assert.equal(isTtsAvailable({ keyPresent: false, configs, activeId: 'a' }), false)
    assert.equal(isTtsAvailable({ keyPresent: false, configs, activeId: null }), false)
})

test('the script auto name is built from the program', () => {
    assert.equal(ttsConfigAutoName(scriptConfig('s', { command: 'piper --model x' })), 'Script · piper')
    assert.equal(ttsConfigAutoName(scriptConfig('s', { command: '/usr/local/bin/say -v x' })), 'Script · say')
    assert.equal(ttsConfigAutoName(scriptConfig('s', { command: '' })), 'Unnamed script')
    assert.equal(ttsConfigAutoName(scriptConfig('s', { command: '   ' })), 'Unnamed script')
    assert.equal(ttsConfigDisplayName(scriptConfig('s', { name: 'Mine' })), 'Mine')
    assert.equal(ttsConfigDisplayName(scriptConfig('s', { command: '  espeak-ng\t-v fr' })), 'Script · espeak-ng')
})

test('the script streaming note follows the output kind and the max characters', () => {
    const streaming = 'Streaming: the audio starts while it is generated.'
    const firstPart = 'No streaming: the audio starts once its first part is generated.'
    const whole = 'No streaming: the audio starts once it is fully generated.'
    assert.equal(scriptStreamingNote(scriptConfig('s', { output: 'stream' })), streaming)
    assert.equal(scriptStreamingNote(scriptConfig('s', { output: 'stream', max_chars: 500 })), streaming)
    assert.equal(scriptStreamingNote(scriptConfig('s', { output: 'whole', max_chars: 500 })), firstPart)
    assert.equal(scriptStreamingNote(scriptConfig('s', { output: 'path', max_chars: 500 })), firstPart)
    assert.equal(scriptStreamingNote(scriptConfig('s', { output: 'whole' })), whole)
    assert.equal(scriptStreamingNote(scriptConfig('s', { output: 'path' })), whole)
})

test('the requested format: MP3 stays MP3, WAV and PCM reach the browser as PCM', () => {
    assert.equal(requestedFormat(speechFieldsOf(scriptConfig('s', { audio_format: 'mp3' }))), 'mp3')
    assert.equal(requestedFormat(speechFieldsOf(scriptConfig('s', { audio_format: 'wav' }))), 'pcm')
    assert.equal(requestedFormat(speechFieldsOf(scriptConfig('s', { audio_format: 'pcm', pcm_rate: 22050 }))), 'pcm')
    assert.equal(requestedFormat(speechFieldsOf({ ...createTtsConfig('a'), response_format: 'pcm' })), 'pcm')
    assert.equal(requestedFormat({ ...createTtsConfig('a'), response_format: 'mp3' }), 'mp3')
})

test('a script can be tested with a command, and a sample rate for PCM', () => {
    assert.equal(canTestScript(scriptConfig('s')), true)
    assert.equal(canTestScript(scriptConfig('s', { command: '' })), false)
    assert.equal(canTestScript(scriptConfig('s', { command: '   ' })), false)
    assert.equal(canTestScript(scriptConfig('s', { audio_format: 'pcm' })), false)
    assert.equal(canTestScript(scriptConfig('s', { audio_format: 'pcm', pcm_rate: 22050 })), true)
    assert.equal(canTestScript(scriptConfig('s', { audio_format: 'wav' })), true)
})

test('positiveIntegerInput: empty is null, a bounded integer is kept, the rest is refused', () => {
    assert.equal(positiveIntegerInput('', MAX_PCM_RATE), null)
    assert.equal(positiveIntegerInput('  ', MAX_PCM_RATE), null)
    assert.equal(positiveIntegerInput('22050', MAX_PCM_RATE), 22050)
    assert.equal(positiveIntegerInput(' 22050 ', MAX_PCM_RATE), 22050)
    assert.equal(positiveIntegerInput('384000', MAX_PCM_RATE), 384000)
    assert.equal(positiveIntegerInput('0', MAX_PCM_RATE), undefined)
    assert.equal(positiveIntegerInput('-3', MAX_PCM_RATE), undefined)
    assert.equal(positiveIntegerInput('1.5', MAX_PCM_RATE), undefined)
    assert.equal(positiveIntegerInput('abc', MAX_PCM_RATE), undefined)
    assert.equal(positiveIntegerInput('384001', MAX_PCM_RATE), undefined)
    assert.equal(positiveIntegerInput('9', MAX_PCM_CHANNELS), undefined)
})

test('stats and facts of an unpriced run have no cost part', () => {
    const run = { at: '2026-10-31T10:00:00+00:00', generation_id: '', chars: 2000, generation_ms: 2200, audio_seconds: 143, cost: null, first_audio_ms: 1160 }
    assert.equal(ttsTestStats(run, { priced: false }), '1.1 s / 1k ch · 1st audio 1.2 s')
    assert.equal(ttsLastTestFacts(run, { priced: false }), '2,000 chars · 2 min 23 s of audio · generated in 2.2 s · first audio after 1.2 s')
    // Even a recorded cost is not shown.
    assert.equal(ttsTestStats({ ...run, cost: 0.008 }, { priced: false }), '1.1 s / 1k ch · 1st audio 1.2 s')
    assert.equal(ttsLastTestFacts({ ...run, cost: 0.008 }, { priced: false }), '2,000 chars · 2 min 23 s of audio · generated in 2.2 s · first audio after 1.2 s')
    // Priced stays the default.
    assert.equal(ttsTestStats(run), 'price unknown · 1.1 s / 1k ch · 1st audio 1.2 s')
})

// ── Text adaptation ──────────────────────────────────────────────

test('each adaptation engine runs on its provider', () => {
    // Qwen runs on OpenRouter, not on a provider: it is not in the list.
    assert.deepEqual(ADAPT_PROVIDERS, { haiku: 'claude_code', luna: 'codex' })
})

test('normalizeAdapt keeps the four choices and drops anything else', () => {
    assert.equal(QWEN_ADAPT, 'qwen')
    for (const choice of ['haiku', 'luna', 'qwen', 'off']) assert.equal(normalizeAdapt(choice), choice)
    assert.equal(normalizeAdapt('opus'), null)
    assert.equal(normalizeAdapt(null), null)
})

test('the provider labels of the disabled options match the providers own labels', () => {
    // Read as text: the providers' helpers do not load under node --test.
    for (const provider of Object.values(ADAPT_PROVIDERS)) {
        const source = readFileSync(new URL(`../providers/${provider}/helpers.js`, import.meta.url), 'utf8')
        const label = source.match(/static label = '([^']+)'/)?.[1]
        assert.ok(label, `${provider} declares a static label`)
        assert.equal(ADAPT_PROVIDER_LABELS[provider], label)
    }
})

test('effectiveAdapt: no stored choice takes the first enabled provider, Claude then Codex', () => {
    assert.equal(effectiveAdapt(null, ['claude_code', 'codex']), 'haiku')
    assert.equal(effectiveAdapt(null, ['codex', 'claude_code']), 'haiku')
    assert.equal(effectiveAdapt(null, ['codex']), 'luna')
    assert.equal(effectiveAdapt(null, new Set(['codex'])), 'luna')
    assert.equal(effectiveAdapt(null, []), 'off')
    assert.equal(effectiveAdapt(undefined, undefined), 'off')
    // An unknown stored value is no stored choice (as the backend normalizes it).
    assert.equal(effectiveAdapt('opus', ['claude_code']), 'haiku')
})

test('effectiveAdapt: off stays off; a forced choice falls back to the other enabled provider', () => {
    assert.equal(effectiveAdapt('off', ['claude_code', 'codex']), 'off')
    assert.equal(effectiveAdapt('luna', ['claude_code', 'codex']), 'luna')
    assert.equal(effectiveAdapt('haiku', ['codex']), 'luna')
    assert.equal(effectiveAdapt('luna', new Set(['claude_code'])), 'haiku')
    assert.equal(effectiveAdapt('haiku', []), 'off')
    assert.equal(effectiveAdapt('luna', []), 'off')
})

test('effectiveAdapt: Qwen runs with the key, even with no provider; without the key it is no choice', () => {
    assert.equal(effectiveAdapt('qwen', ['claude_code'], true), 'qwen')
    assert.equal(effectiveAdapt('qwen', [], true), 'qwen')
    assert.equal(effectiveAdapt('qwen', ['claude_code', 'codex'], false), 'haiku')
    assert.equal(effectiveAdapt('qwen', ['codex']), 'luna')
    assert.equal(effectiveAdapt('qwen', [], false), 'off')
    // Never the default: the key alone does not choose Qwen.
    assert.equal(effectiveAdapt(null, ['claude_code'], true), 'haiku')
    assert.equal(effectiveAdapt(null, [], true), 'off')
})

test('adaptOptions: both models, Qwen, then Off; a model that cannot run stays shown, disabled', () => {
    assert.deepEqual(adaptOptions(['claude_code', 'codex']), [
        { value: 'haiku', label: 'Claude Haiku 5.5' },
        { value: 'luna', label: 'GPT-6 Luna' },
        { value: 'qwen', label: 'Qwen3.8 27B (OpenRouter)', disabled: true, title: 'Qwen3.8 27B (OpenRouter): the OpenRouter key is not set' },
        { value: 'off', label: 'Off' },
    ])
    assert.deepEqual(adaptOptions(new Set(['codex'])), [
        { value: 'haiku', label: 'Claude Haiku 5.5', disabled: true, title: 'Claude Haiku 5.5: Claude is disabled' },
        { value: 'luna', label: 'GPT-6 Luna' },
        { value: 'qwen', label: 'Qwen3.8 27B (OpenRouter)', disabled: true, title: 'Qwen3.8 27B (OpenRouter): the OpenRouter key is not set' },
        { value: 'off', label: 'Off' },
    ])
    assert.deepEqual(adaptOptions([]), [
        { value: 'haiku', label: 'Claude Haiku 5.5', disabled: true, title: 'Claude Haiku 5.5: Claude is disabled' },
        { value: 'luna', label: 'GPT-6 Luna', disabled: true, title: 'GPT-6 Luna: Codex is disabled' },
        { value: 'qwen', label: 'Qwen3.8 27B (OpenRouter)', disabled: true, title: 'Qwen3.8 27B (OpenRouter): the OpenRouter key is not set' },
        { value: 'off', label: 'Off' },
    ])
})

test('adaptOptions: Qwen is enabled once the OpenRouter key is set', () => {
    const options = adaptOptions(['claude_code', 'codex'], true)
    assert.deepEqual(options.map(option => option.value), ['haiku', 'luna', 'qwen', 'off'])
    assert.deepEqual(options[2], { value: 'qwen', label: 'Qwen3.8 27B (OpenRouter)' })
    assert.deepEqual(adaptOptions([], true)[2], { value: 'qwen', label: 'Qwen3.8 27B (OpenRouter)' })
})

test('adaptHint: the OpenRouter note for Qwen, the no-provider note when Off comes from no provider', () => {
    assert.equal(adaptHint('qwen', []), 'openrouter')
    assert.equal(adaptHint('qwen', ['claude_code']), 'openrouter')
    assert.equal(adaptHint('off', []), 'no_provider')
    assert.equal(adaptHint('off', undefined), 'no_provider')
    assert.equal(adaptHint('haiku', ['claude_code']), null)
    assert.equal(adaptHint('luna', new Set(['codex'])), null)
    // Off chosen by the user, with a provider enabled.
    assert.equal(adaptHint('off', ['claude_code']), null)
})

test('ttsAdaptationFacts: the engine and the wait for the first text', () => {
    const run = { chars: 2000, adapted_by: 'haiku', adapt_reason: null, adapt_first_ms: 2140, adapted_text: 'Hi' }
    assert.equal(ttsAdaptationFacts(run), 'Adapted by Claude Haiku 5.5 · first text 2.1 s')
    assert.equal(ttsAdaptationFacts({ ...run, adapted_by: 'luna', adapt_first_ms: 12400 }), 'Adapted by GPT-6 Luna · first text 12 s')
    // Served from the cache: no wait to show.
    assert.equal(ttsAdaptationFacts({ ...run, adapt_first_ms: null }), 'Adapted by Claude Haiku 5.5')
    assert.equal(
        ttsAdaptationFacts({ adapted_by: 'qwen', adapt_first_ms: 300 }),
        'Adapted by Qwen3.8 27B (OpenRouter) · first text 0.3 s',
    )
})

test('ttsAdaptationFacts: why Qwen failed before an agent took over', () => {
    assert.equal(
        ttsAdaptationFacts({ adapted_by: 'haiku', adapt_first_ms: 6200, fallback_reason: 'stalled' }),
        'Adapted by Claude Haiku 5.5 · first text 6.2 s · Qwen3.8 27B (OpenRouter) failed: the model did not answer in time',
    )
    assert.equal(
        ttsAdaptationFacts({ adapted_by: null, adapt_reason: 'failed', fallback_reason: 'refused' }),
        'Not adapted: the model failed · Qwen3.8 27B (OpenRouter) failed: OpenRouter refused the call (check the key and the credits)',
    )
    // An unknown reason adds nothing.
    assert.equal(ttsAdaptationFacts({ adapted_by: 'luna', adapt_first_ms: null, fallback_reason: 'unknown' }), 'Adapted by GPT-6 Luna')
})

test('ttsAdaptationFacts: why the text was not adapted', () => {
    const reasons = {
        off: 'the option is off',
        no_provider: 'no Claude or Codex provider is running',
        too_long: 'the text is too long',
        failed: 'the model failed',
        stalled: 'the model did not answer in time',
        empty: 'the model gave no text',
        refused: 'OpenRouter refused the call (check the key and the credits)',
    }
    for (const [reason, text] of Object.entries(reasons)) {
        const run = { chars: 35, adapted_by: null, adapt_reason: reason, adapt_first_ms: null, adapted_text: null }
        assert.equal(ttsAdaptationFacts(run), `Not adapted: ${text}`)
    }
})

test('ttsAdaptationFacts: nothing for a record without adaptation fields', () => {
    assert.equal(ttsAdaptationFacts(null), '')
    assert.equal(ttsAdaptationFacts(undefined), '')
    assert.equal(ttsAdaptationFacts({ chars: 35, generation_ms: 900 }), '')
    assert.equal(ttsAdaptationFacts({ chars: 35, adapted_by: null, adapt_reason: null, adapt_first_ms: null, adapted_text: null }), '')
    assert.equal(ttsAdaptationFacts({ chars: 35, adapted_by: null, adapt_reason: 'unknown' }), '')
})
