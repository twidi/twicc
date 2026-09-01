<script setup>
/**
 * ProviderStatusToastContent — body of the persistent "<vendor> status update"
 * toast: the outage (or recovery, with its time window) sentence and a link
 * to the vendor's status page.
 *
 * Its one behaviour is the acknowledgment: closing the toast with Notivue's
 * "×" records, through the backend, that the user has seen this episode —
 * which then clears the same toast on every other tab and device. A clear
 * issued by ``providers/serviceStatusToast.js`` itself (a newer episode
 * replacing this one, or an acknowledgment that arrived from elsewhere) is
 * told apart via ``isProgrammaticClear`` and records nothing.
 */
import { computed, onBeforeUnmount } from 'vue'
import { episodeToWire, toastSentence } from '../../utils/providersStatus'
import { forgetProviderStatusToast, isProgrammaticClear } from '../../providers/serviceStatusToast'
import { useProvidersStatusStore } from '../../stores/providersStatus'

const props = defineProps({
    /** Notivue item reference — injected by CustomNotification. */
    item: {
        type: Object,
        default: null,
    },
    /** Wire key of the provider this toast belongs to. */
    provider: {
        type: String,
        required: true,
    },
    /** From ``deriveEpisode``: kind, startedAt, status, resolvedAt. */
    episode: {
        type: Object,
        required: true,
    },
    /** ``{ productLabel, vendorLabel, statusUrl }`` — how to name the provider. */
    identity: {
        type: Object,
        required: true,
    },
})

const sentence = computed(() => toastSentence(props.episode, props.identity))

const statusHost = computed(() => {
    try {
        return new URL(props.identity.statusUrl).host
    } catch {
        return props.identity.statusUrl
    }
})

// Notivue unmounts the body when the item is cleared, by whoever cleared it.
// Only the user's own "×" is an acknowledgment. A page reload does not run
// this hook, so reloading never acknowledges anything.
onBeforeUnmount(() => {
    forgetProviderStatusToast(props.provider, props.item)
    if (isProgrammaticClear(props.item)) return
    useProvidersStatusStore().acknowledge(props.provider, episodeToWire(props.episode))
})
</script>

<template>
    <span class="provider-status-toast">
        {{ sentence }} —
        <a :href="identity.statusUrl" target="_blank" rel="noopener">{{ statusHost }}</a>
    </span>
</template>

<style scoped>
.provider-status-toast a {
    color: inherit;
    text-decoration: underline;
}
</style>
