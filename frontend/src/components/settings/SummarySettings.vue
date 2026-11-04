<script setup>
import { computed, onBeforeUnmount, ref, shallowRef } from 'vue'
import { useSettingsStore } from '../../stores/settings'
import { useTextToSpeechStore } from '../../stores/textToSpeech'
import { useSummariesStore } from '../../stores/summaries'
import { adaptOptions, effectiveAdapt, adaptHint } from '../../utils/ttsConfig'
import { OPENROUTER_MODEL_DESCRIPTION, TITLE_SUGGESTION_MODEL_LABELS } from '../../constants'
import SegmentedControl from '../ui/SegmentedControl.vue'
import SummaryContent from '../ui/SummaryContent.vue'

const settings = useSettingsStore()
const speech = useTextToSpeechStore()
const summaries = useSummariesStore()
const enabled = computed(() => new Set(settings.enabledProviders))
const effective = computed(() => effectiveAdapt(settings.summaryModel === 'auto' ? null : settings.summaryModel, enabled.value, speech.keyPresent))
const choices = computed(() => {
    const agent = effectiveAdapt(null, enabled.value, speech.keyPresent)
    const label = TITLE_SUGGESTION_MODEL_LABELS[agent]
    return [{ value: 'auto', label: label ? `Auto · ${label}` : 'Auto', disabled: agent === 'off' },
        ...adaptOptions(enabled.value, speech.keyPresent).filter(option => option.value !== 'off')]
})
const choice = computed(() => settings.summaryModel === 'auto' || effective.value === 'off' ? 'auto' : effective.value)
const hints = computed(() => adaptHint(effective.value, enabled.value, speech.keyPresent))
const testText = ref('')
const subscription = shallowRef(null)
function runTest() {
    const text = testText.value.trim()
    if (!text) return
    subscription.value?.release()
    subscription.value = summaries.request({ text, purpose: 'test' }, { manual: false })
}
function cancelTest() { subscription.value?.release() }
onBeforeUnmount(() => { subscription.value?.release() })
</script>

<template>
    <div class="summary-settings">
        <h3>Summaries</h3>
        <p>Messages need at least 1,000 content characters. Summaries target one third of the source, up to 140 words.</p>
        <label>Summary model</label>
        <SegmentedControl :model-value="choice" :options="choices" label="Summary model" @update:model-value="settings.setSummaryModel($event)" />
        <small v-if="hints.noProvider">No Claude or Codex provider is enabled. Set an OpenRouter key or enable a provider.</small>
        <small v-if="!speech.keyPresent">OpenRouter is unavailable: its key is not set. Set the key in Text to Speech settings.</small>
        <small v-if="hints.openrouter">OpenRouter uses {{ OPENROUTER_MODEL_DESCRIPTION }}. It sends the source to OpenRouter and uses your credits.</small>
        <wa-switch :checked="settings.timelineSummaryEnabled" @change="settings.setTimelineSummaryEnabled($event.target.checked)">
            Read summaries in timeline speech
        </wa-switch>
        <small>On by default. Manual summaries remain available when this is off.</small>
        <wa-textarea label="Temporary test text" :value="testText" :rows="5" @input="testText = $event.target.value"></wa-textarea>
        <wa-button size="small" :disabled="!testText.trim() || effective === 'off'" @click="runTest">Test summary</wa-button>
        <small>Test text and results are discarded when this section closes.</small>
        <SummaryContent v-if="subscription" :entry="subscription.entry" @cancel="cancelTest" @retry="runTest" />
    </div>
</template>

<style scoped>
.summary-settings { display: flex; flex-direction: column; gap: var(--wa-space-s); }
p { margin: 0; }
small { color: var(--wa-color-text-quiet); }
</style>
