import { computed, ref, watch, onActivated, onDeactivated, onBeforeUnmount } from 'vue'

/** Mounted chats share a history; only active consumers keep its requests running. */
export function useSessionEventHistory({ store, sessionId, eligible, active }) {
    const state = computed(() => store.localState.sessionEventHistory[sessionId.value] ?? null)
    const owner = Symbol('chat event history'), activated = ref(true)
    let retainedId = null
    watch([sessionId, eligible, active, activated], ([id, canLoad, isActive, isActivated]) => {
        if (retainedId !== null && (retainedId !== id || !canLoad)) {
            store.releaseSessionEventHistory(retainedId, owner)
            retainedId = null
        }
        if (canLoad) {
            store.retainSessionEventHistory(id, owner, isActive && isActivated)
            retainedId = id
        }
    }, { immediate: true, flush: 'sync' })
    onActivated(() => { activated.value = true })
    onDeactivated(() => { activated.value = false })
    onBeforeUnmount(() => {
        if (retainedId !== null) store.releaseSessionEventHistory(retainedId, owner)
    })
    return { state, retry: () => store.retrySessionEventHistory(sessionId.value) }
}
