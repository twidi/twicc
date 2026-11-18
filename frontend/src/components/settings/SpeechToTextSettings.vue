<script setup>
/**
 * Settings → Dictation (speech to text), synced across devices.
 *
 * The engine choice is shown with what THIS browser/instance can run, so a
 * phone without the Web Speech API, or an instance without the OpenRouter
 * key, explains itself. Design: docs/plans/2026-11-18-speech-to-text-design.md.
 */
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useSettingsStore } from '../../stores/settings'
import { useTextToSpeechStore } from '../../stores/textToSpeech'
import SegmentedControl from '../ui/SegmentedControl.vue'
import HelpFeatureLink from '../help/HelpFeatureLink.vue'
import {
    DEFAULT_STT_MODEL,
    canRecordAudio,
    getSpeechRecognition,
    resolveSttEngine,
} from '../../utils/speechToText.js'

const { t } = useI18n()
const settings = useSettingsStore()
const speech = useTextToSpeechStore()

const engineSetting = computed(() => settings.sttEngine || 'auto')
const caps = computed(() => ({
    browser: Boolean(getSpeechRecognition()),
    recorder: canRecordAudio(),
    keyPresent: Boolean(speech.keyPresent),
    scriptConfigured: Boolean((settings.sttScriptCommand || '').trim()),
    secureContext: globalThis.isSecureContext !== false,
}))
const effective = computed(() => resolveSttEngine(engineSetting.value, caps.value))

const engineOptions = computed(() => [
    { value: 'auto', label: t('settings.dictation.engines.auto') },
    { value: 'browser', label: t('settings.dictation.engines.browser'), title: caps.value.browser ? undefined : t('settings.dictation.notHere.browser') },
    { value: 'openrouter', label: t('settings.dictation.engines.openrouter'), title: caps.value.keyPresent ? undefined : t('settings.dictation.notHere.noKey') },
    { value: 'script', label: t('settings.dictation.engines.script') },
    { value: 'off', label: t('settings.dictation.engines.off') },
])

const effectiveLabel = computed(() => effective.value
    ? t(`settings.dictation.engines.${effective.value}`)
    : t('settings.dictation.unavailable'))

// Text fields commit on change (blur/Enter), not on every keystroke: each
// commit is a synced write broadcast to every client.
const model = ref(settings.sttModel || '')
const language = ref(settings.sttLanguage || '')
const command = ref(settings.sttScriptCommand || '')
watch(() => settings.sttModel, value => { model.value = value || '' })
watch(() => settings.sttLanguage, value => { language.value = value || '' })
watch(() => settings.sttScriptCommand, value => { command.value = value || '' })

const languageInvalid = ref(false)

function commitModel(event) {
    settings.setSttModel((event.target.value || '').trim())
}

function commitLanguage(event) {
    const value = (event.target.value || '').trim()
    languageInvalid.value = value !== '' && !/^[a-zA-Z]{2,3}(-[a-zA-Z0-9]{2,8})*$/.test(value)
    if (!languageInvalid.value) settings.setSttLanguage(value)
}

function commitCommand(event) {
    settings.setSttScriptCommand((event.target.value || '').trim())
}

const SCRIPT_EXAMPLE = 'ffmpeg -loglevel error -i "$TWICC_STT_FILE" -ar 16000 -ac 1 -f wav - | whisper-cli -m ~/models/ggml-large-v3-turbo.bin -l "${TWICC_STT_LANGUAGE:-auto}" -nt -np -f -'
</script>

<template>
    <div class="stt-settings">
        <h3>{{ $t('settings.sections.dictation') }}</h3>
        <p>{{ $t('settings.dictation.hint') }}</p>

        <label>{{ $t('settings.dictation.engine') }}</label>
        <div class="stt-engine-row">
            <SegmentedControl
                fit
                :model-value="engineSetting"
                :options="engineOptions"
                :label="$t('settings.dictation.engine')"
                @update:model-value="settings.setSttEngine($event)"
            />
        </div>
        <small>{{ $t('settings.dictation.effective', { engine: effectiveLabel }) }}</small>
        <small>{{ $t(`settings.dictation.engineHints.${engineSetting}`) }}</small>
        <small v-if="!caps.secureContext" class="stt-warning">{{ $t('settings.dictation.notHere.insecure') }}</small>
        <small v-else-if="!caps.browser && !caps.recorder" class="stt-warning">{{ $t('settings.dictation.notHere.nothing') }}</small>

        <label for="stt-language">{{ $t('settings.dictation.language') }}</label>
        <wa-input
            id="stt-language"
            size="small"
            :value.prop="language"
            :placeholder="$t('settings.dictation.languagePlaceholder')"
            @change="commitLanguage"
        ></wa-input>
        <small v-if="languageInvalid" class="stt-warning">{{ $t('settings.dictation.languageInvalid') }}</small>
        <small v-else>{{ $t('settings.dictation.languageHint') }}</small>

        <template v-if="engineSetting === 'openrouter' || engineSetting === 'auto'">
            <label for="stt-model">{{ $t('settings.dictation.model') }}</label>
            <wa-input
                id="stt-model"
                size="small"
                :value.prop="model"
                :placeholder="DEFAULT_STT_MODEL"
                @change="commitModel"
            ></wa-input>
            <small>{{ $t('settings.dictation.modelHint') }}</small>
            <small v-if="!speech.keyPresent">{{ $t('settings.dictation.notHere.noKey') }}</small>
            <HelpFeatureLink help-key="openrouter" :label="$t('help.openrouter')" />
        </template>

        <template v-if="engineSetting === 'script' || engineSetting === 'auto'">
            <label for="stt-command">{{ $t('settings.dictation.command') }}</label>
            <wa-textarea
                id="stt-command"
                size="small"
                rows="3"
                resize="vertical"
                :value.prop="command"
                :placeholder="SCRIPT_EXAMPLE"
                @change="commitCommand"
            ></wa-textarea>
            <small>{{ $t('settings.dictation.commandHint') }}</small>
        </template>

        <small class="stt-shortcut">{{ $t('settings.dictation.shortcut', { shortcut: 'Ctrl+Shift+Space' }) }}</small>
    </div>
</template>

<style scoped>
.stt-settings { display: flex; flex-direction: column; gap: var(--wa-space-s); }
.stt-settings > .help-feature-link { align-self: flex-start; }
.stt-engine-row { display: flex; flex-wrap: wrap; align-items: center; }
.stt-engine-row > .segmented-control { flex: none; max-width: 100%; }
p { margin: 0; }
small { color: var(--wa-color-text-quiet); }
.stt-warning { color: var(--wa-color-warning-on-quiet); }
.stt-shortcut { margin-top: var(--wa-space-s); }
</style>
