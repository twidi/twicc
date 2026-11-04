<script setup>
import { computed } from 'vue'
import { useTextToSpeechStore } from '../../../../stores/textToSpeech'
import { toast } from '../../../../composables/useToast'
import SummaryContent from '../../../ui/SummaryContent.vue'
import TextToSpeechButton from '../../../ui/TextToSpeechButton.vue'

const props = defineProps({ summary: { type: Object, required: true }, speech: { type: Boolean, default: true } })
const tts = useTextToSpeechStore()
const reading = computed(() => props.summary.identity && tts.statusForSummary(props.summary.identity) !== 'idle')
async function copy() {
    try { await navigator.clipboard.writeText(props.summary.entry.text); toast.success('Copied to clipboard', { duration: 2000 }) }
    catch (error) { toast.error(error.message || 'Could not copy the summary') }
}
</script>

<template>
    <section class="message-summary" aria-label="Message summary">
        <template v-if="summary.view === 'summary'">
            <strong>Summary</strong>
            <SummaryContent v-if="summary.entry" :entry="summary.entry" show-original @show-original="summary.showOriginal" @retry="summary.regenerate" @cancel="summary.cancel" />
            <div v-else>
                <p v-if="summary.loading" role="status">Loading summary…</p>
                <wa-button size="small" @click="summary.showOriginal">Show original</wa-button>
                <wa-button v-if="summary.error" size="small" @click="summary.summarize">Retry</wa-button>
            </div>
            <wa-callout v-if="summary.error" variant="danger">{{ summary.error }}</wa-callout>
            <template v-if="summary.entry && summary.generation && summary.entry !== summary.generation">
                <p v-if="summary.generation.state === 'running'" role="status">Generating summary… <wa-button size="small" @click="summary.cancel">Cancel</wa-button></p>
                <wa-callout v-if="summary.generation.error" variant="danger">{{ summary.generation.error }}</wa-callout>
            </template>
            <wa-button v-if="summary.descriptor?.previous && !summary.entry?.previousPolicy" size="small" @click="summary.showPrevious">Show previous summary</wa-button>
            <wa-button v-if="summary.entry?.previousPolicy && (summary.descriptor?.saved || summary.generation)" size="small" @click="summary.showCurrent">Show current summary</wa-button>
            <wa-button v-if="summary.entry?.text" size="small" @click="copy">Copy summary</wa-button>
        </template>
        <TextToSpeechButton
            v-if="speech && (reading || (summary.view === 'summary' && summary.playbackReference))"
            :text="summary.entry?.text || ''"
            :summary-ref="summary.playbackReference"
            :summary-identity="summary.identity"
            @summary-changed="summary.refreshSaved"
        />
    </section>
</template>

<style scoped>
.message-summary:empty { display: none; }
.message-summary { overflow-wrap: anywhere; }
</style>
