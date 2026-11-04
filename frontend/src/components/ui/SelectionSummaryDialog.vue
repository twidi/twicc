<script setup>
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useSummariesStore } from '../../stores/summaries'
import { useTextToSpeechStore } from '../../stores/textToSpeech'
import { useSettingsStore } from '../../stores/settings'
import { useTemporarySummaryGeneration } from '../../composables/useTemporarySummaryGeneration'
import { toast } from '../../composables/useToast'
import SummaryContent from './SummaryContent.vue'
import TextToSpeechButton from './TextToSpeechButton.vue'

const store = useSummariesStore()
const speech = useTextToSpeechStore()
const settings = useSettingsStore()
const generation = useTemporarySummaryGeneration(() => store.temporary?.source, settings, speech, store)
const closeButton = ref(null)
const identity = computed(() => ({ selectionId: store.temporary?.id, variant: 'summary' }))
const reading = computed(() => store.temporary && speech.statusForSummary(identity.value) !== 'idle')
const summaryReference = computed(() => store.temporary ? store.temporaryReference() : null)
watch(() => store.temporary?.id, (_id, previousId) => {
    if (previousId && speech.statusForSummary({ selectionId: previousId, variant: 'summary' }) !== 'idle') speech.stop()
}, { flush: 'sync' })
const excerpt = computed(() => {
    const text = store.temporary?.source.text || ''
    return text.length > 240 ? `${text.slice(0, 240)}…` : text
})
function stopReading() {
    if (speech.statusForSummary(identity.value) !== 'idle') speech.stop()
}
function close() { stopReading(); store.closeTemporary() }
function retry() { if (!generation.value.available) return; stopReading(); store.retryTemporary() }
function cancel() { stopReading(); store.cancelTemporary() }
function onHide(event) {
    if (event.target !== event.currentTarget || event.currentTarget.open) return
    close()
}
function focusClose(event) {
    if (event.target !== event.currentTarget) return
    closeButton.value?.focus()
}
async function copy() {
    try { await navigator.clipboard.writeText(store.temporary.entry.text); toast.success('Copied to clipboard', { duration: 2000 }) }
    catch (error) { toast.error(error.message || 'Could not copy the summary') }
}
onBeforeUnmount(close)
</script>

<template>
    <wa-dialog :open="Boolean(store.temporary)" label="Selection summary" class="selection-summary-dialog" @wa-hide="onHide" @wa-after-show="focusClose">
        <template v-if="store.temporary">
            <blockquote class="source-excerpt">{{ excerpt }}</blockquote>
            <SummaryContent :entry="store.temporary.entry" :retry-disabled="!generation.available" :retry-unavailable-reason="generation.unavailableReason" @cancel="cancel" @retry="retry" />
        </template>
        <div slot="footer" class="dialog-actions">
            <wa-button size="small" :disabled="!store.temporary?.entry.text" @click="copy">Copy</wa-button>
            <TextToSpeechButton
                v-if="store.temporary && (reading || store.temporary.entry.state === 'running' || store.temporary.entry.state === 'complete')"
                :text="store.temporary.entry.text"
                :summary-ref="summaryReference"
                :summary-identity="identity"
            />
            <wa-button ref="closeButton" size="small" @click="close">Close</wa-button>
        </div>
    </wa-dialog>
</template>

<style scoped>
.selection-summary-dialog { --width: min(640px, calc(100vw - 2rem)); }
.source-excerpt { margin: 0 0 var(--wa-space-m); padding: var(--wa-space-s); border-left: 3px solid var(--wa-color-border-neutral-tertiary); color: var(--wa-color-text-quiet); white-space: pre-wrap; overflow-wrap: anywhere; max-height: 8rem; overflow-y: auto; }
.dialog-actions { display: flex; flex-wrap: wrap; align-items: center; gap: var(--wa-space-s); }
</style>
