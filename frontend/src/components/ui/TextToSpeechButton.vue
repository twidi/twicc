<script setup>
import { useI18n } from 'vue-i18n'
const { t } = useI18n()
/**
 * Read-aloud button of the MarkdownContent toolbar (a `wa-button` of its button group).
 *
 * A view of the app-wide speech store for one text: it never owns the playback, so the
 * audio survives the unmount of this button (virtualized list, navigation).
 * Shown while the service is available, and while its own text is still busy even if the
 * availability is lost mid-request (the store aborts the playback in that case).
 */
import { computed } from 'vue'
import { useTextToSpeechStore } from '../../stores/textToSpeech'
import { toast } from '../../composables/useToast'

const props = defineProps({
    text: {
        type: String,
        required: true,
    },
    summaryRef: { type: [Object, Function], default: null },
    nativeSpeechRequest: { type: Object, default: null },
    summaryIdentity: { type: Object, default: null },
    originalText: { type: String, default: null },
})

const emit = defineEmits(['summary-changed'])
const store = useTextToSpeechStore()

const TITLES = {
    idle: 'tts.controls.readAloud',
    loading: 'tts.controls.cancelRequest',
    playing: 'tts.controls.stopReading',
}

const status = computed(() => {
    const summary = props.summaryIdentity ? store.statusForSummary(props.summaryIdentity) : 'idle'
    return summary !== 'idle' ? summary : store.statusForText(props.originalText ?? props.text)
})
const title = computed(() => t(TITLES[status.value]))

async function onClick() {
    if (status.value !== 'idle') {
        store.stop()
        return
    }
    const reference = props.summaryRef
    const result = await (reference
        ? store.playSummary(reference, { identity: props.summaryIdentity, nativeRequest: props.nativeSpeechRequest })
        : store.play(props.text))
    if (result.summaryChanged) emit('summary-changed', result.savedId)
    if (!result.ok && !result.aborted) toast.error(result.uiMessage || result.error, { duration: 4000 })
    if (!result.ok) return
    // An error can still come after the sound started (the stream broke). The toast is an
    // app-wide service: it shows even when this button is already unmounted.
    result.ended.then(end => {
        if (!end.ok && !end.aborted) toast.error(end.uiMessage || end.error, { duration: 4000 })
    })
}
</script>

<template>
    <wa-button
        v-if="store.available || status !== 'idle'"
        :class="{ 'markdown-toolbar-speech--busy': status !== 'idle' }"
        size="small"
        :variant="status === 'playing' ? 'brand' : 'neutral'"
        appearance="filled"
        :title="title"
        :aria-label="title"
        @click="onClick"
    >
        <wa-spinner v-if="status === 'loading'"></wa-spinner>
        <wa-icon v-else :name="status === 'playing' ? 'stop' : 'volume-up'"></wa-icon>
    </wa-button>
</template>
