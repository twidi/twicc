import test from 'node:test'
import assert from 'node:assert/strict'
import { existsSync, readFileSync } from 'node:fs'

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

// The source with its whitespace runs folded to one space: a copy can wrap in the template.
const flatSource = settingsSource.replace(/\s+/g, ' ')

/** The script branch of a card body, up to its `v-else` (the OpenRouter branch). */
function scriptBranchSource() {
    const start = settingsSource.indexOf(`<template v-if="kindOf(config) === 'script'">`)
    assert.ok(start !== -1, 'the card body branches on the script kind')
    const end = settingsSource.indexOf('<template v-else>', start)
    assert.ok(end !== -1, 'the OpenRouter branch follows')
    return settingsSource.slice(start, end)
}

/** The body of `function canTest(config)`, up to the next top-level function. */
function canTestSource() {
    const start = settingsSource.indexOf('function canTest(config)')
    assert.ok(start !== -1, 'canTest exists')
    const end = settingsSource.indexOf('\nfunction ', start)
    return settingsSource.slice(start, end === -1 ? undefined : end)
}

test('a script card shows its own fields, without model, voice or OpenRouter link', () => {
    const body = scriptBranchSource()
    assert.doesNotMatch(body, /FilterableSelect/)
    assert.doesNotMatch(body, /openRouterModelUrl/)
    assert.match(body, /:options="SCRIPT_OUTPUT_OPTIONS"/)
    assert.match(body, /:options="SCRIPT_FORMAT_OPTIONS"/)
    assert.match(body, /scriptStreamingNote\(config\)/)
    assert.match(body, /v-if="config\.audio_format === 'pcm'"/)
    assert.match(body, />Sample rate \(Hz\)</)
    assert.match(body, />Channels</)
    assert.match(body, />Max characters per run</)
    assert.match(body, /placeholder="Whole text in one run"/)
})

test('the script number inputs go through positiveIntegerInput with their bounds', () => {
    assert.match(settingsSource, /positiveIntegerInput\(input\.value, max\)/)
    assert.match(settingsSource, /'pcm_rate', MAX_PCM_RATE/)
    assert.match(settingsSource, /'pcm_channels', MAX_PCM_CHANNELS/)
    assert.match(settingsSource, /'max_chars', Number\.MAX_SAFE_INTEGER/)
})

test('the script help states the command contract', () => {
    const help = "The text goes to the command's standard input; the audio (or a file path) comes back on its standard output. The help at the top of this section tells the rest."
    assert.ok(scriptBranchSource().replace(/\s+/g, ' ').includes(help), 'the help copy is verbatim')
})

test('a File path script card offers to delete the returned file', () => {
    const body = scriptBranchSource()
    const flat = body.replace(/\s+/g, ' ')
    assert.match(flat, /<wa-checkbox v-if="config\.output === 'path'"[^>]*:checked="config\.delete_file"[^>]*@change="onDeleteFileChange\(config, \$event\)"[^>]*> Delete the file after reading <\/wa-checkbox>/)
    // Scoped to its own element: a bubbling change of a nested element is ignored.
    assert.match(
        settingsSource,
        /function onDeleteFileChange\(config, event\) \{\s*if \(event\.target !== event\.currentTarget\) return\s*const deleteFile = event\.target\.checked\s*if \(deleteFile !== config\.delete_file\) commit\(config\.id, \{ delete_file: deleteFile \}\)/,
    )
})

test('the intro names both kinds and the key callout speaks of OpenRouter configs only', () => {
    assert.ok(flatSource.includes('Read the chat messages aloud with OpenRouter, or with your own script. See the'))
    assert.ok(flatSource.includes(
        'Add <code>TWICC_OPENROUTER_API_KEY</code> to <code>{{ store.envPath }}</code>, then restart TwiCC. Testing OpenRouter configs is disabled until then.',
    ))
})

test('two new-config buttons add an OpenRouter config and a script config', () => {
    assert.match(settingsSource, /@click="addConfig\('openrouter'\)"[\s\S]*?New OpenRouter config/)
    assert.match(settingsSource, /@click="addConfig\('script'\)"[\s\S]*?New script config/)
    assert.match(settingsSource, /function addConfig\(kind\) \{\s*openIds\.value\[store\.addConfig\(kind\)\] = true/)
})

test('a script config tests without the key, with its own rule and title', () => {
    const body = canTestSource()
    assert.match(body, /kindOf\(config\) === 'script'/)
    assert.match(body, /canTestScript\(config\)/)
    assert.ok(settingsSource.includes("'Test: needs a command, a sample rate for PCM, and a test text'"))
})

test('the last test of a script config shows no price', () => {
    // Computed once per config, with the price rule of its kind.
    assert.match(settingsSource, /const priced = isPriced\(config\)/)
    assert.match(settingsSource, /stats: ttsTestStats\(config\.last_test, \{ priced \}\)/)
    assert.match(settingsSource, /facts: ttsLastTestFacts\(config\.last_test, \{ priced \}\)/)
    // The template reads the computed texts: it calls neither helper itself.
    const template = settingsSource.slice(settingsSource.indexOf('<template>'))
    assert.doesNotMatch(template, /ttsTestStats\(|ttsLastTestFacts\(/)
    assert.match(template, /lastTestTexts\[config\.id\]\.stats/)
    assert.match(template, /lastTestTexts\[config\.id\]\.facts/)
    assert.match(settingsSource, /function isPriced\(config\) \{\s*return kindOf\(config\) !== 'script'/)
    assert.ok(settingsSource.includes("'Last test run: generation time per 1,000 characters, and time to the first audio'"))
})

test('an icon before the name tells an OpenRouter config from a script config', () => {
    assert.match(settingsSource, /openrouter: \{ name: 'globe', label: 'OpenRouter config' \}/)
    assert.match(settingsSource, /script: \{ name: 'terminal', label: 'Script config' \}/)
    assert.match(settingsSource, /<span class="tts-summary-name"><wa-icon\s+class="tts-kind-icon"\s+:name="kindIconOf\(config\)\.name"/)
})

test('the new-config buttons carry the icon of their kind', () => {
    assert.match(settingsSource, /<wa-icon slot="start" :name="KIND_ICONS\.openrouter\.name"><\/wa-icon> New OpenRouter config/)
    assert.match(settingsSource, /<wa-icon slot="start" :name="KIND_ICONS\.script\.name"><\/wa-icon> New script config/)
})

test('a segmented control keeps its content width and one row of segments', () => {
    assert.match(settingsSource, /\.tts-format-row > \.segmented-control \{\s*flex: none;/)
    assert.match(settingsSource, /:deep\(wa-radio-group::part\(form-control-input\)\) \{\s*flex-wrap: nowrap;/)
    // Every segmented control of a card sits in a format row.
    assert.equal(settingsSource.match(/<SegmentedControl/g).length, settingsSource.match(/<div class="tts-format-row">\s*<SegmentedControl/g).length)
})

test('the section opens its help page from the top, like the other sections', () => {
    assert.match(settingsSource, /showHelp\('text-to-speech', \{ showDontShowAgain: false \}\)/)
    const top = settingsSource.slice(settingsSource.indexOf('settings-section-title">Text to Speech'))
    assert.ok(top.indexOf('View help') < top.indexOf('Read the chat messages aloud'), 'the link comes first')
    assert.ok(existsSync(new URL('../../../public/help/text-to-speech.md', import.meta.url)), 'the help page exists')
})
