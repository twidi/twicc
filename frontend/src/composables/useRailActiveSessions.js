import { computed, onScopeDispose, shallowReactive, watch } from 'vue'
import { isRailSessionProcess, selectRailActiveSessions } from '../utils/railActiveSessions.js'
import { buildChildrenIndex, sameChildrenIndex, selectRailTreeRows } from '../utils/railActiveSessionsTree.js'
import { getTreeRootWindows } from '../utils/treeRootWindows.js'

// Share outstanding rail loads across consumers and remounts. The store owns
// fetched records; this map only owns the request until it settles.
const inFlightByStore = new WeakMap()

function sameArray(previous, next) {
    return previous?.length === next.length && next.every((value, index) => value === previous[index])
        ? previous : next
}

const ACTIVITY_KEYS = ['processCount', 'pendingRequestCount', 'hasAssistantTurn', 'activeCronCount',
    'backgroundShellCount', 'unreadCount', 'unreadBackgroundCount', 'unreadShellCount']
const sameActivity = (a, b) => !!a && !!b && ACTIVITY_KEYS.every(key => a[key] === b[key])
const NO_CANDIDATES = []

function sameEntries(a, b) {
    return Array.isArray(a) && Array.isArray(b) && a.length === b.length
        && a.every((entry, i) => Object.keys(entry).length === Object.keys(b[i]).length
            && Object.keys(b[i]).every(key => entry[key] === b[i][key]))
}

function sameRow(a, b) {
    if (Object.keys(a).length !== Object.keys(b).length) return false
    return Object.keys(b).every(key => a[key] === b[key]
        || (key === 'activeDescendants' && sameEntries(a.activeDescendants, b.activeDescendants))
        || (key === 'cumulative' && sameActivity(a.cumulative, b.cumulative))
        || (key === 'session' && a.metadataStatus !== 'loaded' && b.metadataStatus !== 'loaded'
            && Object.keys(a.session).length === Object.keys(b.session).length
            && Object.keys(b.session).every(field => a.session[field] === b.session[field])))
}

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
 *
 * With `treeEnabled()` true the rows follow the session tree (spec §14): tree
 * order, and a node for which `isCollapsed(id)` is true becomes one row
 * `{ session, cumulative, metadataStatus }` (no processState, it may be idle).
 * `treeCandidates` lists the rail-active stored sessions whose ancestors the
 * caller must fetch; it is empty with the option off.
 * Missing/error records keep the process snapshot fallback. Retry only after
 * the process leaves and returns, or after this composable mounts again.
 */
export function useRailActiveSessions(store, currentSessionId = () => null, { treeEnabled = () => false, isCollapsed = () => false } = {}) {
    const metadata = shallowReactive(new Map())
    let disposed = false
    const candidates = computed(previous => sameArray(previous, Object.keys(store.processStates)
        .filter(id => isRailSessionProcess(store.processStates[id], store.sessions[id]))))
    // Rebuild tie ordering only when session membership changes, not per process update.
    const sessionOrder = computed(() => new Map(Object.keys(store.sessions).map((id, index) => [id, index])))

    // Load before cron exclusion: full metadata can reveal canonical unread.
    // Track record availability, not metadata status, to avoid fetch loops.
    const metadataEntries = computed(previous => {
        const next = candidates.value.map(id => ({ id, loaded: !!store.sessions[id] }))
        return previous?.length === next.length && next.every((entry, i) => entry.id === previous[i].id && entry.loaded === previous[i].loaded)
            ? previous : next
    })
    watch(metadataEntries, entries => {
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

    const flatSelection = computed(() => selectRailActiveSessions(
        store.processStates, store.sessions, currentSessionId(), sessionOrder.value))

    // Tree-parent children index, read only in tree mode and retained while unchanged.
    const childrenIndex = computed(previous => {
        const next = buildChildrenIndex(store.sessions)
        return previous && sameChildrenIndex(previous, next) ? previous : next
    })
    const treeCandidates = computed(previous => {
        if (!treeEnabled()) return NO_CANDIDATES
        const next = flatSelection.value.map(row => store.sessions[row.session.id]).filter(Boolean)
        return sameArray(previous, next)
    })

    // Window keys of the tree roots: the same tracker as the sidebar, so both agree on the root order.
    const treeRootWindows = getTreeRootWindows(store)

    const rows = computed(previous => {
        const previousRows = new Map(previous?.map(row => [row.session.id, row]) || [])
        const selected = treeEnabled()
            ? selectRailTreeRows(flatSelection.value, {
                processStates: store.processStates, sessions: store.sessions,
                childrenIndex: childrenIndex.value, isCollapsed, viewedId: currentSessionId(),
                rootKey: treeRootWindows.rootKey,
            })
            : flatSelection.value
        const next = selected
            .map(row => ({
                ...row,
                metadataStatus: store.sessions[row.session.id] ? 'loaded' : metadata.get(row.session.id)?.status || 'loading',
            })).map(row => {
                const old = previousRows.get(row.session.id)
                return old && sameRow(old, row) ? old : row
            })
        return sameArray(previous, next)
    })
    return { rows, treeCandidates }
}
