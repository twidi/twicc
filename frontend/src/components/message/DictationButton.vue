<script setup>
/**
 * The composer's dictation button (speech to text).
 *
 * Driven by the ``useSpeechToText`` instance its composer owns (passed as
 * ``stt``): a microphone when idle, a pulsing stop button with the elapsed
 * time while listening/recording, a spinner while the server transcribes.
 * A tap toggles — the same gesture on desktop and on a phone; on a touch
 * device the hit area is enlarged. Errors show as a short-lived tooltip.
 */
import { computed, watch, ref, onBeforeUnmount } from 'vue'
import { useI18n } from 'vue-i18n'
import AppTooltip from '../ui/AppTooltip.vue'
import { formatElapsed } from '../../utils/speechToText.js'
import { resolveUiMessage } from '../../i18n/uiMessages.js'

const props = defineProps({
    stt: { type: Object, required: true },
    buttonId: { type: String, required: true },
    shortcutLabel: { type: String, default: '' },
})

const { t } = useI18n()

const status = computed(() => props.stt.status.value)
const listening = computed(() => status.value === 'listening' || status.value === 'recording')
const busy = computed(() => status.value === 'starting' || status.value === 'transcribing')
const unavailable = computed(() => !props.stt.engine.value)

const ENGINE_LABEL_KEYS = {
    browser: 'composer.dictation.engines.browser',
    openrouter: 'composer.dictation.engines.openrouter',
    script: 'composer.dictation.engines.script',
}

const tooltip = computed(() => {
    if (status.value === 'transcribing') return t('composer.dictation.transcribing')
    if (listening.value) return t('composer.dictation.stop')
    if (unavailable.value) return reasonText(props.stt.unavailableReason.value)
    const engine = t(ENGINE_LABEL_KEYS[props.stt.engine.value] || ENGINE_LABEL_KEYS.browser)
    const base = t('composer.dictation.start', { engine })
    return props.shortcutLabel ? `${base} (${props.shortcutLabel})` : base
})

function reasonText(reason) {
    if (!reason) return ''
    if (typeof reason === 'object') return resolveUiMessage(reason)
    const key = `composer.dictation.errors.${reason}`
    const translated = t(key)
    return translated === key ? String(reason) : translated
}

// A transient error tooltip, also on touch devices (forced).
const errorText = ref('')
let errorTimer = null
watch(() => props.stt.error.value, (value) => {
    if (!value) return
    errorText.value = reasonText(value)
    clearTimeout(errorTimer)
    errorTimer = setTimeout(() => {
        errorText.value = ''
        props.stt.clearError()
    }, 5000)
})
onBeforeUnmount(() => clearTimeout(errorTimer))

function onClick() {
    errorText.value = ''
    props.stt.toggle()
}
</script>

<template>
    <wa-button
        :id="buttonId"
        variant="neutral"
        appearance="plain"
        size="small"
        class="dictation-button"
        :class="{ 'is-listening': listening, 'is-busy': busy, 'is-unavailable': unavailable }"
        :aria-pressed="listening"
        :aria-label="tooltip"
        :disabled="status === 'transcribing'"
        @click="onClick"
    >
        <wa-spinner v-if="busy" class="dictation-spinner"></wa-spinner>
        <wa-icon v-else :name="listening ? 'stop' : 'microphone'"></wa-icon>
        <span v-if="listening" class="dictation-elapsed">{{ formatElapsed(stt.elapsed.value) }}</span>
    </wa-button>
    <AppTooltip :for="buttonId">{{ tooltip }}</AppTooltip>
    <AppTooltip
        force
        :for="buttonId"
        trigger="manual"
        placement="top"
        :open="Boolean(errorText) || undefined"
    >{{ errorText }}</AppTooltip>
</template>

<style scoped>
.dictation-button.is-listening {
    color: var(--wa-color-danger-fill-loud);
}

.dictation-button.is-listening wa-icon {
    animation: dictation-pulse 1.2s ease-in-out infinite;
}

.dictation-button.is-unavailable {
    opacity: 0.55;
}

.dictation-elapsed {
    font-variant-numeric: tabular-nums;
    font-size: var(--wa-font-size-s);
    margin-inline-start: 0.25em;
}

.dictation-spinner {
    font-size: 1em;
}

/* A fade on a status indicator: kept under reduce-motion (utils/reducedMotion.js). */
@keyframes dictation-pulse {
    0%, 100% { opacity: 1; }
    50% { opacity: 0.35; }
}

/* Touch devices: a larger hit area for a one-thumb tap. */
@media (pointer: coarse) {
    .dictation-button::part(base) {
        min-width: 2.75rem;
        min-height: 2.75rem;
    }
}
</style>
