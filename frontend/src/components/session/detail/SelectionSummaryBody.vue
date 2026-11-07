<script setup>
import { useI18n } from 'vue-i18n'
const { t } = useI18n()
// The summary of a text selection, inside the floating selection panel. It owns
// the temporary summary: it opens it on mount and discards it on unmount.
import { computed, onBeforeUnmount, watch } from 'vue'
import { useSettingsStore } from '../../../stores/settings'
import { useSummariesStore } from '../../../stores/summaries'
import { useTextToSpeechStore } from '../../../stores/textToSpeech'
import { useTemporarySummaryGeneration } from '../../../composables/useTemporarySummaryGeneration'
import { toast } from '../../../composables/useToast'
import MarkdownContent from '../../ui/MarkdownContent.vue'
import SummaryGenerationLinks from '../../ui/SummaryGenerationLinks.vue'
import TextToSpeechButton from '../../ui/TextToSpeechButton.vue'

const props = defineProps({ text: { type: String, required: true } })
const emit = defineEmits(['content-change'])

const store = useSummariesStore()
const speech = useTextToSpeechStore()
const settings = useSettingsStore()
store.openTemporary(props.text)

const generation = useTemporarySummaryGeneration(() => store.temporary?.source, settings, speech, store)
const entry = computed(() => store.temporary?.entry)
const identity = computed(() => ({ selectionId: store.temporary?.id, variant: 'summary' }))
const nativeSpeechRequest = computed(() => {
    const descriptor = entry.value?.descriptor, source = store.temporary?.source
    if (!source) return null
    if (descriptor) return { source: { ...source }, source_key: descriptor.source_key,
        source_hash: descriptor.source_hash, policy_key: descriptor.policy_key }
    const requested = ['auto', 'haiku', 'luna', 'openrouter'].includes(settings.summaryModel) ? settings.summaryModel : 'auto'
    return { source: { ...source }, requested_model: requested }
})
const reading = computed(() => !!store.temporary && speech.statusForSummary(identity.value) !== 'idle')
const summaryReference = computed(() => store.temporary ? store.temporaryReference() : null)
const speakable = computed(() => reading.value || ['running', 'complete'].includes(entry.value?.state))

watch(() => store.temporary?.id, (_id, previousId) => {
    if (!speech.nativeMode && previousId && speech.statusForSummary({ selectionId: previousId, variant: 'summary' }) !== 'idle') speech.stop()
}, { flush: 'sync' })
watch(() => entry.value?.text, () => emit('content-change'), { flush: 'post' })

function stopReading() {
    if (reading.value) speech.stop()
}
function regenerate() {
    if (!generation.value.available) return
    stopReading()
    store.retryTemporary()
}
function cancel() {
    stopReading()
    store.cancelTemporary()
}
async function copy() {
    try {
        await navigator.clipboard.writeText(entry.value.text)
        toast.success(t('session.summary.copied'), { duration: 2000 })
    } catch (error) {
        toast.error(error.message || t('session.summary.copyFailed'))
    }
}
onBeforeUnmount(() => {
    if (!speech.nativeMode) stopReading()
    store.closeTemporary()
})
</script>

<template>
    <h3 class="tsc-summary-title">{{ $t('session.summary.title') }}</h3>
    <div v-if="entry?.text" class="tsc-summary-text" aria-live="polite" :aria-busy="entry.state === 'running'">
        <MarkdownContent :source="entry.text" :show-toolbar="false" />
    </div>
    <wa-callout v-if="entry?.error" variant="danger">{{ entry.error }}</wa-callout>
    <div class="tsc-summary-footer">
        <div class="tsc-summary-links">
            <SummaryGenerationLinks
                :running="entry?.state === 'running'"
                :can-regenerate="generation.available"
                @cancel="cancel"
                @regenerate="regenerate"
            />
            <a
                v-if="generation.lookupFailed"
                href="#"
                class="tsc-summary-retry"
                :title="generation.unavailableReason"
                @click.prevent="generation.retryLookup"
            >{{ $t('session.summary.retryAvailability') }}</a>
        </div>
        <div class="tsc-summary-buttons">
            <wa-button
                size="small"
                variant="neutral"
                appearance="filled"
                :title="$t('session.summary.copy')"
                :aria-label="$t('session.summary.copy')"
                :disabled="!entry?.text"
                @click="copy"
            >
                <wa-icon name="copy" variant="regular"></wa-icon>
            </wa-button>
            <TextToSpeechButton
                v-if="speakable"
                :text="entry?.text ?? ''"
                :native-speech-request="nativeSpeechRequest"
                :summary-ref="summaryReference"
                :summary-identity="identity"
            />
        </div>
    </div>
</template>

<style scoped>
.tsc-summary-title {
    margin: 0;
    padding-inline: var(--wa-space-l);
    font-size: var(--wa-font-size-s);
    font-weight: var(--wa-font-weight-semibold);
    text-align: center;
    cursor: grab; /* Part of the drag handle, like the panel background. */
}

.tsc-summary-text {
    max-height: min(22rem, 50vh);
    overflow: auto;
    cursor: auto;
    touch-action: auto; /* The panel blocks touch gestures for its drag. */
}

.tsc-summary-text :deep(.markdown-body) {
    font-size: var(--wa-font-size-s);
}

.tsc-summary-footer {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: var(--wa-space-s);
    font-size: var(--wa-font-size-s);
    line-height: 1.2;
}

.tsc-summary-links,
.tsc-summary-buttons {
    display: flex;
    align-items: center;
    gap: var(--wa-space-s);
}

.tsc-summary-retry {
    color: var(--wa-color-text-link);
}
</style>
