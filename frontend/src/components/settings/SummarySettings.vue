<script setup>
import { computed } from 'vue'
import { useSettingsStore } from '../../stores/settings'
import { useTextToSpeechStore } from '../../stores/textToSpeech'
import { adaptOptions, effectiveAdapt, adaptHint } from '../../utils/ttsConfig'
import { OPENROUTER_MODEL_DESCRIPTION, TITLE_SUGGESTION_MODEL_LABELS } from '../../constants'
import SegmentedControl from '../ui/SegmentedControl.vue'

const settings = useSettingsStore()
const speech = useTextToSpeechStore()
const enabled = computed(() => new Set(settings.enabledProviders))
// The stored choice is never rewritten here: the control shows the engine that really runs, which is the
// first enabled agent while nothing was chosen ('auto').
const effective = computed(() => effectiveAdapt(settings.summaryModel === 'auto' ? null : settings.summaryModel, enabled.value, speech.keyPresent))
const choices = computed(() => adaptOptions(enabled.value, speech.keyPresent)
    .filter(option => option.value !== 'off')
    .map(option => option.value === 'openrouter' && !option.disabled
        ? { ...option, title: `${option.label}: ${OPENROUTER_MODEL_DESCRIPTION}` } : option))
const choice = computed(() => effective.value === 'off' ? '' : effective.value)
const hints = computed(() => adaptHint(effective.value, enabled.value, speech.keyPresent))
const displacedPreference = computed(() => settings.summaryModel !== 'auto' && settings.summaryModel !== effective.value
    ? choices.value.find(option => option.value === settings.summaryModel) : null)
</script>

<template>
    <div class="summary-settings">
        <h3>Summaries</h3>
        <p>Messages and selections need at least 500 characters. Summaries target one third of the source, up to 140 words.</p>
        <label>Summary model</label>
        <div class="summary-model-row">
            <SegmentedControl :model-value="choice" :options="choices" label="Summary model" @update:model-value="settings.setSummaryModel($event)" />
        </div>
        <small v-if="displacedPreference">
            Stored preference: {{ displacedPreference.label }}. {{ displacedPreference.title }}.
            Effective engine: {{ TITLE_SUGGESTION_MODEL_LABELS[effective] || 'Unavailable' }}.
            The stored preference returns when it is available.
        </small>
        <small v-if="hints.noProvider">No Claude or Codex provider is enabled. Set an OpenRouter key or enable a provider.</small>
        <small v-if="!speech.keyPresent">OpenRouter is unavailable: its key is not set. Set the key in Text to Speech settings.</small>
        <small v-if="hints.openrouter">OpenRouter uses {{ OPENROUTER_MODEL_DESCRIPTION }}. It sends the source to OpenRouter and uses your credits.</small>
        <wa-switch :checked="settings.timelineSummaryEnabled" @change="settings.setTimelineSummaryEnabled($event.target.checked)">
            Read summaries in timeline speech
        </wa-switch>
        <small>On by default. Manual summaries remain available when this is off.</small>
    </div>
</template>

<style scoped>
.summary-settings { display: flex; flex-direction: column; gap: var(--wa-space-s); }
/* The segmented control keeps its content width and its one row of segments, as in the speech settings. */
.summary-model-row { display: flex; flex-wrap: wrap; align-items: center; }
.summary-model-row > .segmented-control { flex: none; max-width: 100%; }
.summary-model-row :deep(wa-radio-group::part(form-control-input)) { flex-wrap: wrap; }
p { margin: 0; }
small { color: var(--wa-color-text-quiet); }
</style>
