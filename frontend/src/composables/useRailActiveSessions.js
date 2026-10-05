import { computed, onScopeDispose, shallowReactive, watch } from 'vue'
import { isRailSessionProcess, selectRailActiveSessions } from '../utils/railActiveSessions.js'

// Share outstanding rail loads across consumers and remounts. The store owns
// fetched records; this map only owns the request until it settles.
const inFlightByStore = new WeakMap()

function loadMetadata(store, sessionId) {
    let inFlight = inFlightByStore.get(store)
    if (!inFlight) {
        inFlight = new Map()
        inFlightByStore.set(store, inFlight)
    }
    if (!inFlight.has(sessionId)) {
        const request = Promise.resolve()
            .then(() => store.loadSessionById(sessionId))
            .then(session => session ? 'loaded' : 'missing', () => 'error')
            .finally(() => inFlight.delete(sessionId))
        inFlight.set(sessionId, request)
    }
    return inFlight.get(sessionId)
}

/**
 * Reactive global rows for SidebarRail. Call inside the component's scope.
 * Pass the existing data store and a getter for the currently viewed session.
 * No sidebar/project/workspace filters participate in selection.
 *
 * Rows expose session, processState, unread (canonical), hasUnread (visible),
 * pendingRequest, indicatorKind, userTurnBackgroundShells, hasActiveCrons,
 * and metadataStatus: loading | loaded | missing | error.
 * Missing/error records keep the process snapshot fallback. Retry only after
 * the process leaves and returns, or after this composable mounts again.
 */
export function useRailActiveSessions(store, currentSessionId = () => null) {
    const metadata = shallowReactive(new Map())
    let disposed = false
    const candidates = computed(() => Object.keys(store.processStates)
        .filter(id => isRailSessionProcess(store.processStates[id], store.sessions[id])))

    // Load before cron exclusion: full metadata can reveal canonical unread.
    // Track record availability, not metadata status, to avoid fetch loops.
    watch(() => candidates.value.map(id => ({ id, loaded: !!store.sessions[id] })), entries => {
        const active = new Set(entries.map(entry => entry.id))
        for (const id of metadata.keys()) {
            if (!active.has(id)) metadata.delete(id)
        }
        for (const { id, loaded } of entries) {
            if (loaded || metadata.has(id)) continue
            const attempt = { status: 'loading' }
            metadata.set(id, attempt)
            loadMetadata(store, id).then(status => {
                // A stopped/replaced presence and an unmounted rail ignore
                // this result. Only the store loader can write session records.
                if (disposed || metadata.get(id) !== attempt) return
                metadata.set(id, { status })
            })
        }
    }, { immediate: true })

    onScopeDispose(() => { disposed = true })

    const rows = computed(() => selectRailActiveSessions(store.processStates, store.sessions, currentSessionId())
        .map(row => ({
            ...row,
            metadataStatus: store.sessions[row.session.id] ? 'loaded' : metadata.get(row.session.id)?.status || 'loading',
        })))
    return { rows }
}
