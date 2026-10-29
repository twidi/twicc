<script setup>
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
})

const store = useTextToSpeechStore()

const TITLES = {
    idle: 'Read aloud',
    loading: 'Cancel the request',
    playing: 'Stop reading',
}

const status = computed(() => store.statusForText(props.text))
const title = computed(() => TITLES[status.value])

async function onClick() {
    if (status.value !== 'idle') {
        store.stop()
        return
    }
    const result = await store.play(props.text)
    if (!result.ok && !result.aborted) toast.error(result.error, { duration: 4000 })
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
