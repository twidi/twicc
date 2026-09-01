import { defineStore } from 'pinia'
import { ref } from 'vue'

/**
 * Per-provider upstream service status, as pushed whole by the backend's
 * ``providers_status_updated`` frame (on connect, reconnect, and after every
 * write of ``providers-status.json``).
 *
 * Shape: ``{ <provider>: { status, incident, acknowledged } }`` — see
 * ``utils/providersStatus.js`` for the vocabulary and the rules that turn a
 * record into a toast. This store holds the data and the one write path (the
 * acknowledgment); it renders nothing itself.
 */
export const useProvidersStatusStore = defineStore('providersStatus', () => {
    const records = ref({})

    function applyProvidersStatus(remote) {
        records.value = remote && typeof remote === 'object' ? remote : {}
    }

    /**
     * Record that the user dismissed the toast for ``episode`` of ``provider``'s
     * current incident. Nothing is written locally: the backend persists it and
     * broadcasts the updated file, which is what clears the toast — here and on
     * every other tab and device.
     *
     * @param {string} provider
     * @param {{ started_at: string, status: string }} episode - Wire shape.
     */
    function acknowledge(provider, episode) {
        // Lazy import to avoid circular dependency (store → composable → store).
        import('../composables/useWebSocket').then(({ sendAcknowledgeProviderStatus }) => {
            sendAcknowledgeProviderStatus(provider, episode)
        })
    }

    return { records, applyProvidersStatus, acknowledge }
})
