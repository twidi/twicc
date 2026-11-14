<script setup>
import { useI18n } from 'vue-i18n'
const { t } = useI18n()
import { computed } from 'vue'
import { useSettingsStore } from '../../stores/settings'
import { useTextToSpeechStore } from '../../stores/textToSpeech'
import { adaptOptions, effectiveAdapt, adaptHint } from '../../utils/ttsConfig'
import { OPENROUTER_MODEL_DESCRIPTION_KEY, TITLE_SUGGESTION_MODEL_LABELS } from '../../constants'
import SegmentedControl from '../ui/SegmentedControl.vue'
import HelpFeatureLink from '../help/HelpFeatureLink.vue'

const settings = useSettingsStore()
const speech = useTextToSpeechStore()
const enabled = computed(() => new Set(settings.enabledProviders))
// The stored choice is never rewritten here: the control shows the engine that really runs, which is the
// first enabled agent while nothing was chosen ('auto').
const effective = computed(() => effectiveAdapt(settings.summaryModel === 'auto' ? null : settings.summaryModel, enabled.value, speech.keyPresent))
const choices = computed(() => adaptOptions(enabled.value, speech.keyPresent)
    .filter(option => option.value !== 'off')
    .map(option => option.value === 'openrouter' && !option.disabled
        ? { ...option, title: `${option.label}: ${t(OPENROUTER_MODEL_DESCRIPTION_KEY)}` } : option))
const choice = computed(() => effective.value === 'off' ? '' : effective.value)
const hints = computed(() => adaptHint(effective.value, enabled.value, speech.keyPresent))
const displacedPreference = computed(() => settings.summaryModel !== 'auto' && settings.summaryModel !== effective.value
    ? choices.value.find(option => option.value === settings.summaryModel) : null)
</script>

<template>
    <div class="summary-settings">
        <h3>{{ $t('settings.sections.summaries') }}</h3>
        <p>{{ $t('settings.summaries.hint') }}</p>
        <label>{{ $t('settings.summaries.model') }}</label>
        <div class="summary-model-row">
            <SegmentedControl fit :model-value="choice" :options="choices" :label="$t('settings.summaries.model')" @update:model-value="settings.setSummaryModel($event)" />
        </div>
        <HelpFeatureLink help-key="openrouter" :label="$t('help.openrouter')" />
        <small v-if="displacedPreference">
            {{ $t('settings.summaries.storedPreference', { preference: displacedPreference.label, reason: displacedPreference.title, engine: TITLE_SUGGESTION_MODEL_LABELS[effective] || $t('settings.summaries.unavailable') }) }}
        </small>
        <small v-if="hints.noProvider">{{ $t('settings.summaries.noProvider') }}</small>
        <small v-if="!speech.keyPresent">{{ $t('settings.summaries.noKey') }}</small>
        <wa-switch :checked="settings.timelineSummaryEnabled" @change="settings.setTimelineSummaryEnabled($event.target.checked)">
            {{ $t('settings.summaries.timelineSpeech') }}
        </wa-switch>
        <small>{{ $t('settings.summaries.timelineSpeechHint') }}</small>
    </div>
</template>

<style scoped>
.summary-settings { display: flex; flex-direction: column; gap: var(--wa-space-s); }
/* The segmented control keeps its content width and its one row of segments, as in the speech settings. */
.summary-settings > .help-feature-link { align-self: flex-start; }
.summary-model-row { display: flex; flex-wrap: wrap; align-items: center; }
.summary-model-row > .segmented-control { flex: none; max-width: 100%; }
p { margin: 0; }
small { color: var(--wa-color-text-quiet); }
</style>
