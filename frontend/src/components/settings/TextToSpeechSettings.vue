<script setup>
/**
 * TextToSpeechSettings - the "Text to Speech" settings section: the OpenRouter key status,
 * the shared test text, and the configs (synced via <data_dir>/tts.json), edited in place
 * like the Apprise targets of NotificationSettings.vue.
 *
 * A config is validated by ear: Test reads the test text aloud with the config's current
 * speech fields, then asks "Did you hear it correctly?". Yes validates exactly the tested
 * fields; any later speech field change drops the validation (store `updateConfig`).
 */
import { onMounted, ref, watch } from 'vue'
import { useTextToSpeechStore } from '../../stores/textToSpeech'
import { DEFAULT_TEST_TEXT, isConfigValidated, sameSpeechFields, speechFieldsOf } from '../../utils/ttsConfig'
import SegmentedControl from '../ui/SegmentedControl.vue'

const store = useTextToSpeechStore()

const FORMAT_OPTIONS = [{ value: 'mp3', label: 'MP3' }, { value: 'pcm', label: 'PCM' }]

// Local only, never persisted.
const testText = ref(DEFAULT_TEST_TEXT)

// Transient per-config state, keyed by config id.
const testingId = ref(null) // the config whose test is in flight (one test at a time)
const testErrors = ref({}) // { [id]: string }
const prompts = ref({}) // { [id]: testedFields } -- "Did you hear it correctly?" is shown
// The model as currently typed: inputs commit on `change` (blur/Enter), but the voice
// suggestions and the Test button react to the value before it is committed.
const liveModels = ref({})

onMounted(() => {
    store.loadModels()
})

// A speech field edited after the test (here or on another device): the answer would no
// longer vouch for the current fields, so the prompt goes away.
watch(() => store.configs, (configs) => {
    for (const [id, testedFields] of Object.entries(prompts.value)) {
        const config = configs.find(entry => entry.id === id)
        if (!config || !sameSpeechFields(speechFieldsOf(config), testedFields)) delete prompts.value[id]
    }
}, { deep: true })

function effectiveModel(config) {
    return liveModels.value[config.id] ?? config.model
}

function voicesFor(config) {
    const model = effectiveModel(config).trim()
    return store.models.find(entry => entry.id === model)?.voices ?? []
}

function canTest(config) {
    return store.keyPresent && !!effectiveModel(config).trim() && !!testText.value.trim() && testingId.value === null
}

function commit(id, patch) {
    store.updateConfig(id, patch)
}

function onNameChange(config, event) {
    const name = event.target.value.trim()
    event.target.value = name
    if (name !== config.name) commit(config.id, { name })
}

function onModelInput(config, event) {
    liveModels.value[config.id] = event.target.value
}

function onModelChange(config, event) {
    const model = event.target.value.trim()
    event.target.value = model
    delete liveModels.value[config.id]
    if (model !== config.model) commit(config.id, { model })
}

function onVoiceChange(config, event) {
    const voice = event.target.value.trim()
    event.target.value = voice
    if (voice !== config.voice) commit(config.id, { voice })
}

function onModelSuggestion(config, event) {
    const model = event.detail?.item?.value
    if (!model) return
    delete liveModels.value[config.id]
    if (model !== config.model) commit(config.id, { model })
}

function onVoiceSuggestion(config, event) {
    const voice = event.detail?.item?.value
    if (voice && voice !== config.voice) commit(config.id, { voice })
}

function onFormatChange(config, format) {
    if (format !== config.response_format) commit(config.id, { response_format: format })
}

function speedText(config) {
    return config.speed == null ? '' : String(config.speed)
}

function onSpeedChange(config, event) {
    const raw = event.target.value.trim()
    if (raw === '') {
        if (config.speed !== null) commit(config.id, { speed: null })
        return
    }
    const speed = Number(raw)
    if (!Number.isFinite(speed) || speed <= 0) {
        // Not a usable speed: back to the stored value.
        event.target.value = speedText(config)
        return
    }
    if (speed !== config.speed) commit(config.id, { speed })
}

function onInstructionsChange(config, event) {
    const instructions = event.target.value.trim()
    event.target.value = instructions
    if (instructions !== config.instructions) commit(config.id, { instructions })
}

function onTestTextInput(event) {
    testText.value = event.target.value
}

function forget(id) {
    delete testErrors.value[id]
    delete prompts.value[id]
    delete liveModels.value[id]
}

function deleteConfig(config) {
    forget(config.id)
    store.deleteConfig(config.id)
}

