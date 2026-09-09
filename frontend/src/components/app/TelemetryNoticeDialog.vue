<script setup>
// One-time notice for the anonymous usage telemetry feature. Auto-opens once
// for users who haven't seen it yet (telemetryNoticeSeen === false), only
// when telemetry is actually on. Modeled on HybridAnnouncementDialog.vue:
// higher-priority first-run dialogs get the floor first, and any dismissal
// (Got it, Open settings, Esc, X) marks it seen — the notice must never nag
// twice. It yields the floor but never its turn: it waits for as long as
// something else holds it (see tryOpen below).
import { ref, onMounted, onBeforeUnmount } from 'vue'
import { useSettingsStore } from '../../stores/settings'
import { useDataStore } from '../../stores/data'
import { hasBlockingOverlay } from '../../utils/focusGuard'

const settingsStore = useSettingsStore()
const dataStore = useDataStore()
const dialogRef = ref(null)
// Set when "Open settings" is clicked, consumed in onAfterHide once the
// dialog has actually finished closing.
const pendingOpenSettings = ref(false)

// Small delay before the first attempt, so first-run dialogs that have
// priority (provider activation, hybrid announcement) get to render first.
const OPEN_DELAY_MS = 500
// Then look again on this cadence for as long as one is up. Deliberately
// UNBOUNDED: the notice must reach the user before anything else happens, so
// giving up is never the right answer — a user who walks away for four hours
// still finds it waiting. Skipping the load (what this used to do) meant the
// notice silently vanished until the next reload, AND released the changelog
// hold, letting the changelog jump the queue over a notice never shown.
const RECHECK_MS = 500
let openTimer = null

/** Open the notice, or wait for the floor to clear — see the constants above. */
function tryOpen() {
    openTimer = null
    // Terminal: it should not show at all any more. Release the changelog.
    if (!settingsStore.isTelemetryEnabled || settingsStore.isTelemetryNoticeSeen) {
        dataStore.setTelemetryNoticeActive(false)
        return
    }
    // Transient: something has the floor. Keep our turn and look again.
    if (hasBlockingOverlay()) {
        openTimer = setTimeout(tryOpen, RECHECK_MS)
        return
    }
    if (dialogRef.value) dialogRef.value.open = true
}

onMounted(() => {
    if (!settingsStore.isTelemetryEnabled) return
    if (settingsStore.isTelemetryNoticeSeen) return
    // We intend to show it → hold the changelog auto-open back until we're
    // done (set now, before any WS message can arrive, so the two never
    // race). Mirrors HybridAnnouncementDialog's dataStore.hybridAnnouncementActive.
    dataStore.setTelemetryNoticeActive(true)
    openTimer = setTimeout(tryOpen, OPEN_DELAY_MS)
})
onBeforeUnmount(() => {
    if (openTimer) clearTimeout(openTimer)
    // Never leave the changelog gated if we go away while pending/open.
    dataStore.setTelemetryNoticeActive(false)
})

function onGotIt() {
    if (dialogRef.value) dialogRef.value.open = false
}

function onOpenSettings() {
    pendingOpenSettings.value = true
    if (dialogRef.value) dialogRef.value.open = false
}

// Any dismissal counts as seen — Got it, Open settings, Esc, or the X.
// Scoped to the dialog's own wa-after-hide: guards against a nested wa-*
// element (none today, but keep the pattern) bubbling the same event up.
// Also releases the changelog hold-back — always, on every dismissal path.
function onAfterHide(event) {
    if (event.target !== dialogRef.value) return
    settingsStore.setTelemetryNoticeSeen(true)
    dataStore.setTelemetryNoticeActive(false)
    if (pendingOpenSettings.value) {
        pendingOpenSettings.value = false
        // wa-after-hide fires once the dialog's own close animation and
        // backdrop teardown are complete, so the click below lands on a
        // fully interactive page rather than mid-close. Assumes
        // #settings-trigger is present — it lives in SettingsPopover, mounted
        // by the two app shells (HomeView.vue, ProjectView.vue) where this
        // notice fires; a no-op elsewhere.
        document.querySelector('#settings-trigger')?.click()
    }
}
</script>

<template>
    <wa-dialog
        ref="dialogRef"
        class="telemetry-notice-dialog"
        label="Anonymous usage statistics"
        style="--width: min(32rem, calc(100vw - 2rem))"
        @wa-after-hide="onAfterHide"
    >
        <p>TwiCC collects anonymous usage statistics — counters only, never content.</p>
        <p>
            This helps TwiCC's author understand how the project is used — thank you for leaving it
            enabled. The telemetry code is
            <a href="https://github.com/twidi/twicc" target="_blank" rel="noopener noreferrer">open source</a>,
            so you can verify it does nothing more than advertised.
        </p>
        <p>
            See exactly what is sent on the
            <a href="https://twicc-telemetry.twidi.com/" target="_blank" rel="noopener noreferrer">transparency page</a>.
        </p>
        <p>You can disable it any time in Settings &rarr; General.</p>

        <wa-button slot="footer" appearance="outlined" @click="onOpenSettings">Open settings</wa-button>
        <wa-button slot="footer" variant="brand" @click="onGotIt">Got it</wa-button>
    </wa-dialog>
</template>

<style scoped>
.telemetry-notice-dialog p {
    margin-block: 0 0.75rem;
    line-height: 1.5;
}
.telemetry-notice-dialog p:last-of-type {
    margin-block-end: 0;
}
</style>
