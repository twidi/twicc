<script setup>
import { useI18n } from 'vue-i18n'
const { t, locale } = useI18n()
/**
 * TextToSpeechSettings - the "Text to Speech" settings section: the OpenRouter key status,
 * the shared test text, and the configs (synced via <data_dir>/tts.json). Each config is a
 * collapsible card whose summary line shows its name, its marks, a quick Test (play / stop)
 * and the Activate button. The test sentence is saved in tts.json too.
 *
 * A config has a kind: an OpenRouter config (model, voice, format) or a script config (the
 * user's shell command gives the audio; it needs no key and has no price).
 *
 * A config is validated by ear: Test reads the test text aloud with the config's current
 * speech fields, then asks "Did you hear it correctly?" (not when those fields are already
 * the validated ones). Yes validates exactly the tested fields; any later speech field
 * change drops the validation (store `updateConfig`). A question or an error opens the card.
 *
 * Above the configs, the global "Adapt the text for speech" choice (tts.json `adapt`) shows the
 * adaptation that really runs, given the enabled providers and the OpenRouter key, as the Titles
 * setting does. With the key set, its hints always say which model OpenRouter uses and where it
 * sends the text.
 */
import { computed, onMounted, ref, watch } from 'vue'
import { resolveUiMessage } from '../../i18n/uiMessages.js'
import { useSettingsStore } from '../../stores/settings'
import { useTextToSpeechStore } from '../../stores/textToSpeech'
import {
    DEFAULT_TEST_TEXT,
    adaptHint,
    adaptOptions,
    effectiveAdapt,
    MAX_PCM_CHANNELS,
    MAX_PCM_RATE,
    SCRIPT_FORMAT_OPTIONS,
    SCRIPT_OUTPUT_OPTIONS,
    canTestScript,
    isConfigValidated,
    kindOf,
    positiveIntegerInput,
    scriptStreamingNote,
    openRouterModelUrl,
    sameSpeechFields,
    speechFieldsOf,
    ttsConfigAutoName,
    ttsConfigDisplayName,
    ttsAdaptationFacts,
    ttsLastTestFacts,
    ttsTestStats,
    shouldAskAfterTest,
    ttsModelOptions,
    ttsTestState,
    ttsVoiceOptions,
    voiceAfterModelChange,
    ttsModelFacts,
    ttsFormatOptions,
    formatAfterModelChange,
    ttsStreamingNote,
} from '../../utils/ttsConfig'
import HelpFeatureLink from '../help/HelpFeatureLink.vue'
import HelpIconButton from '../help/HelpIconButton.vue'
import FilterableSelect from '../ui/FilterableSelect.vue'
import SegmentedControl from '../ui/SegmentedControl.vue'

const store = useTextToSpeechStore()
const settingsStore = useSettingsStore()

// The text adaptation shows the choice that really runs: an engine that cannot run (its provider
// disabled, or OpenRouter without its key) shows disabled, with the effective one selected.
// The stored choice is never rewritten here.
const enabledProviders = computed(() => new Set(settingsStore.enabledProviders))
const adaptChoice = computed(() => effectiveAdapt(store.adapt, enabledProviders.value, store.keyPresent))
const adaptChoices = computed(() => adaptOptions(enabledProviders.value, store.keyPresent))
const adaptHints = computed(() => adaptHint(adaptChoice.value, enabledProviders.value, store.keyPresent))

// The test sentence as typed; saved in tts.json on change ('' there means the default one).
const testText = ref(store.testText || DEFAULT_TEST_TEXT)
const editingTestText = ref(false)
// Another device saved a sentence: take it, unless the user is typing their own here.
watch(() => store.testText, (stored) => {
    if (!editingTestText.value) testText.value = stored || DEFAULT_TEST_TEXT
})

// Transient per-config state, keyed by config id. Where a test stands lives in the store
// (`playback.configId`): it outlives this section, which unmounts on a section switch.
const testErrors = ref({}) // { [id]: string | UiMessage }
const prompts = ref({}) // { [id]: testedFields } -- "Did you hear it correctly?" is shown
const openIds = ref({}) // { [id]: true } -- the expanded cards

onMounted(() => {
    store.loadModels()
})

