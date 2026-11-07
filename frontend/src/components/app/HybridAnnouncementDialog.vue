<script setup>
// App-load announcement for the 15 June 2026 billing change / hybrid mode.
// Auto-opens once for users who haven't seen the explainer yet
// (claudeHybridExplainerSeen === false). Reuses the shared HybridModeExplainer
// (rendered open) so the copy stays in one place. Hosted in App.vue behind
// ``v-if="isAppReady"``, so it only mounts once the user is past the login layer
// and the synced settings have been hydrated by the bootstrap fetch.
import { ref, onMounted, onBeforeUnmount } from 'vue'
import { useSettingsStore } from '../../stores/settings'
import { useDataStore } from '../../stores/data'
import { hasBlockingOverlay } from '../../utils/focusGuard'
import HybridModeExplainer from '../message/HybridModeExplainer.vue'

const settingsStore = useSettingsStore()
const dataStore = useDataStore()
const dialogRef = ref(null)
// "Don't show this again" defaults OFF (opt-in): the announcement re-shows on
// each load until the user actively turns it on, then closing marks it seen.
const dontShowAgain = ref(false)

// Small delay before auto-opening so first-run dialogs that may have priority
// (provider activation, version mismatch) get to render first; if one is up we
// skip this load and show on a later one, rather than stacking on top of it.
const OPEN_DELAY_MS = 500
let openTimer = null

onMounted(() => {
    if (settingsStore.isClaudeHybridExplainerSeen) return
    // We intend to show it → hold the changelog auto-open back until we're done
    // (set now, before any WS message can arrive, so the two never race).
    dataStore.setHybridAnnouncementActive(true)
    openTimer = setTimeout(() => {
        openTimer = null
        // Bail out (and release the changelog) if it shouldn't show after all.
        if (settingsStore.isClaudeHybridExplainerSeen || hasBlockingOverlay()) {
            dataStore.setHybridAnnouncementActive(false)
            return
        }
        if (dialogRef.value) dialogRef.value.open = true
    }, OPEN_DELAY_MS)
})
onBeforeUnmount(() => {
    if (openTimer) clearTimeout(openTimer)
    // Never leave the changelog gated if we go away while pending/open.
    dataStore.setHybridAnnouncementActive(false)
})

function onOk() {
    if (dialogRef.value) dialogRef.value.open = false
}

// Persist "seen" when the dialog closes with the switch turned on — whether
// dismissed via OK, Esc or the X. Scoped to the dialog's own
// wa-after-hide: the nested wa-details (explainer) bubbles the same event up
// when collapsed, which must not be mistaken for the dialog closing. Releasing
// the gate here lets a pending changelog auto-open take over.
function onAfterHide(event) {
    if (event.target !== dialogRef.value) return
    if (dontShowAgain.value) settingsStore.setClaudeHybridExplainerSeen(true)
    dataStore.setHybridAnnouncementActive(false)
}
</script>

<template>
    <wa-dialog
        ref="dialogRef"
        class="hybrid-announcement-dialog"
        :label="$t('hybrid.announcement.title')"
        style="--width: min(40rem, calc(100vw - 2rem))"
        @wa-after-hide="onAfterHide"
    >
        <i18n-t tag="p" class="announcement-intro" keypath="hybrid.announcement.intro" scope="global">
            <template #hybridMode><strong>{{ $t('hybrid.announcement.mode') }}</strong></template>
        </i18n-t>

        <HybridModeExplainer :open="true" />

        <wa-switch
            slot="footer"
            class="announcement-switch"
            :checked="dontShowAgain"
            @change="dontShowAgain = $event.target.checked"
        >{{ $t('hybrid.announcement.dontShowAgain') }}</wa-switch>
        <wa-button slot="footer" variant="brand" @click="onOk">{{ $t('hybrid.announcement.ok') }}</wa-button>
    </wa-dialog>
</template>

<style scoped>
.announcement-intro {
    margin-block-start: 0;
    line-height: 1.5;
}
/* Push the switch to the far left of the footer, buttons to the right —
   mirrors the hybrid toggle dialog. */
.announcement-switch {
    margin-inline-end: auto;
    align-self: center;
}
</style>
