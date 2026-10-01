<script setup>
/**
 * StopProcessConfirmDialog - Confirmation dialog shown when stopping a process
 * that has active crons and/or background shells.
 *
 * Warns the user that active cron jobs will no longer trigger, and that
 * background shells the agent left running will be killed,
 * and asks for explicit confirmation before proceeding. Supports two modes:
 * - 'stop': just stop the process
 * - 'archive': stop the process and archive the session
 *
 * Props-driven: the parent controls visibility via the `open` prop and
 * receives `confirm` / `cancel` events. There is typically a single instance
 * mounted globally in App.vue.
 */
import { computed, ref, watch } from 'vue'
import { backgroundShellsKillSentence, stopConfirmationTitle } from '../../utils/backgroundWork'

const props = defineProps({
    open: { type: Boolean, default: false },
    mode: { type: String, default: 'stop' },       // 'stop' | 'archive'
    cronCount: { type: Number, default: 0 },
    shellCount: { type: Number, default: 0 },
})
const emit = defineEmits(['confirm', 'cancel'])

const dialogRef = ref(null)

// Counts shown, frozen while the dialog closes: the parent resets its props
// to 0 as soon as it closes, which would otherwise swap the title and drop
// the callouts during the hide animation.
const shown = ref({ cronCount: 0, shellCount: 0 })
watch(() => [props.open, props.cronCount, props.shellCount], ([isOpen, cronCount, shellCount]) => {
    if (isOpen) shown.value = { cronCount, shellCount }
}, { immediate: true })

const title = computed(() => stopConfirmationTitle(shown.value))
const shellsSentence = computed(() => backgroundShellsKillSentence(shown.value.shellCount))

// Set synchronously by handleConfirm / handleCancel so the subsequent
// wa-hide event (fired when the dialog closes in reaction to props.open
// going false) does NOT re-emit `cancel` on top of the button's emit.
let closedByButton = false

// Sync the `open` prop to the underlying wa-dialog's `open` attribute.
watch(() => props.open, (isOpen) => {
    if (!dialogRef.value) return
    dialogRef.value.open = isOpen
}, { immediate: true })

// wa-hide fires when the dialog is requested to close: either because
// props.open flipped to false (our watcher set it) or because the user
// dismissed via Escape / the X button / backdrop.
// We only want to emit `cancel` in the user-dismissal case.
function onWaHide() {
    if (closedByButton) {
        closedByButton = false
        return
    }
    if (props.open) emit('cancel')
}

function handleConfirm() {
    closedByButton = true
    emit('confirm', { mode: props.mode })
}

function handleCancel() {
    closedByButton = true
    emit('cancel')
}
</script>

<template>
    <wa-dialog
        ref="dialogRef"
        :label="title"
        class="stop-confirm-dialog"
        @wa-hide="onWaHide"
    >
        <div class="dialog-content">
            <wa-callout v-if="shown.shellCount > 0" variant="warning" size="small" open>
                <p>{{ shellsSentence }}</p>
            </wa-callout>
            <wa-callout v-if="shown.cronCount > 0" variant="warning" size="small" open>
                <p>
                    This session has
                    <strong>{{ shown.cronCount }} active cron{{ shown.cronCount > 1 ? 's' : '' }}</strong>.
                </p>
                <p>
                    Stopping the process manually will cancel all active crons. They will not be
                    restored automatically when the process restarts.
                </p>
                <p>
                    You can always ask the agent to set them up again later.
                </p>
            </wa-callout>
        </div>

        <div slot="footer" class="dialog-footer">
            <wa-button variant="neutral" appearance="outlined" @click="handleCancel">
                Cancel, keep the process
            </wa-button>
            <wa-button variant="danger" appearance="filled" @click="handleConfirm">
                <wa-icon slot="start" name="ban"></wa-icon>
                {{ mode === 'archive' ? 'Stop and archive' : 'Stop the process' }}
            </wa-button>
        </div>
    </wa-dialog>
</template>

<style scoped>
.stop-confirm-dialog {
    --width: min(40rem, calc(100vw - 2rem));
}

.dialog-content {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-m);
}

.dialog-content p {
    margin: 0;
}

.dialog-content p + p {
    margin-top: var(--wa-space-s);
}

.dialog-footer {
    display: flex;
    flex-wrap: wrap;
    gap: var(--wa-space-s);
    justify-content: flex-end;
}
</style>
