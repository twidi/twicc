<script setup>
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
 */
import { computed, onMounted, ref, watch } from 'vue'
import { useTextToSpeechStore } from '../../stores/textToSpeech'
import {
    DEFAULT_TEST_TEXT,
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
import FilterableSelect from '../ui/FilterableSelect.vue'
import SegmentedControl from '../ui/SegmentedControl.vue'

const store = useTextToSpeechStore()

// The test sentence as typed; saved in tts.json on change ('' there means the default one).
const testText = ref(store.testText || DEFAULT_TEST_TEXT)
const editingTestText = ref(false)
// Another device saved a sentence: take it, unless the user is typing their own here.
watch(() => store.testText, (stored) => {
    if (!editingTestText.value) testText.value = stored || DEFAULT_TEST_TEXT
})

// Transient per-config state, keyed by config id. Where a test stands lives in the store
// (`playback.configId`): it outlives this section, which unmounts on a section switch.
const testErrors = ref({}) // { [id]: string }
const prompts = ref({}) // { [id]: testedFields } -- "Did you hear it correctly?" is shown
const openIds = ref({}) // { [id]: true } -- the expanded cards

onMounted(() => {
    store.loadModels()
})

function lastTestWhen(lastTest) {
    return new Date(lastTest.at).toLocaleString(undefined, { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' })
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
    if (store.modelsStatus === 'loading') return 'Loading the models...'
    if (store.modelsStatus === 'failed') return 'The model list did not load'
    return 'No match'
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
    if (!config.model) return 'Choose a model first.'
    if (isModelListed(config)) return 'No voice to choose for this model.'
    if (store.modelsStatus === 'loading') return 'Loading the voices...'
    if (store.modelsStatus === 'failed') return 'The voice list did not load.'
    return 'This model is not in the OpenRouter list.'
}

// A script config tests without the OpenRouter key.
function canTest(config) {
    const ready = kindOf(config) === 'script' ? canTestScript(config) : store.keyPresent && !!config.model
    return ready && !!testText.value.trim() && !testLoading()
}

// The icon before a config's name tells its kind apart.
const KIND_ICONS = {
    openrouter: { name: 'globe', label: 'OpenRouter config' },
    script: { name: 'terminal', label: 'Script config' },
}

function kindIconOf(config) {
    return KIND_ICONS[kindOf(config)]
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
    }]
})))