async function testConfig(config) {
    if (!canTest(config)) return
    const id = config.id
    // Commit a model typed but not yet committed, so the test reads the fields it validates.
    const live = liveModels.value[id]
    if (live !== undefined) {
        delete liveModels.value[id]
        if (live.trim() !== config.model) commit(id, { model: live.trim() })
    }
    delete testErrors.value[id]
    delete prompts.value[id]
    const current = store.configs.find(entry => entry.id === id)
    if (!current) return
    // Captured before the request: the prompt vouches only for these exact fields.
    const testedFields = speechFieldsOf(current)
    testingId.value = id
    const result = await store.playWith(testText.value, testedFields)
    testingId.value = null
    const latest = store.configs.find(entry => entry.id === id)
    if (!latest) return
    if (result.ok) {
        // A field edited while the request loaded: no prompt.
        if (sameSpeechFields(speechFieldsOf(latest), testedFields)) prompts.value[id] = testedFields
    } else if (!result.aborted) {
        testErrors.value[id] = result.error
    }
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

        <wa-callout v-if="store.keyPresent" variant="success" size="small">
            <wa-icon slot="icon" name="circle-check"></wa-icon>
            <code>TWICC_OPENROUTER_API_KEY</code> is loaded. After you change it in
            <code>{{ store.envPath }}</code>, restart TwiCC.
        </wa-callout>
        <wa-callout v-else variant="warning" size="small">
            <wa-icon slot="icon" name="triangle-exclamation"></wa-icon>
            Add <code>TWICC_OPENROUTER_API_KEY</code> to <code>{{ store.envPath }}</code>,
            then restart TwiCC. Testing is disabled until then.
        </wa-callout>

        <p class="setting-group-hint">
            Read the chat messages aloud with OpenRouter. See the
            <a href="https://openrouter.ai/docs/guides/overview/multimodal/tts" target="_blank" rel="noopener">OpenRouter TTS guide</a>
            and the <a href="https://openrouter.ai/models?output_modalities=speech" target="_blank" rel="noopener">speech models</a>.
        </p>

        <div class="setting-group">
            <label class="setting-group-label" for="tts-test-text">Test text</label>
            <wa-textarea
                id="tts-test-text"
                size="small"
                rows="2"
                resize="auto"
                :value.prop="testText"
                @input="onTestTextInput"
            ></wa-textarea>
        </div>

        <div class="tts-configs">
            <div v-for="config in store.configs" :key="config.id" class="tts-config">
                <!-- Row 1: name, marks, actions -->
                <div class="tts-config-row">
                    <div class="tts-field tts-field-grow">
                        <label class="setting-group-label" :for="`tts-${config.id}-name`">Name</label>
                        <wa-input
                            :id="`tts-${config.id}-name`"
                            size="small"
                            :value.prop="config.name"
                            @change="onNameChange(config, $event)"
                        ></wa-input>
                    </div>
                    <div class="tts-config-actions">
                        <wa-tag v-if="store.activeId === config.id" size="small">Active</wa-tag>
                        <wa-tag v-if="isConfigValidated(config)" variant="success" size="small">Validated</wa-tag>
                        <wa-button
                            v-if="store.activeId !== config.id"
                            size="small"
                            appearance="outlined"
                            :disabled="!isConfigValidated(config)"
                            @click="store.activateConfig(config.id)"
                        >Activate</wa-button>
                        <wa-button
                            v-else
                            size="small"
                            appearance="outlined"
                            @click="store.deactivateConfig()"
                        >Deactivate</wa-button>
                        <wa-button size="small" appearance="outlined" @click="store.duplicateConfig(config.id)">Duplicate</wa-button>
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

                <!-- Row 2: model and voice, free text with suggestions -->
                <div class="tts-config-row">
                    <div class="tts-field tts-field-grow">
                        <label class="setting-group-label" :for="`tts-${config.id}-model`">Model</label>
                        <div class="tts-suggest">
                            <wa-input
                                :id="`tts-${config.id}-model`"
                                class="tts-code-input"
                                size="small"
                                :value.prop="config.model"
                                @input="onModelInput(config, $event)"
                                @change="onModelChange(config, $event)"
                            ></wa-input>
                            <wa-dropdown placement="bottom-end" @wa-select="onModelSuggestion(config, $event)">
                                <wa-button
                                    slot="trigger"
                                    size="small"
                                    appearance="outlined"
                                    title="Suggested models"
                                    :disabled="!store.models.length"
                                >
                                    <wa-icon name="chevron-down" label="Suggested models"></wa-icon>
                                </wa-button>
                                <wa-dropdown-item v-for="model in store.models" :key="model.id" :value="model.id">{{ model.name }}</wa-dropdown-item>
                            </wa-dropdown>
                        </div>
                    </div>
                    <div class="tts-field tts-field-grow">
                        <label class="setting-group-label" :for="`tts-${config.id}-voice`">Voice</label>
                        <div class="tts-suggest">
                            <wa-input
                                :id="`tts-${config.id}-voice`"
                                class="tts-code-input"
                                size="small"
                                :value.prop="config.voice"
                                @change="onVoiceChange(config, $event)"
                            ></wa-input>
                            <wa-dropdown placement="bottom-end" @wa-select="onVoiceSuggestion(config, $event)">
                                <wa-button
                                    slot="trigger"
                                    size="small"
                                    appearance="outlined"
                                    title="Suggested voices"
                                    :disabled="!voicesFor(config).length"
                                >
                                    <wa-icon name="chevron-down" label="Suggested voices"></wa-icon>
                                </wa-button>
                                <wa-dropdown-item v-for="voice in voicesFor(config)" :key="voice" :value="voice">{{ voice }}</wa-dropdown-item>
                            </wa-dropdown>
                        </div>
                    </div>
                </div>

                <!-- Row 3: format, speed, instructions -->
                <div class="tts-config-row">
                    <div class="tts-field">
                        <span class="setting-group-label">Format</span>
                        <SegmentedControl
                            label="Format"
                            :model-value="config.response_format"
                            :options="FORMAT_OPTIONS"
                            @update:model-value="onFormatChange(config, $event)"
                        />
                    </div>
                    <div class="tts-field">
                        <label class="setting-group-label" :for="`tts-${config.id}-speed`">Speed</label>
                        <wa-input
                            :id="`tts-${config.id}-speed`"
                            class="tts-speed-input"
                            type="number"
                            step="0.1"
                            min="0.1"
                            size="small"
                            placeholder="Default"
                            :value.prop="speedText(config)"
                            @change="onSpeedChange(config, $event)"
                        ></wa-input>
                    </div>
                    <div class="tts-field tts-field-grow">
                        <label class="setting-group-label" :for="`tts-${config.id}-instructions`">Instructions</label>
                        <wa-textarea
                            :id="`tts-${config.id}-instructions`"
                            size="small"
                            rows="1"
                            resize="auto"
                            placeholder="Optional delivery instructions"
                            :value.prop="config.instructions"
                            @change="onInstructionsChange(config, $event)"
                        ></wa-textarea>
                    </div>
                </div>

                <!-- Row 4: test and confirmation -->
                <div class="tts-config-row tts-test-row">
                    <wa-button
                        size="small"
                        appearance="outlined"
                        :disabled="!canTest(config)"
                        @click="testConfig(config)"
                    >
                        <wa-spinner v-if="testingId === config.id" slot="start"></wa-spinner>
                        <wa-icon v-else slot="start" name="volume-up"></wa-icon>
                        Test
                    </wa-button>
                    <div v-if="prompts[config.id]" class="tts-prompt">
                        <span>Did you hear it correctly?</span>
                        <wa-button size="small" appearance="outlined" @click="answerYes(config.id)">Yes</wa-button>
                        <wa-button size="small" appearance="outlined" @click="answerNo(config.id)">No</wa-button>
                    </div>
                </div>

                <div v-if="testErrors[config.id]" class="tts-test-error">{{ testErrors[config.id] }}</div>

                <wa-callout v-if="store.activeId === config.id && !isConfigValidated(config)" variant="warning" size="small">
                    <wa-icon slot="icon" name="triangle-exclamation"></wa-icon>
                    This config changed since its last test: the read-aloud buttons are hidden until a new test is confirmed.
                </wa-callout>
            </div>
        </div>

        <wa-button class="tts-new-config" size="small" appearance="outlined" @click="store.addConfig()">
            <wa-icon slot="start" name="plus"></wa-icon> New config
        </wa-button>
    </section>
</template>

<style scoped>
.synced-icon {
    color: var(--wa-color-brand);
}

.tts-configs {
    /* Size container: the rows wrap on a narrow panel. */
    container-type: inline-size;
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-m);
}

.tts-config {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-s);
    padding: var(--wa-space-s);
    border: var(--wa-border-width-s) var(--wa-border-style) var(--wa-color-surface-border);
    border-radius: var(--wa-border-radius-m);
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
    min-width: 10rem;
}

.tts-config-actions {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: var(--wa-space-xs);
    margin-left: auto;
}

.tts-suggest {
    display: flex;
    align-items: center;
    gap: var(--wa-space-2xs);

    wa-input {
        flex: 1;
        min-width: 0;
    }
}

.tts-code-input {
    font-family: var(--wa-font-family-code);
}

.tts-speed-input {
    width: 6rem;
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

.tts-new-config {
    align-self: flex-start;
}
</style>
