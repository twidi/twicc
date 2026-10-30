import test from 'node:test'
import assert from 'node:assert/strict'
import { filterSelectOptions } from './filterableSelect.js'

const OPTIONS = [
    { value: 'hexgrad/kokoro-82m', label: 'Kokoro 82M', keywords: 'hexgrad/kokoro-82m' },
    { value: 'google/gemini-tts', label: 'Google: Gemini TTS', keywords: 'google/gemini-tts' },
    { value: 'main', label: 'main' },
]

test('an empty query shows every option in order', () => {
    assert.deepEqual(filterSelectOptions(OPTIONS, '  '), OPTIONS)
})

test('the query matches the label or the keywords, never across both', () => {
    assert.deepEqual(filterSelectOptions(OPTIONS, 'KOKORO').map(o => o.value), ['hexgrad/kokoro-82m'])
    assert.deepEqual(filterSelectOptions(OPTIONS, 'hexgrad').map(o => o.value), ['hexgrad/kokoro-82m'])
    assert.deepEqual(filterSelectOptions(OPTIONS, '82m hexgrad'), [])
})

test('a query that matches nothing gives no option', () => {
    assert.deepEqual(filterSelectOptions(OPTIONS, 'unknown/model'), [])
})
