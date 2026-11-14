import test from 'node:test'
import assert from 'node:assert/strict'
import { existsSync, readFileSync } from 'node:fs'
import { createAppI18n } from '../../i18n/index.js'

const { t } = createAppI18n().global

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
    const showError = late.indexOf('showTestError(id, end.uiMessage || end.error)')
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
    assert.match(body, /:for="`tts-\$\{config\.id\}-pcm-rate`">\{\{ \$t\('settings\.textToSpeech\.sampleRate'\) \}\}<\/label>/)
    assert.match(body, /:for="`tts-\$\{config\.id\}-pcm-channels`">\{\{ \$t\('settings\.textToSpeech\.channels'\) \}\}<\/label>/)
    assert.match(body, /:for="`tts-\$\{config\.id\}-max-chars`">\{\{ \$t\('settings\.textToSpeech\.maxCharacters'\) \}\}<\/label>/)
    assert.match(body, /:placeholder="\$t\('settings\.textToSpeech\.wholeText'\)"/)
})

test('the script number inputs go through positiveIntegerInput with their bounds', () => {
    assert.match(settingsSource, /positiveIntegerInput\(input\.value, max\)/)
    assert.match(settingsSource, /'pcm_rate', MAX_PCM_RATE/)
    assert.match(settingsSource, /'pcm_channels', MAX_PCM_CHANNELS/)
    assert.match(settingsSource, /'max_chars', Number\.MAX_SAFE_INTEGER/)
})

test('the script help states the command contract', () => {
    const help = "The text goes to the command's standard input; the audio (or a file path) comes back on its standard output. The help at the top of this section tells the rest."
    assert.match(scriptBranchSource(), /<p class="setting-group-hint tts-script-help">\s*\{\{ \$t\('settings\.textToSpeech\.scriptHelp'\) \}\}\s*<\/p>/)
    assert.equal(t('settings.textToSpeech.scriptHelp'), help)
})

