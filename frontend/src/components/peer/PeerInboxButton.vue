<script setup>
/**
 * PeerInboxButton — badge entry point to the peer inbox.
 *
 * Sits next to the Settings button (ProjectView sidebar footer, HomeView).
 * Rendered as soon as the peer system is configured, pending work or not: the
 * inbox is a surface the user reaches on their own terms, and an empty one
 * still shows the history. An instance without peers grows no chrome at all.
 *
 * The badge is the pending-work signal — it renders nothing at zero.
 */
import { useId, computed } from 'vue'
import { usePeersStore } from '../../stores/peers'
import { usePeerSystemConfigured } from '../../composables/usePeerSystemConfigured'
import AppTooltip from '../ui/AppTooltip.vue'
import PeerInboxBadge from './PeerInboxBadge.vue'

const peersStore = usePeersStore()
const buttonId = useId()

const count = computed(() => peersStore.inboxCount)
const visible = usePeerSystemConfigured()

function openInbox() {
    window.dispatchEvent(new CustomEvent('twicc:open-peer-inbox'))
}
</script>

<template>
    <template v-if="visible">
        <wa-button
            :id="buttonId"
            class="peer-inbox-button"
            variant="neutral"
            appearance="filled-outlined"
            size="small"
            @click="openInbox"
        >
            <wa-icon name="envelope"></wa-icon>
            <PeerInboxBadge :count="count" />
        </wa-button>
        <AppTooltip :for="buttonId">Peer inbox</AppTooltip>
    </template>
</template>

<style scoped>
.peer-inbox-button {
    position: relative;
}
.peer-inbox-button::part(base) {
    inline-size: var(--wa-form-control-height);
    padding-inline: 0;
}
/* Same footer degradation as CommandPaletteButton: drop out when the sidebar
   gets too narrow, below SettingsPopover's compact threshold. */
@container sidebar (width <= 9rem) {
    .peer-inbox-button { display: none; }
}
/* SettingsPopover MIRRORS this threshold to reveal the count on the Settings
   button, the only footer action left at that width. Move one, move both. */
</style>
