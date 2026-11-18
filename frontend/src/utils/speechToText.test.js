import test from 'node:test'
import assert from 'node:assert/strict'
import {
    formatElapsed,
    insertDictation,
    pickRecorderMimeType,
    recognitionLang,
    resolveSttEngine,
    sttUnavailableReason,
} from './speechToText.js'

const ALL = { browser: true, recorder: true, keyPresent: true, scriptConfigured: true, secureContext: true }

test('auto prefers the script, then OpenRouter, then the browser', () => {
    assert.equal(resolveSttEngine('auto', ALL), 'script')
    assert.equal(resolveSttEngine('auto', { ...ALL, scriptConfigured: false }), 'openrouter')
    assert.equal(resolveSttEngine('auto', { ...ALL, scriptConfigured: false, keyPresent: false }), 'browser')
    // iOS Safari without a key: the browser engine.
    assert.equal(resolveSttEngine(null, { ...ALL, scriptConfigured: false, keyPresent: false }), 'browser')
    // Firefox (no Web Speech API) without a server engine: nothing.
    assert.equal(resolveSttEngine('auto', { ...ALL, browser: false, keyPresent: false, scriptConfigured: false }), null)
})

test('an explicit engine never silently switches', () => {
    assert.equal(resolveSttEngine('browser', { ...ALL, browser: false }), null)
    assert.equal(resolveSttEngine('openrouter', { ...ALL, keyPresent: false }), null)
    assert.equal(resolveSttEngine('script', { ...ALL, recorder: false }), null)
    assert.equal(resolveSttEngine('off', ALL), null)
    assert.equal(sttUnavailableReason('openrouter', { ...ALL, keyPresent: false }), 'noKey')
    assert.equal(sttUnavailableReason('script', { ...ALL, recorder: false }), 'noRecorder')
    assert.equal(sttUnavailableReason('auto', { ...ALL, secureContext: false }), 'insecure')
})

test('dictated text is inserted with natural spacing', () => {
    assert.deepEqual(insertDictation('', 0, 0, ' hello '), { value: 'hello', cursor: 5 })
    assert.deepEqual(insertDictation('Say', 3, 3, 'hello'), { value: 'Say hello', cursor: 9 })
    assert.deepEqual(insertDictation('Say ', 4, 4, 'hello'), { value: 'Say hello', cursor: 9 })
    assert.deepEqual(insertDictation('ab', 1, 1, 'X'), { value: 'a X b', cursor: 3 })
    assert.deepEqual(insertDictation('end.', 3, 3, 'X'), { value: 'end X.', cursor: 5 })
    // A selection is replaced; empty text removes the range (partial result withdrawn).
    assert.deepEqual(insertDictation('one two three', 4, 7, 'TWO'), { value: 'one TWO three', cursor: 7 })
    assert.deepEqual(insertDictation('one partial', 3, 11, ''), { value: 'one', cursor: 3 })
})

test('recording format: the first supported, Safari falls back to MP4', () => {
    const chrome = { isTypeSupported: type => type.startsWith('audio/webm') }
    const safari = { isTypeSupported: type => type.startsWith('audio/mp4') }
    assert.equal(pickRecorderMimeType(chrome), 'audio/webm;codecs=opus')
    assert.equal(pickRecorderMimeType(safari), 'audio/mp4;codecs=mp4a.40.2')
    assert.equal(pickRecorderMimeType(undefined), '')
})

test('recognition language and timer formatting', () => {
    assert.equal(recognitionLang('', 'pt-BR'), 'pt-BR')
    assert.equal(recognitionLang('en', 'pt-BR'), 'en')
    assert.equal(formatElapsed(0), '0:00')
    assert.equal(formatElapsed(65.7), '1:05')
})
