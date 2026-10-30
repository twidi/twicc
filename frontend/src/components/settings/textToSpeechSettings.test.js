import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

const settingsSource = readFileSync(new URL('./TextToSpeechSettings.vue', import.meta.url), 'utf8')

/** The body of `async function testConfig(config)`, up to the next top-level function. */
function testConfigSource() {
    const start = settingsSource.indexOf('async function testConfig(config)')
    assert.ok(start !== -1, 'testConfig exists')
    const end = settingsSource.indexOf('\nfunction ', start)
    return settingsSource.slice(start, end === -1 ? undefined : end)
}

test('a settings test waits for the end of the speech, after the question', () => {
    const body = testConfigSource()
    const question = body.indexOf('prompts.value[id] = testedFields')
    const ended = body.indexOf('const end = await result.ended')
    assert.ok(question !== -1 && ended > question, 'the end is awaited after the question is set')
})

test('a late error replaces the pending question and shows in the card', () => {
    const body = testConfigSource()
    const late = body.slice(body.indexOf('const end = await result.ended'))
    // An ok end, or a stop / a new test (ABORTED), shows nothing.
    assert.match(late, /if \(end\.ok \|\| end\.aborted\) return/)
    // A config deleted meanwhile shows nothing either.
    assert.match(late, /if \(!store\.configs\.some\(entry => entry\.id === id\)\) return/)
    const dropQuestion = late.indexOf('delete prompts.value[id]')
    const showError = late.indexOf('showTestError(id, end.error)')
    assert.ok(dropQuestion !== -1 && showError > dropQuestion, 'the question goes, then the error shows')
})

test('showTestError keeps the error and opens the card', () => {
    assert.match(
        settingsSource,
        /function showTestError\(id, error\) \{\s*testErrors\.value\[id\] = error\s*openIds\.value\[id\] = true/,
    )
})

test('the format switch follows the model facts and says whether the audio streams', () => {
    assert.match(settingsSource, /:options="ttsFormatOptions\(modelFactsOf\(config\)\)"/)
    assert.match(settingsSource, /ttsStreamingNote\(modelFactsOf\(config\), config\.response_format\)/)
    // A model change switches away from a format the new model refuses.
    assert.match(settingsSource, /formatAfterModelChange\(config\.response_format, ttsModelFacts\(store\.models, model\)\)/)
})

test('speed and instructions are not editable in the settings for now', () => {
    assert.doesNotMatch(settingsSource, /tts-\$\{config\.id\}-speed|tts-\$\{config\.id\}-instructions/)
    assert.doesNotMatch(settingsSource, />Speed<|>Instructions</)
})
