<script setup>
/**
 * TelemetryPayloadDialog - Shows the last anonymous telemetry payload sent
 * to the backend's collector, or a live preview when nothing has been sent
 * yet. Read-only: fetches on open via the ``get_telemetry_payload`` WS
 * round-trip, no mutation.
 *
 * Mounted once in SettingsPopover, toggled via the `open` prop like
 * ShareManagerDialog.
 */
import { ref, computed, watch } from 'vue'
import JsonViewer from '../json/JsonViewer.vue'
import { requestTelemetryPayload } from '../../composables/useWebSocket'
import { formatDate } from '../../utils/date'

const props = defineProps({ open: Boolean })
const emit = defineEmits(['close'])

const dialogRef = ref(null)
const loading = ref(false)
const payload = ref(null)
const sentAt = ref(null)
const preview = ref(false)
const collapsedPaths = ref(new Set())

function toggleCollapse(path) {
    if (collapsedPaths.value.has(path)) {
        collapsedPaths.value.delete(path)
    } else {
        collapsedPaths.value.add(path)
    }
}

const formattedSentAt = computed(() => {
    if (!sentAt.value) return ''
    return formatDate(Math.floor(new Date(sentAt.value).getTime() / 1000), { smart: true })
})

// Fetch a fresh payload every time the dialog opens.
watch(() => props.open, async (isOpen) => {
    if (!isOpen) return
    loading.value = true
    payload.value = null
    sentAt.value = null
    preview.value = false
    collapsedPaths.value = new Set()
    const result = await requestTelemetryPayload()
    payload.value = result.payload
    sentAt.value = result.sent_at
    preview.value = result.preview
    loading.value = false
})

// Guard against wa-hide bubbling from a nested wa-* element (see CLAUDE.md
// "Bubbling custom events") — only react when the dialog itself fires it.
function onHide(e) {
    if (e.target === dialogRef.value) emit('close')
}
</script>

<template>
    <wa-dialog
        ref="dialogRef"
        :open="open"
        :label="$t('settings.telemetry.payload.title')"
        class="telemetry-payload-dialog"
        @wa-hide="onHide"
    >
        <div v-if="loading" class="telemetry-payload-loading">
            <wa-spinner></wa-spinner>
        </div>
        <template v-else>
            <p v-if="payload === null" class="muted">{{ $t('settings.telemetry.payload.empty') }}</p>
            <template v-else>
                <p class="telemetry-payload-heading">
                    <template v-if="preview">{{ $t('settings.telemetry.payload.preview') }}</template>
                    <template v-else>{{ $t('settings.telemetry.payload.lastSent', { date: formattedSentAt }) }}</template>
                </p>
                <div class="telemetry-payload-json">
                    <JsonViewer
                        :data="payload"
                        path="root"
                        :collapsed-paths="collapsedPaths"
                        @toggle="toggleCollapse"
                    />
                </div>
            </template>
        </template>

        <div slot="footer" class="dialog-footer">
            <wa-button @click="emit('close')">{{ $t('common.actions.close') }}</wa-button>
        </div>
    </wa-dialog>
</template>

<style scoped>
.telemetry-payload-dialog {
    --width: min(40rem, calc(100vw - 2rem));
}

.telemetry-payload-loading {
    display: flex;
    justify-content: center;
    padding: var(--wa-space-l) 0;
}

.telemetry-payload-heading {
    margin: 0 0 var(--wa-space-s) 0;
    color: var(--wa-color-text-quiet);
    font-size: var(--wa-font-size-s);
}

.telemetry-payload-json {
    max-height: 60vh;
    overflow: auto;
}

.muted {
    color: var(--wa-color-text-quiet);
}

.dialog-footer {
    display: flex;
    flex-wrap: wrap;
    gap: var(--wa-space-s);
    justify-content: flex-end;
}
</style>