function testStatsTitle(config) {
    return isPriced(config)
        ? 'Last test run: cost per 1,000 characters, per minute of audio, generation time per 1,000 characters, and time to the first audio'
        : 'Last test run: generation time per 1,000 characters, and time to the first audio'
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
    if (isTestBusy(config)) return 'Stop the test'
    if (canTest(config)) return 'Test this config'
    if (testLoading()) return 'Another test is loading'
    if (kindOf(config) === 'script') return 'Test: needs a command, a sample rate for PCM, and a test text'
    return 'Test: needs the key, a model and a test text'
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
        if (!result.aborted) showTestError(id, result.error)
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
    showTestError(id, end.error)
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
        <h3 class="settings-section-title">Text to Speech <wa-icon name="cloud" class="synced-icon"></wa-icon></h3>

        <p class="setting-group-hint">
            Read the chat messages aloud with OpenRouter, or with your own script. See the
            <a href="https://openrouter.ai/docs/guides/overview/multimodal/tts" target="_blank" rel="noopener">OpenRouter TTS guide</a>
            and the <a href="https://openrouter.ai/models?output_modalities=speech" target="_blank" rel="noopener">speech models</a>.
            Prices are OpenRouter's list prices: the real cost can differ.
        </p>

        <wa-callout v-if="store.keyPresent" variant="success" size="small">
            <wa-icon slot="icon" name="circle-check"></wa-icon>
            <code>TWICC_OPENROUTER_API_KEY</code> is loaded. After you change it in
            <code>{{ store.envPath }}</code>, restart TwiCC.
        </wa-callout>
        <wa-callout v-else variant="warning" size="small">
            <wa-icon slot="icon" name="triangle-exclamation"></wa-icon>
            Add <code>TWICC_OPENROUTER_API_KEY</code> to <code>{{ store.envPath }}</code>,
            then restart TwiCC. Testing OpenRouter configs is disabled until then.
        </wa-callout>

        <div class="setting-group">
            <label class="setting-group-label" for="tts-test-text">Test text</label>
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
                        label="Validated"
                        title="Validated: tested and confirmed"
                    ></wa-icon>
                    <wa-icon
                        v-else-if="store.activeId === config.id"
                        class="tts-mark tts-mark-warning"
                        name="triangle-exclamation"
                        label="Changed since its last test"
                        title="Changed since its last test"
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
                            :title.attr="'Active: click to deactivate'"
                            @click="store.deactivateConfig()"
                        >
                            <wa-icon name="circle-dot" label="Deactivate"></wa-icon>
                        </wa-button>
                        <wa-button
                            v-else
                            class="tts-activation"
                            size="small"
                            appearance="plain"
                            :disabled="!isConfigValidated(config)"
                            :title.attr="isConfigValidated(config) ? 'Activate: use this config to read messages' : 'Test and confirm this config first'"
                            @click="store.activateConfig(config.id)"
                        >
                            <wa-icon name="circle" variant="regular" label="Activate"></wa-icon>
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
                        <span class="setting-group-label">Last test run · {{ lastTestWhen(config.last_test) }}</span>
                        <span class="setting-group-hint">{{ lastTestTexts[config.id].facts }}</span>
                    </div>
                    <div class="tts-field">
                        <label class="setting-group-label" :for="`tts-${config.id}-name`">Name</label>
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
                            <label class="setting-group-label" :for="`tts-${config.id}-command`">Command</label>
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
                            <span class="setting-group-label">Output</span>
                            <div class="tts-format-row">
                                <SegmentedControl
                                    label="Output"
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
                                Delete the file after reading
                            </wa-checkbox>
                        </div>

                        <div class="tts-field">
                            <span class="setting-group-label">Format</span>
                            <div class="tts-format-row">
                                <SegmentedControl
                                    label="Format"
                                    :model-value="config.audio_format"
                                    :options="SCRIPT_FORMAT_OPTIONS"
                                    @update:model-value="onScriptChoice(config, 'audio_format', $event)"
                                />
                            </div>
                        </div>

                        <!-- Raw PCM has no header: its sample rate and channels come from here. -->
                        <div v-if="config.audio_format === 'pcm'" class="tts-config-row">
                            <div class="tts-field tts-field-grow">
                                <label class="setting-group-label" :for="`tts-${config.id}-pcm-rate`">Sample rate (Hz)</label>
                                <wa-input
                                    :id="`tts-${config.id}-pcm-rate`"
                                    size="small"
                                    inputmode="numeric"
                                    :value.prop="integerText(config.pcm_rate)"
                                    @change="onIntegerChange(config, 'pcm_rate', MAX_PCM_RATE, $event)"
                                ></wa-input>
                            </div>
                            <div class="tts-field tts-field-grow">
                                <label class="setting-group-label" :for="`tts-${config.id}-pcm-channels`">Channels</label>
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
                            <label class="setting-group-label" :for="`tts-${config.id}-max-chars`">Max characters per run</label>
                            <wa-input
                                :id="`tts-${config.id}-max-chars`"
                                size="small"
                                inputmode="numeric"
                                placeholder="Whole text in one run"
                                :value.prop="integerText(config.max_chars)"
                                @change="onIntegerChange(config, 'max_chars', Number.MAX_SAFE_INTEGER, $event)"
                            ></wa-input>
                        </div>

                        <p class="setting-group-hint tts-script-help">
                            TwiCC runs this command in your shell, as a non-interactive login shell in your home
                            directory, and writes the text to its standard input. The command writes the audio to its
                            standard output (for File path: one absolute path, to a new file at each run, which TwiCC
                            deletes after reading unless Delete the file after reading is off) and exits with
                            status 0. If it fails, the last line of its error output is shown. What ~/.bashrc or
                            ~/.zshrc sets is usually missing, and nothing else may print to the standard output.
                            Several readings can run the command at the same time. With Whole audio or File path, set
                            a max characters per run: the audio then starts after the first part.
                        </p>
                    </template>
                    <template v-else>
                        <!-- Top-aligned: only the model label line carries the OpenRouter link. -->
                        <div class="tts-config-row tts-pickers">
                            <div class="tts-field tts-field-grow">
                                <div class="tts-label-row">
                                    <label class="setting-group-label" :for="`tts-${config.id}-model`">Model</label>
                                    <a
                                        v-if="config.model"
                                        class="tts-model-link"
                                        :href="openRouterModelUrl(config.model)"
                                        target="_blank"
                                        rel="noopener"
                                    >View on OpenRouter <wa-icon name="arrow-up-right-from-square"></wa-icon></a>
                                </div>
                                <FilterableSelect
                                    :input-id="`tts-${config.id}-model`"
                                    label="Model"
                                    size="small"
                                    :placeholder="store.modelsStatus === 'loading' ? 'Loading the models...' : 'Choose a model'"
                                    :empty-text="modelsEmptyText()"
                                    :model-value="config.model"
                                    :options="modelOptionsFor(config)"
                                    @update:model-value="onModelSelect(config, $event)"
                                />
                                <!-- One link through the retry: it keeps the keyboard focus. -->
                                <span v-if="store.modelsStatus === 'failed' || retryingModels" class="setting-group-hint">
                                    {{ retryingModels ? 'Loading the OpenRouter model list...' : 'The OpenRouter model list did not load.' }}
                                    <a href="#" :aria-disabled="retryingModels" @click.prevent="retryModels">{{ retryingModels ? 'Retrying...' : 'Retry' }}</a>
                                </span>
                            </div>
                            <div class="tts-field tts-field-grow">
                                <label class="setting-group-label" :for="`tts-${config.id}-voice`">Voice</label>
                                <FilterableSelect
                                    v-if="hasVoiceChoice(config)"
                                    :input-id="`tts-${config.id}-voice`"
                                    label="Voice"
                                    size="small"
                                    placeholder="Choose a voice"
                                    :model-value="config.voice"
                                    :options="voiceOptionsFor(config)"
                                    @update:model-value="onVoiceSelect(config, $event)"
                                />
                                <span v-else class="setting-group-hint">{{ noVoiceHint(config) }}</span>
                            </div>
                        </div>

                        <div class="tts-field">
                            <span class="setting-group-label">Format</span>
                            <div class="tts-format-row">
                                <SegmentedControl
                                    label="Format"
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
                        <label class="setting-group-label" :for="`tts-${config.id}-note`">Personal note</label>
                        <wa-textarea
                            :id="`tts-${config.id}-note`"
                            size="small"
                            rows="1"
                            resize="auto"
                            placeholder="Optional notes about this choice, for you only"
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
                            Test
                        </wa-button>
                        <div v-if="prompts[config.id]" class="tts-prompt">
                            <span>Did you hear it correctly?</span>
                            <wa-button size="small" appearance="outlined" @click="answerYes(config.id)">Yes</wa-button>
                            <wa-button size="small" appearance="outlined" @click="answerNo(config.id)">No</wa-button>
                        </div>
                        <div class="tts-config-actions">
                            <wa-button size="small" appearance="outlined" @click="duplicateConfig(config)">Duplicate</wa-button>
                            <wa-button
                                size="small"
                                appearance="plain"
                                variant="danger"
                                title="Delete this config"
                                @click="deleteConfig(config)"
                            >
                                <wa-icon name="trash" label="Delete this config"></wa-icon>
                            </wa-button>
                        </div>
                    </div>

                    <div v-if="testErrors[config.id]" class="tts-test-error">{{ testErrors[config.id] }}</div>

                    <wa-callout v-if="store.activeId === config.id && !isConfigValidated(config)" variant="warning" size="small">
                        <wa-icon slot="icon" name="triangle-exclamation"></wa-icon>
                        This config changed since its last test: the read-aloud buttons are hidden until a new test is confirmed.
                    </wa-callout>
                </div>
            </wa-details>
        </div>

        <div class="tts-new-configs">
            <wa-button size="small" appearance="outlined" @click="addConfig('openrouter')">
                <wa-icon slot="start" :name="KIND_ICONS.openrouter.name"></wa-icon> New OpenRouter config
            </wa-button>
            <wa-button size="small" appearance="outlined" @click="addConfig('script')">
                <wa-icon slot="start" :name="KIND_ICONS.script.name"></wa-icon> New script config
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
    /* A narrow card (a phone) tightens its segmented controls, see .tts-format-row. */
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