function lastTestWhen(lastTest) {
    return new Date(lastTest.at).toLocaleString(locale.value, { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' })
}

function testStateOf(config) {
    return ttsTestState(store.playback, config.id)
}

// One test request at a time: a loading test of any config holds the other Test buttons.
function testLoading() {
    return store.playback.source === 'test' && store.status === 'loading'
}

// A speech field edited after the test (here or on another device): the answer would no
// longer vouch for the current fields, so the prompt goes away.
watch(() => store.configs, (configs) => {
    for (const [id, testedFields] of Object.entries(prompts.value)) {
        const config = configs.find(entry => entry.id === id)
        if (!config || !sameSpeechFields(speechFieldsOf(config), testedFields)) delete prompts.value[id]
    }
}, { deep: true })

function modelOptionsFor(config) {
    return ttsModelOptions(store.models, config.model)
}

function voicesOf(modelId) {
    return store.models.find(model => model.id === modelId)?.voices ?? []
}

// Whether the config's model is in the loaded OpenRouter list (its voices are then known).
function isModelListed(config) {
    return store.models.some(model => model.id === config.model)
}

function voiceOptionsFor(config) {
    return ttsVoiceOptions(voicesOf(config.model), config.voice, { listed: isModelListed(config) })
}

// The voice is picked from the model's list only. A model whose list OpenRouter leaves empty
// (e.g. Fish Audio) gets no voice; a stored voice still shows, as its own option.
function hasVoiceChoice(config) {
    return voiceOptionsFor(config).length > 0
}

// What the open model list says when it is empty.
function modelsEmptyText() {
    if (store.modelsStatus === 'loading') return t('settings.textToSpeech.loadingModelsText')
    if (store.modelsStatus === 'failed') return t('settings.textToSpeech.modelsLoadError')
    return t('settings.textToSpeech.noMatch')
}

const retryingModels = ref(false)
async function retryModels() {
    if (retryingModels.value) return
    retryingModels.value = true
    await store.loadModels()
    retryingModels.value = false
}

// Why there is no voice to pick: the hint shown instead of the voice picker.
function noVoiceHint(config) {
    if (!config.model) return t('settings.textToSpeech.chooseModelFirst')
    if (isModelListed(config)) return t('settings.textToSpeech.noVoiceChoice')
    if (store.modelsStatus === 'loading') return t('settings.textToSpeech.loadingVoices')
    if (store.modelsStatus === 'failed') return t('settings.textToSpeech.voicesLoadError')
    return t('settings.textToSpeech.modelNotListed')
}

// A script config tests without the OpenRouter key.
function canTest(config) {
    const ready = kindOf(config) === 'script' ? canTestScript(config) : store.keyPresent && !!config.model
    return ready && !!testText.value.trim() && !testLoading()
}

// The icon before a config's name tells its kind apart.
const KIND_ICONS = {
    openrouter: { name: 'globe', label: 'settings.textToSpeech.kind.openrouter' },
    script: { name: 'terminal', label: 'settings.textToSpeech.kind.script' },
}

function kindIconOf(config) {
    const icon = KIND_ICONS[kindOf(config)]
    return { ...icon, label: t(icon.label) }
}

// A script config has no price: its last test shows no cost.
function isPriced(config) {
    return kindOf(config) !== 'script'
}

// The last test texts of each config, computed once per change (not twice per render).
const lastTestTexts = computed(() => Object.fromEntries(store.configs.map((config) => {
    const priced = isPriced(config)
    return [config.id, {
        stats: ttsTestStats(config.last_test, { priced }),
        facts: ttsLastTestFacts(config.last_test, { priced }),
        adaptation: ttsAdaptationFacts(config.last_test),
    }]
})))

function testStatsTitle(config) {
    return isPriced(config)
        ? t('settings.textToSpeech.pricedTestTitle')
        : t('settings.textToSpeech.scriptTestTitle')
}

function commit(id, patch) {
    store.updateConfig(id, patch)
}

function onNameChange(config, event) {
    const name = event.target.value.trim()
    event.target.value = name
    if (name !== config.name) commit(config.id, { name })
}

// TwiCC's known facts of the config's model (formats, streaming, length), or null when the
// model is not in the loaded list.
function modelFactsOf(config) {
    return ttsModelFacts(store.models, config.model)
}

function onModelSelect(config, model) {
    if (model === config.model) return
    const voice = voiceAfterModelChange(config.voice, voicesOf(model))
    // A format the new model refuses would fail every test: switch to one it accepts.
    const format = formatAfterModelChange(config.response_format, ttsModelFacts(store.models, model))
    const patch = format === config.response_format ? { model, voice } : { model, voice, response_format: format }
    commit(config.id, patch)
}

function onVoiceSelect(config, voice) {
    if (voice !== config.voice) commit(config.id, { voice })
}

function onFormatChange(config, format) {
    if (format !== config.response_format) commit(config.id, { response_format: format })
}

function onCommandChange(config, event) {
    const command = event.target.value.trim()
    event.target.value = command
    if (command !== config.command) commit(config.id, { command })
}

// Output and format of a script config.
function onScriptChoice(config, field, value) {
    if (value !== config[field]) commit(config.id, { [field]: value })
}

// File path only: whether TwiCC deletes the returned file after reading it.
function onDeleteFileChange(config, event) {
    if (event.target !== event.currentTarget) return
    const deleteFile = event.target.checked
    if (deleteFile !== config.delete_file) commit(config.id, { delete_file: deleteFile })
}

function integerText(value) {
    return value == null ? '' : String(value)
}

// A whole number within 1..max; empty clears it (the channels then go back to 1). Anything else
// keeps the stored value, and the input shows it again.
function onIntegerChange(config, field, max, event) {
    const input = event.target
    const parsed = positiveIntegerInput(input.value, max)
    const value = parsed === null && field === 'pcm_channels' ? 1 : parsed
    if (value === undefined || value === config[field]) {
        const stored = integerText(config[field])
        input.value = stored
        // The host value can already equal `stored`, so it would not re-render: reset the
        // native input too.
        if (input.input) input.input.value = stored
        return
    }
    input.value = integerText(value)
    commit(config.id, { [field]: value })
}

// Speed and instructions are not shown for now: a config keeps them (tts.json, speech
// requests), they are just not editable here.

function onNoteChange(config, event) {
    const note = event.target.value.trim()
    event.target.value = note
    if (note !== config.note) commit(config.id, { note })
}

function onTestTextInput(event) {
    editingTestText.value = true
    testText.value = event.target.value
}

// No `change` when the text ends as it was at focus: the blur ends the edit too, and takes
// what another device may have saved meanwhile.
function onTestTextBlur() {
    editingTestText.value = false
    testText.value = store.testText || DEFAULT_TEST_TEXT
}

function onTestTextChange(event) {
    editingTestText.value = false
    const text = event.target.value.trim()
    const stored = text === DEFAULT_TEST_TEXT ? '' : text
    testText.value = stored || DEFAULT_TEST_TEXT
    event.target.value = testText.value
    store.setTestText(stored)
}

// The quick test of the summary line: play, or stop what this config's test is doing.
function isTestBusy(config) {
    return testStateOf(config) !== 'idle'
}

function testIconOf(config) {
    return { loading: 'spinner', playing: 'stop', idle: 'play' }[testStateOf(config)]
}

function testButtonTitle(config) {
    if (isTestBusy(config)) return t('settings.textToSpeech.stopTest')
    if (canTest(config)) return t('settings.textToSpeech.testConfig')
    if (testLoading()) return t('settings.textToSpeech.otherTestLoading')
    if (kindOf(config) === 'script') return t('settings.textToSpeech.scriptTestRequirements')
    return t('settings.textToSpeech.testRequirements')
}

function onSummaryPlay(config) {
    if (isTestBusy(config)) store.stop()
    else testConfig(config)
}

// The card's own show/hide only: nested Web Awesome components bubble theirs.
function onCardToggle(id, open, event) {
    if (event.target !== event.currentTarget) return
    if (open) openIds.value[id] = true
    else delete openIds.value[id]
}

function addConfig(kind) {
    openIds.value[store.addConfig(kind)] = true
}

function duplicateConfig(config) {
    const id = store.duplicateConfig(config.id)
    if (id) openIds.value[id] = true
}

function deleteConfig(config) {
    delete testErrors.value[config.id]
    delete prompts.value[config.id]
    delete openIds.value[config.id]
    store.deleteConfig(config.id)
}

async function testConfig(config) {
    if (!canTest(config)) return
    const id = config.id
    delete testErrors.value[id]
    delete prompts.value[id]
    // Captured before the request: the prompt vouches only for these exact fields.
    const testedFields = speechFieldsOf(config)
    const result = await store.playWith(testText.value, testedFields, { configId: id })
    const latest = store.configs.find(entry => entry.id === id)
    if (!latest) return
    if (!result.ok) {
        if (!result.aborted) showTestError(id, result.uiMessage || result.error)
        return
    }
    // No question for a quick re-listen of validated fields, nor after an edit while loading.
    if (shouldAskAfterTest(latest, testedFields)) {
        prompts.value[id] = testedFields
        openIds.value[id] = true // the question lives in the card
    }
    // An error can still come after the sound started: it replaces a pending question. A Yes
    // given before it stands. A new test or a stop resolves this end ABORTED.
    const end = await result.ended
    if (end.ok || end.aborted) return
    if (!store.configs.some(entry => entry.id === id)) return
    delete prompts.value[id]
    showTestError(id, end.uiMessage || end.error)
}

function showTestError(id, error) {
    testErrors.value[id] = error
    openIds.value[id] = true // the error lives in the card
}

function answerYes(id) {
    store.markValidated(id, prompts.value[id])
    delete prompts.value[id]
}

function answerNo(id) {
    delete prompts.value[id]
}
</script>

<template>
    <section class="settings-section">
        <div class="settings-section-heading">
            <h3 class="settings-section-title">{{ $t('settings.sections.textToSpeech') }} <wa-icon name="cloud" class="synced-icon"></wa-icon></h3>
            <HelpFeatureLink help-key="text-to-speech" :label="$t('settings.textToSpeech.help')" />
        </div>

        <i18n-t tag="p" class="setting-group-hint tts-intro" keypath="settings.textToSpeech.intro" scope="global">
            <template #guide><a href="https://openrouter.ai/docs/guides/overview/multimodal/tts" target="_blank" rel="noopener">{{ $t('settings.textToSpeech.guide') }}</a></template>
            <template #models><a href="https://openrouter.ai/models?output_modalities=speech" target="_blank" rel="noopener">{{ $t('settings.textToSpeech.models') }}</a></template>
            <template #help><HelpIconButton help-key="openrouter" :label="$t('help.openrouter')" /></template>
        </i18n-t>

        <wa-callout v-if="!store.keyPresent" variant="warning" size="small">
            <wa-icon slot="icon" name="triangle-exclamation"></wa-icon>
            {{ $t('settings.textToSpeech.keyMissing') }}
            <HelpFeatureLink help-key="openrouter" :label="$t('help.openrouter')" />
        </wa-callout>

        <div class="setting-group tts-adapt-group">
            <span class="setting-group-label">{{ $t('settings.textToSpeech.adapt') }}</span>
            <div class="tts-format-row">
                <SegmentedControl
                    fit
                    :label="$t('settings.textToSpeech.adapt')"
                    :model-value="adaptChoice"
                    :options="adaptChoices"
                    @update:model-value="store.setAdapt($event)"
                />
            </div>
            <span v-if="adaptHints.noProvider" class="setting-group-hint">{{ $t('settings.textToSpeech.noProvider') }}</span>
            <span v-else class="setting-group-hint">{{ $t('settings.textToSpeech.adaptHint') }}</span>
            <HelpFeatureLink help-key="openrouter" :label="$t('help.openrouter')" />
        </div>

        <div class="setting-group">
            <label class="setting-group-label" for="tts-test-text">{{ $t('settings.textToSpeech.testText') }}</label>
            <wa-textarea
                id="tts-test-text"
                size="small"
                rows="3"
                resize="none"
                :value.prop="testText"
                @input="onTestTextInput"
                @change="onTestTextChange"
                @blur="onTestTextBlur"
            ></wa-textarea>
        </div>

        <div class="tts-configs">
            <wa-details
                v-for="config in store.configs"
                :key="config.id"
                class="tts-config"
                icon-placement="start"
                :open="!!openIds[config.id]"
                @wa-show="onCardToggle(config.id, true, $event)"
                @wa-hide="onCardToggle(config.id, false, $event)"
            >
                <div slot="summary" class="tts-summary">
                    <span class="tts-summary-text">
                        <span class="tts-summary-name"><wa-icon
                            class="tts-kind-icon"
                            :name="kindIconOf(config).name"
                            :label="kindIconOf(config).label"
                            :title="kindIconOf(config).label"
                        ></wa-icon>{{ ttsConfigDisplayName(config, store.models) }}</span>
                        <span v-if="lastTestTexts[config.id]?.stats" class="tts-summary-note" :title="testStatsTitle(config)">{{ lastTestTexts[config.id].stats }}</span>
                        <span v-if="config.note" class="tts-summary-note" :title="config.note">{{ config.note }}</span>
                    </span>
                    <wa-icon
                        v-if="isConfigValidated(config)"
                        class="tts-mark tts-mark-validated"
                        name="circle-check"
                        :label="$t('settings.textToSpeech.validated')"
                        :title="$t('settings.textToSpeech.validatedHint')"
                    ></wa-icon>
                    <wa-icon
                        v-else-if="store.activeId === config.id"
                        class="tts-mark tts-mark-warning"
                        name="triangle-exclamation"
                        :label="$t('settings.textToSpeech.changed')"
                        :title="$t('settings.textToSpeech.changed')"
                    ></wa-icon>
                    <!-- A native <summary> toggles on any click inside it, a button's included, and
                         on Enter / Space / arrows: the actions stop all of these here. Escape stays
                         free: it closes the Settings popover. -->
                    <span
                        class="tts-summary-actions"
                        @click.prevent.stop
                        @keydown.enter.stop
                        @keydown.space.stop
                        @keydown.up.stop
                        @keydown.down.stop
                        @keydown.left.stop
                        @keydown.right.stop
                    >
                        <!-- Radio-like: the filled dot marks the active config. -->
                        <wa-button
                            v-if="store.activeId === config.id"
                            class="tts-activation tts-activation-on"
                            size="small"
                            appearance="plain"
                            :title.attr="t('settings.textToSpeech.deactivateTitle')"
                            @click="store.deactivateConfig()"
                        >
                            <wa-icon name="circle-dot" :label="$t('settings.textToSpeech.deactivate')"></wa-icon>
                        </wa-button>
                        <wa-button
                            v-else
                            class="tts-activation"
                            size="small"
                            appearance="plain"
                            :disabled="!isConfigValidated(config)"
                            :title.attr="isConfigValidated(config) ? t('settings.textToSpeech.activateTitle') : t('settings.textToSpeech.validateFirst')"
                            @click="store.activateConfig(config.id)"
                        >
                            <wa-icon name="circle" variant="regular" :label="$t('settings.textToSpeech.activate')"></wa-icon>
                        </wa-button>
                        <!-- One icon in every state: the button keeps its icon-button size and its
                             accessible name (the icon label); loading turns a spinner icon. -->
                        <wa-button
                            size="small"
                            appearance="plain"
                            :disabled="!isTestBusy(config) && !canTest(config)"
                            :title.attr="testButtonTitle(config)"
                            @click="onSummaryPlay(config)"
                        >
                            <wa-icon
                                :name="testIconOf(config)"
                                :class="{ 'tts-icon-spinning': testStateOf(config) === 'loading' }"
                                :label="testButtonTitle(config)"
                            ></wa-icon>
                        </wa-button>
                    </span>
                </div>

                <div class="tts-config-body">
                    <div v-if="lastTestTexts[config.id]?.facts" class="tts-last-test">
                        <span class="setting-group-label">{{ $t('settings.textToSpeech.lastTest', { date: lastTestWhen(config.last_test) }) }}</span>
                        <span class="setting-group-hint">{{ lastTestTexts[config.id].facts }}</span>
                        <span v-if="lastTestTexts[config.id].adaptation" class="setting-group-hint">{{ lastTestTexts[config.id].adaptation }}</span>
                        <details v-if="config.last_test.adapted_text" class="tts-adapted-text">
                            <summary>{{ $t('settings.textToSpeech.adaptedText') }}</summary>
                            <div class="tts-adapted-text-body">{{ config.last_test.adapted_text }}</div>
                        </details>
                    </div>
                    <div class="tts-field">
                        <label class="setting-group-label" :for="`tts-${config.id}-name`">{{ $t('common.fields.name') }}</label>
                        <wa-input
                            :id="`tts-${config.id}-name`"
                            size="small"
                            :placeholder="ttsConfigAutoName(config, store.models)"
                            :value.prop="config.name"
                            @change="onNameChange(config, $event)"
                        ></wa-input>
                    </div>

                    <template v-if="kindOf(config) === 'script'">
                        <div class="tts-field">
                            <label class="setting-group-label" :for="`tts-${config.id}-command`">{{ $t('settings.textToSpeech.command') }}</label>
                            <wa-textarea
                                :id="`tts-${config.id}-command`"
                                class="tts-command"
                                size="small"
                                rows="2"
                                resize="auto"
                                :spellcheck="false"
                                :value.prop="config.command"
                                @change="onCommandChange(config, $event)"
                            ></wa-textarea>
                        </div>

                        <div class="tts-field">
                            <span class="setting-group-label">{{ $t('settings.textToSpeech.output') }}</span>
                            <div class="tts-format-row">
                                <SegmentedControl
                                    :label="$t('settings.textToSpeech.output')"
                                    :model-value="config.output"
                                    :options="SCRIPT_OUTPUT_OPTIONS"
                                    @update:model-value="onScriptChoice(config, 'output', $event)"
                                />
                                <span class="setting-group-hint">{{ scriptStreamingNote(config) }}</span>
                            </div>
                            <wa-checkbox
                                v-if="config.output === 'path'"
                                size="small"
                                :checked="config.delete_file"
                                @change="onDeleteFileChange(config, $event)"
                            >
                                {{ $t('settings.textToSpeech.deleteFile') }}
                            </wa-checkbox>
                        </div>

                        <div class="tts-field">
                            <span class="setting-group-label">{{ $t('settings.textToSpeech.format') }}</span>
                            <div class="tts-format-row">
                                <SegmentedControl
                                    :label="$t('settings.textToSpeech.format')"
                                    :model-value="config.audio_format"
                                    :options="SCRIPT_FORMAT_OPTIONS"
                                    @update:model-value="onScriptChoice(config, 'audio_format', $event)"
                                />
                            </div>
                        </div>

                        <!-- Raw PCM has no header: its sample rate and channels come from here. -->
                        <div v-if="config.audio_format === 'pcm'" class="tts-config-row">
                            <div class="tts-field tts-field-grow">
                                <label class="setting-group-label" :for="`tts-${config.id}-pcm-rate`">{{ $t('settings.textToSpeech.sampleRate') }}</label>
                                <wa-input
                                    :id="`tts-${config.id}-pcm-rate`"
                                    size="small"
                                    inputmode="numeric"
                                    :value.prop="integerText(config.pcm_rate)"
                                    @change="onIntegerChange(config, 'pcm_rate', MAX_PCM_RATE, $event)"
                                ></wa-input>
                            </div>
                            <div class="tts-field tts-field-grow">
                                <label class="setting-group-label" :for="`tts-${config.id}-pcm-channels`">{{ $t('settings.textToSpeech.channels') }}</label>
                                <wa-input
                                    :id="`tts-${config.id}-pcm-channels`"
                                    size="small"
                                    inputmode="numeric"
                                    :value.prop="integerText(config.pcm_channels)"
                                    @change="onIntegerChange(config, 'pcm_channels', MAX_PCM_CHANNELS, $event)"
                                ></wa-input>
                            </div>
                        </div>

                        <div class="tts-field">
                            <label class="setting-group-label" :for="`tts-${config.id}-max-chars`">{{ $t('settings.textToSpeech.maxCharacters') }}</label>
                            <wa-input
                                :id="`tts-${config.id}-max-chars`"
                                size="small"
                                inputmode="numeric"
                                :placeholder="$t('settings.textToSpeech.wholeText')"
                                :value.prop="integerText(config.max_chars)"
                                @change="onIntegerChange(config, 'max_chars', Number.MAX_SAFE_INTEGER, $event)"
                            ></wa-input>
                        </div>

                        <p class="setting-group-hint tts-script-help">
                            {{ $t('settings.textToSpeech.scriptHelp') }}
                        </p>
                    </template>
                    <template v-else>
                        <!-- Top-aligned: only the model label line carries the OpenRouter link. -->
                        <div class="tts-config-row tts-pickers">
                            <div class="tts-field tts-field-grow">
                                <div class="tts-label-row">
                                    <label class="setting-group-label" :for="`tts-${config.id}-model`">{{ $t('common.labels.model') }}</label>
                                    <a
                                        v-if="config.model"
                                        class="tts-model-link"
                                        :href="openRouterModelUrl(config.model)"
                                        target="_blank"
                                        rel="noopener"
                                    >{{ $t('settings.textToSpeech.viewModel') }} <wa-icon name="arrow-up-right-from-square"></wa-icon></a>
                                </div>
                                <FilterableSelect
                                    :input-id="`tts-${config.id}-model`"
                                    :label="$t('common.labels.model')"
                                    size="small"
                                    :placeholder="store.modelsStatus === 'loading' ? t('settings.textToSpeech.loadingModelsText') : t('settings.textToSpeech.chooseModel')"
                                    :empty-text="modelsEmptyText()"
                                    :model-value="config.model"
                                    :options="modelOptionsFor(config)"
                                    @update:model-value="onModelSelect(config, $event)"
                                />
                                <!-- One link through the retry: it keeps the keyboard focus. -->
                                <span v-if="store.modelsStatus === 'failed' || retryingModels" class="setting-group-hint">
                                    {{ retryingModels ? $t('settings.textToSpeech.loadingModels') : $t('settings.textToSpeech.modelsFailed') }}
                                    <a href="#" :aria-disabled="retryingModels" @click.prevent="retryModels">{{ retryingModels ? $t('settings.textToSpeech.retrying') : $t('common.actions.retry') }}</a>
                                </span>
                            </div>
                            <div class="tts-field tts-field-grow">
                                <label class="setting-group-label" :for="`tts-${config.id}-voice`">{{ $t('settings.textToSpeech.voice') }}</label>
                                <FilterableSelect
                                    v-if="hasVoiceChoice(config)"
                                    :input-id="`tts-${config.id}-voice`"
                                    :label="$t('settings.textToSpeech.voice')"
                                    size="small"
                                    :placeholder="$t('settings.textToSpeech.chooseVoice')"
                                    :model-value="config.voice"
                                    :options="voiceOptionsFor(config)"
                                    @update:model-value="onVoiceSelect(config, $event)"
                                />
                                <span v-else class="setting-group-hint">{{ noVoiceHint(config) }}</span>
                            </div>
                        </div>

                        <div class="tts-field">
                            <span class="setting-group-label">{{ $t('settings.textToSpeech.format') }}</span>
                            <div class="tts-format-row">
                                <SegmentedControl
                                    :label="$t('settings.textToSpeech.format')"
                                    :model-value="config.response_format"
                                    :options="ttsFormatOptions(modelFactsOf(config))"
                                    @update:model-value="onFormatChange(config, $event)"
                                />
                                <span v-if="ttsStreamingNote(modelFactsOf(config), config.response_format)" class="setting-group-hint">
                                    {{ ttsStreamingNote(modelFactsOf(config), config.response_format) }}
                                </span>
                            </div>
                        </div>
                    </template>

                    <div class="tts-field">
                        <label class="setting-group-label" :for="`tts-${config.id}-note`">{{ $t('settings.textToSpeech.note') }}</label>
                        <wa-textarea
                            :id="`tts-${config.id}-note`"
                            size="small"
                            rows="1"
                            resize="auto"
                            :placeholder="$t('settings.textToSpeech.notePlaceholder')"
                            :value.prop="config.note"
                            @change="onNoteChange(config, $event)"
                        ></wa-textarea>
                    </div>

                    <div class="tts-config-row tts-test-row">
                        <wa-button
                            size="small"
                            appearance="outlined"
                            :disabled="!canTest(config)"
                            @click="testConfig(config)"
                        >
                            <wa-spinner v-if="testStateOf(config) === 'loading'" slot="start"></wa-spinner>
                            <wa-icon v-else slot="start" name="volume-up"></wa-icon>
                            {{ $t('settings.textToSpeech.test') }}
                        </wa-button>
                        <div v-if="prompts[config.id]" class="tts-prompt">
                            <span>{{ $t('settings.textToSpeech.heardQuestion') }}</span>
                            <wa-button size="small" appearance="outlined" @click="answerYes(config.id)">{{ $t('settings.textToSpeech.yes') }}</wa-button>
                            <wa-button size="small" appearance="outlined" @click="answerNo(config.id)">{{ $t('settings.textToSpeech.no') }}</wa-button>
                        </div>
                        <div class="tts-config-actions">
                            <wa-button size="small" appearance="outlined" @click="duplicateConfig(config)">{{ $t('settings.textToSpeech.duplicate') }}</wa-button>
                            <wa-button
                                size="small"
                                appearance="plain"
                                variant="danger"
                                :title="$t('settings.textToSpeech.delete')"
                                @click="deleteConfig(config)"
                            >
                                <wa-icon name="trash" :label="$t('settings.textToSpeech.delete')"></wa-icon>
                            </wa-button>
                        </div>
                    </div>

                    <div v-if="testErrors[config.id]" class="tts-test-error">{{ resolveUiMessage(testErrors[config.id]) }}</div>

                    <wa-callout v-if="store.activeId === config.id && !isConfigValidated(config)" variant="warning" size="small">
                        <wa-icon slot="icon" name="triangle-exclamation"></wa-icon>
                        {{ $t('settings.textToSpeech.changedWarning') }}
                    </wa-callout>
                </div>
            </wa-details>
        </div>

        <div class="tts-new-configs">
            <wa-button size="small" appearance="outlined" @click="addConfig('openrouter')">
                <wa-icon slot="start" :name="KIND_ICONS.openrouter.name"></wa-icon> {{ $t('settings.textToSpeech.newOpenRouter') }}
            </wa-button>
            <wa-button size="small" appearance="outlined" @click="addConfig('script')">
                <wa-icon slot="start" :name="KIND_ICONS.script.name"></wa-icon> {{ $t('settings.textToSpeech.newScript') }}
            </wa-button>
        </div>
    </section>
</template>

<style scoped>
.synced-icon {
    color: var(--wa-color-brand);
}

/* The cards read as one list: no gap, shared borders, round corners on the ends only. */
.tts-configs {
    display: flex;
    flex-direction: column;
}

.tts-config {
    --spacing: var(--wa-space-s); /* the header and body padding: compact rows */
}

.tts-config::part(base) {
    border-radius: 0;
}

.tts-config + .tts-config {
    margin-top: calc(-1 * var(--wa-panel-border-width));
}

.tts-config:first-child::part(base) {
    border-start-start-radius: var(--wa-panel-border-radius);
    border-start-end-radius: var(--wa-panel-border-radius);
}

.tts-config:last-child::part(base) {
    border-end-start-radius: var(--wa-panel-border-radius);
    border-end-end-radius: var(--wa-panel-border-radius);
}

/* The summary slot is display: contents: the summary line itself takes the header's width,
   so the actions line up on the right of every card. */
.tts-summary {
    display: flex;
    align-items: center;
    gap: var(--wa-space-xs);
    flex: 1;
    width: 100%;
    min-width: 0;
}

/* The name, and the personal note on a small line under it. */
.tts-summary-text {
    display: flex;
    flex-direction: column;
    flex: 1;
    min-width: 0;
}

.tts-summary-name,
.tts-summary-note {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
}

.tts-summary-note {
    font-size: var(--wa-font-size-xs);
    color: var(--wa-color-text-quiet);
}

.tts-kind-icon {
    color: var(--wa-color-text-quiet);
    margin-inline-end: var(--wa-space-xs);
    vertical-align: -0.125em;
}

.tts-mark-validated {
    color: var(--wa-color-success-60);
}

.tts-mark-warning {
    color: var(--wa-color-warning-60);
}

/* Fixed icon buttons on the right: they line up from card to card. */
.tts-summary-actions {
    display: flex;
    align-items: center;
    flex: none;
}

.tts-activation-on {
    color: var(--wa-color-brand);
}

.tts-icon-spinning {
    animation: motion-spin 1s linear infinite; /* motion.css: a status indicator, kept under reduced motion */
}

.tts-config-body {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-s);
    /* A narrow card (a phone) tightens its segmented controls, see .tts-format-row. The
       adaptation group is the other container (.tts-adapt-group). */
    container-type: inline-size;
}

/* The section already spaces its blocks: the paragraph keeps no margin of its own. */
.settings-section :deep(.tts-intro) {
    margin-block: 0;
}

/* Its own container: on a phone the adaptation segments tighten as a card's do, then wrap. */
.tts-adapt-group {
    container-type: inline-size;
}

.tts-config-row {
    display: flex;
    align-items: flex-end;
    flex-wrap: wrap;
    column-gap: var(--wa-space-s);
    row-gap: var(--wa-space-xs);
}

.tts-field {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-2xs);
}

