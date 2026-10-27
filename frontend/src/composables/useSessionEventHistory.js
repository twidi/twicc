import { computed, watch, onActivated } from 'vue'

/** The chat opens a cache; the loaded session owns and disposes it. */
export function useSessionEventHistory({ store, sessionId, eligible, active }) {
    const state = computed(() => store.localState.sessionEventHistory[sessionId.value] ?? null)
    watch([sessionId, eligible, active], ([id, canLoad, isActive], previous) => {
        if (!canLoad) return
        const cached = !!store.localState.sessionEventHistory[id]
        store.ensureSessionEventHistory(id)
        if (cached && isActive && previous && (!previous[2] || previous[0] !== id)) store.refreshSessionEventHistory(id)
    }, { immediate: true })
    let activated = false
    onActivated(() => {
        if (activated && eligible.value) store.refreshSessionEventHistory(sessionId.value)
        activated = true
    })
    return { state, retry: () => store.retrySessionEventHistory(sessionId.value) }
}
