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
        name: 'New voice',
        model: '',
        voice: '',
        response_format: 'mp3',
        speed: null,
        instructions: '',
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
    assert.equal(config.name, 'New voice')
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
})

test('duplicate keeps the validation and is named "(copy)"', () => {
    const config = { ...validatedConfig('a'), name: 'Kore' }
    const copy = duplicateTtsConfig(config, 'b')
    assert.equal(copy.id, 'b')
    assert.equal(copy.name, 'Kore (copy)')
    assert.deepEqual(speechFieldsOf(copy), speechFieldsOf(config))
    assert.equal(isConfigValidated(copy), true)
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