.tts-field-grow {
    flex: 1;
    min-width: 12rem;
}

.tts-config-actions {
    display: flex;
    align-items: center;
    gap: var(--wa-space-xs);
    margin-left: auto;
}

.tts-pickers {
    align-items: flex-start;
}

.tts-label-row {
    display: flex;
    align-items: baseline;
    justify-content: space-between;
    gap: var(--wa-space-xs);
}

.tts-last-test {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-3xs);
}

.tts-adapted-text {
    font-size: var(--wa-font-size-s);
}

.tts-adapted-text > summary {
    cursor: pointer;
    color: var(--wa-color-text-quiet);
}

/* The model's plain text, with its own line breaks; a long one scrolls inside the card. */
.tts-adapted-text-body {
    white-space: pre-wrap;
    overflow-wrap: anywhere;
    max-height: 12rem;
    overflow-y: auto;
    margin-top: var(--wa-space-2xs);
    padding: var(--wa-space-xs);
    border-radius: var(--wa-border-radius-s);
    background: var(--wa-color-surface-lowered);
}

.tts-model-link {
    font-size: var(--wa-font-size-xs);
}

.tts-format-row {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    column-gap: var(--wa-space-s);
    row-gap: var(--wa-space-2xs);
}

/* A segmented control keeps its content width and its one row of segments: the note wraps
   under it instead. */
.tts-format-row > .segmented-control {
    flex: none;
    max-width: 100%;
}

.tts-format-row :deep(wa-radio-group::part(form-control-input)) {
    flex-wrap: nowrap;
}

@container (max-width: 26rem) {
    .tts-format-row > .segmented-control {
        --wa-form-control-padding-inline: 0.5em;
    }
}

.tts-test-row {
    align-items: center;
}

.tts-prompt {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: var(--wa-space-xs);
    font-size: var(--wa-font-size-s);
}

.tts-test-error {
    font-size: var(--wa-font-size-xs);
    color: var(--wa-color-danger-60);
}

.tts-new-configs {
    display: flex;
    flex-wrap: wrap;
    gap: var(--wa-space-xs);
}

.tts-command::part(textarea) {
    font-family: var(--wa-font-family-code);
}

.tts-script-help {
    margin: 0;
}
</style>