test('a File path script card offers to delete the returned file', () => {
    const body = scriptBranchSource()
    const flat = body.replace(/\s+/g, ' ')
    assert.match(flat, /<wa-checkbox v-if="config\.output === 'path'"[^>]*:checked="config\.delete_file"[^>]*@change="onDeleteFileChange\(config, \$event\)"[^>]*> \{\{ \$t\('settings\.textToSpeech\.deleteFile'\) \}\} <\/wa-checkbox>/)
    // Scoped to its own element: a bubbling change of a nested element is ignored.
    assert.match(
        settingsSource,
        /function onDeleteFileChange\(config, event\) \{\s*if \(event\.target !== event\.currentTarget\) return\s*const deleteFile = event\.target\.checked\s*if \(deleteFile !== config\.delete_file\) commit\(config\.id, \{ delete_file: deleteFile \}\)/,
    )
})

test('the intro names both kinds and the key callout points to the help page', () => {
    assert.match(flatSource, /<i18n-t tag="p" class="setting-group-hint tts-intro" keypath="settings\.textToSpeech\.intro" scope="global">/)
    assert.equal(t('settings.textToSpeech.intro', { guide: 'GUIDE', models: 'MODELS', help: 'HELP' }), 'Read the chat messages aloud with OpenRouter, or with your own script. See the GUIDE and the MODELS. HELP')
    // One warning, only while the key is missing; no path, no variable name: the help page says how.
    assert.match(
        flatSource,
        /<wa-callout v-if="!store\.keyPresent" variant="warning" size="small"> <wa-icon slot="icon" name="triangle-exclamation"><\/wa-icon> \{\{ \$t\('settings\.textToSpeech\.keyMissing'\) \}\} <HelpFeatureLink help-key="openrouter" :label="\$t\('help\.openrouter'\)" \/> <\/wa-callout>/,
    )
    assert.equal(t('settings.textToSpeech.keyMissing'), 'The OpenRouter key is not set. OpenRouter configs and the OpenRouter adaptation are disabled until then.')
    assert.doesNotMatch(flatSource, /keyLoaded|store\.envPath/)
})

test('two new-config buttons add an OpenRouter config and a script config', () => {
    assert.match(settingsSource, /@click="addConfig\('openrouter'\)"[\s\S]*?\{\{ \$t\('settings\.textToSpeech\.newOpenRouter'\) \}\}/)
    assert.match(settingsSource, /@click="addConfig\('script'\)"[\s\S]*?\{\{ \$t\('settings\.textToSpeech\.newScript'\) \}\}/)
    assert.match(settingsSource, /function addConfig\(kind\) \{\s*openIds\.value\[store\.addConfig\(kind\)\] = true/)
})

test('a script config tests without the key, with its own rule and title', () => {
    const body = canTestSource()
    assert.match(body, /kindOf\(config\) === 'script'/)
    assert.match(body, /canTestScript\(config\)/)
    assert.ok(settingsSource.includes("t('settings.textToSpeech.scriptTestRequirements')"))
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
    assert.ok(settingsSource.includes("t('settings.textToSpeech.scriptTestTitle')"))
})

test('an icon before the name tells an OpenRouter config from a script config', () => {
    assert.match(settingsSource, /openrouter: \{ name: 'globe', label: 'settings.textToSpeech.kind.openrouter' \}/)
    assert.match(settingsSource, /script: \{ name: 'terminal', label: 'settings.textToSpeech.kind.script' \}/)
    assert.match(settingsSource, /<span class="tts-summary-name"><wa-icon\s+class="tts-kind-icon"\s+:name="kindIconOf\(config\)\.name"/)
})

test('the new-config buttons carry the icon of their kind', () => {
    assert.match(settingsSource, /<wa-icon slot="start" :name="KIND_ICONS\.openrouter\.name"><\/wa-icon> \{\{ \$t\('settings\.textToSpeech\.newOpenRouter'\) \}\}/)
    assert.match(settingsSource, /<wa-icon slot="start" :name="KIND_ICONS\.script\.name"><\/wa-icon> \{\{ \$t\('settings\.textToSpeech\.newScript'\) \}\}/)
})

test('a segmented control keeps its content width and one row of segments', () => {
    assert.match(settingsSource, /\.tts-format-row > \.segmented-control \{\s*flex: none;/)
    assert.match(settingsSource, /:deep\(wa-radio-group::part\(form-control-input\)\) \{\s*flex-wrap: nowrap;/)
    // Every segmented control of a card sits in a format row.
    assert.equal(settingsSource.match(/<SegmentedControl/g).length, settingsSource.match(/<div class="tts-format-row">\s*<SegmentedControl/g).length)
})

test('the section opens its help page from the top, like the other sections', () => {
    assert.match(settingsSource, /<div class="settings-section-heading">\s*<h3 class="settings-section-title">\{\{ \$t\('settings\.sections\.textToSpeech'\) \}\}[\s\S]*?<\/h3>\s*<HelpFeatureLink help-key="text-to-speech" :label="\$t\('settings\.textToSpeech\.help'\)" \/>/)
    const top = settingsSource.slice(settingsSource.indexOf('settings-section-heading'))
    assert.ok(top.indexOf('HelpFeatureLink') < top.indexOf('keypath="settings.textToSpeech.intro"'), 'the link comes first')
    assert.ok(existsSync(new URL('../../../public/help/text-to-speech.md', import.meta.url)), 'the help page exists')
})

test('the text adaptation choice sits after the introduction, before the test text and the configs', () => {
    const template = settingsSource.slice(settingsSource.indexOf('<template>'))
    const intro = template.indexOf('keypath="settings.textToSpeech.intro"')
    const adapt = template.indexOf("{{ $t('settings.textToSpeech.adapt') }}")
    assert.ok(intro !== -1 && adapt > intro, 'the control follows the introduction')
    assert.ok(adapt < template.indexOf('for="tts-test-text"'), 'the control comes before the test text')
    assert.ok(adapt < template.indexOf('<div class="tts-configs">'), 'the control comes before the configs')
    // Its own container: the narrow-width rule of the segmented controls applies to it too.
    assert.match(flatSource, /<div class="setting-group tts-adapt-group"> <span class="setting-group-label">\{\{ \$t\('settings\.textToSpeech\.adapt'\) \}\}<\/span>/)
    assert.match(settingsSource, /\.tts-adapt-group \{\s*container-type: inline-size;\s*\}/)
    assert.match(settingsSource, /@container \(max-width: 26rem\) \{\s*\.tts-format-row > \.segmented-control \{/)
    assert.match(
        flatSource,
        /<div class="tts-format-row"> <SegmentedControl fit :label="\$t\('settings\.textToSpeech\.adapt'\)" :model-value="adaptChoice" :options="adaptChoices" @update:model-value="store\.setAdapt\(\$event\)" \/>/,
    )
})

test('the adaptation control shows what really runs, from the enabled providers and the OpenRouter key', () => {
    assert.match(settingsSource, /const enabledProviders = computed\(\(\) => new Set\(settingsStore\.enabledProviders\)\)/)
    assert.match(settingsSource, /const adaptChoice = computed\(\(\) => effectiveAdapt\(store\.adapt, enabledProviders\.value, store\.keyPresent\)\)/)
    assert.match(settingsSource, /const adaptChoices = computed\(\(\) => adaptOptions\(enabledProviders\.value, store\.keyPresent\)\)/)
    assert.match(settingsSource, /const adaptHints = computed\(\(\) => adaptHint\(adaptChoice\.value, enabledProviders\.value, store\.keyPresent\)\)/)
    assert.doesNotMatch(settingsSource, /noAdaptProvider/)
})

test('the adaptation hint or the no-provider hint instead, and a help icon for OpenRouter', () => {
    assert.match(
        flatSource,
        /<span v-if="adaptHints\.noProvider" class="setting-group-hint">\{\{ \$t\('settings\.textToSpeech\.noProvider'\) \}\}<\/span> <span v-else class="setting-group-hint">\{\{ \$t\('settings\.textToSpeech\.adaptHint'\) \}\}<\/span>/,
    )
    // The help page says which model OpenRouter uses and where the text goes.
    assert.match(flatSource, /\{\{ \$t\('settings\.textToSpeech\.adaptHint'\) \}\}<\/span> <HelpFeatureLink help-key="openrouter" :label="\$t\('help\.openrouter'\)" \/>/)
    assert.doesNotMatch(settingsSource, /openRouterAdaptHint|OPENROUTER_MODEL_DESCRIPTION_KEY/)
    assert.doesNotMatch(settingsSource, /Qwen|Cerebras/)
    assert.doesNotMatch(settingsSource, /adaptHintKind/)
})

test('the adaptation segments shrink to fit a narrow adaptation group, the config cards keep their one row', () => {
    // The format controls of the config cards keep their one row of segments.
    assert.match(settingsSource, /\.tts-format-row :deep\(wa-radio-group::part\(form-control-input\)\) \{\s*flex-wrap: nowrap;\s*\}/)
    // The adaptation control uses the fit mode of the segmented control: no wrapping rule of its own.
    assert.match(flatSource, /<SegmentedControl fit :label="\$t\('settings\.textToSpeech\.adapt'\)"/)
    assert.doesNotMatch(settingsSource, /\.tts-adapt-group \.tts-format-row :deep|grid-template-columns: 1fr 1fr/)
})

test('the last test run shows the adaptation facts and the folded adapted text', () => {
    assert.match(settingsSource, /adaptation: ttsAdaptationFacts\(config\.last_test\)/)
    const block = settingsSource.slice(settingsSource.indexOf('<div v-if="lastTestTexts[config.id]?.facts" class="tts-last-test">'))
    const end = block.indexOf('<div class="tts-field">')
    const lastTest = block.slice(0, end).replace(/\s+/g, ' ')
    assert.match(lastTest, /<span v-if="lastTestTexts\[config\.id\]\.adaptation" class="setting-group-hint">\{\{ lastTestTexts\[config\.id\]\.adaptation \}\}<\/span>/)
    assert.match(lastTest, /<details v-if="config\.last_test\.adapted_text" class="tts-adapted-text"> <summary>\{\{ \$t\('settings\.textToSpeech\.adaptedText'\) \}\}<\/summary> <div class="tts-adapted-text-body">\{\{ config\.last_test\.adapted_text \}\}<\/div> <\/details>/)
    assert.match(settingsSource, /\.tts-adapted-text-body \{[^}]*white-space: pre-wrap;/)
})

test('the help page explains the text adaptation', () => {
    const help = readFileSync(new URL('../../../public/help/text-to-speech.md', import.meta.url), 'utf8')
    assert.match(help, /^## Adapting the text$/m)
})
