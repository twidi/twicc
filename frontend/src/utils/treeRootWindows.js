/**
 * Order key of the tree roots among active entries (design:
 * docs/plans/2026-10-28-tree-root-active-order-design.md).
 *
 * A session X has a window while it holds a live process identity (its own,
 * or one of a session below it in the tree). The window key is fixed when the
 * window opens and does not change while X still holds one of the identities
 * it held at the previous update, so a root keeps its place among active
 * entries for as long as its tree stays active.
 *
 * Windows are history (A starts, B starts, A stops: the smallest live start is
 * now B's), so one tracker per store keeps them, updated eagerly by a watcher
 * once per reactive flush, whether or not anything reads it.
 *
 * Pure apart from `getTreeRootWindows`. No Pinia import, explicit `.js`
 * imports: this module loads under plain `node --test`.
 */

import { computed, effectScope, reactive, toRaw, watch } from 'vue'
import { PROCESS_STATE } from '../constants.js'
import { isBlocking, isParticipant, treeParentId } from './sessionTree.js'
import { noteStageRun } from './stageRuns.js'

/**
 * Ancestry of a live process: the ids of the sessions above its session, nearest
 * first, as `buildSessionTree` links them. The walk follows the tree parent
 * (`treeParentId`) over the loaded rows and stops at a blocking session, at an
 * unloaded link and on a cycle. One bridge: when the process's own row is not
 * loaded, its first link is the first entry of `spawn_ancestors`.
 *
 * @param {string} sessionId
 * @param {?Object} processState - read for `spawn_ancestors` only when the own row is not loaded
 * @param {(id: string) => ?Object} lookup
 * @returns {string[]}
 */
export function processAncestry(sessionId, processState, lookup) {
    const chain = []
    const own = lookup(sessionId)
    // A non-participant (ephemeral, draft) session has no tree parent.
    if (own && !isParticipant(own)) return chain
    let currentId = own ? treeParentId(own) : processState?.spawn_ancestors?.[0] ?? null
    const visited = new Set([sessionId])
    while (currentId && !visited.has(currentId)) {
        visited.add(currentId)
        const current = lookup(currentId)
        if (!current || isBlocking(current)) break
        chain.push(currentId)
        currentId = treeParentId(current)
    }
    return chain
}

/**
 * The tracker inputs of the live processes: one entry per non-synthetic process
 * state, `{ id, starting, startedAt, chain }`. A `STARTING` entry carries no
 * `startedAt` (its value can belong to no process).
 *
 * @param {Object} processStates
 * @param {(id: string) => ?Object} lookup
 * @returns {Array<{ id: string, starting: boolean, startedAt: ?number, chain: string[] }>}
 */
export function collectProcessInputs(processStates, lookup) {
    const inputs = []
    for (const [id, ps] of Object.entries(processStates)) {
        if (!ps || ps.synthetic) continue
        const starting = ps.state === PROCESS_STATE.STARTING
        inputs.push({
            id,
            starting,
            startedAt: starting ? null : (ps.started_at || 0),
            chain: processAncestry(id, ps, lookup),
        })
    }
    return inputs
}

function sameInputs(a, b) {
    return !!a && a.length === b.length && a.every((entry, i) => {
        const other = b[i]
        return entry.id === other.id && entry.starting === other.starting && entry.startedAt === other.startedAt
            && entry.chain.length === other.chain.length && entry.chain.every((id, j) => id === other.chain[j])
    })
}

/** The tracker state before its first update. */
export const NO_ROOT_WINDOWS = Object.freeze({ windows: new Map(), identities: new Map() })

/**
 * One tracker update: a function of the previous windows and the current inputs.
 *
 * - An identity is (session id, `started_at`) of a live, non-synthetic, non-`STARTING`
 *   process. A session in `STARTING` keeps the identity it had at the previous update.
 * - X holds an identity when the identity's session is X or X is in its ancestry.
 * - A window opens when X holds an identity and has no window: its key is X's own
 *   `started_at` when X holds its own identity, otherwise the smallest held `started_at`.
 * - It stays open, with the same key, while X still holds one of the identities it held
 *   at the previous update; its held set is then refreshed. Otherwise it closes, and a new
 *   one opens at once if X still holds identities.
 *
 * @param {{ windows: Map<string, { key: number, held: Map<string, number> }>, identities: Map<string, number> }} previous
 * @param {ReturnType<typeof collectProcessInputs>} inputs
 * @returns {{ windows: Map<string, { key: number, held: Map<string, number> }>, identities: Map<string, number> }}
 */
export function nextRootWindows(previous, inputs) {
    const identities = new Map()
    const holdings = new Map() // X -> Map<session id, started_at> of the identities X holds
    for (const { id, starting, startedAt, chain } of inputs) {
        const startedAtOfIdentity = starting ? previous.identities.get(id) : startedAt
        if (startedAtOfIdentity === undefined) continue
        identities.set(id, startedAtOfIdentity)
        for (const holderId of [id, ...chain]) {
            let held = holdings.get(holderId)
            if (!held) holdings.set(holderId, held = new Map())
            held.set(id, startedAtOfIdentity)
        }
    }
    const windows = new Map()
    for (const [holderId, held] of holdings) {
        const window = previous.windows.get(holderId)
        let carried = false
        if (window) {
            for (const [id, startedAt] of window.held) {
                if (held.get(id) === startedAt) { carried = true; break }
            }
        }
        const key = carried ? window.key
            : held.has(holderId) ? held.get(holderId)
                : Math.min(...held.values())
        windows.set(holderId, { key, held })
    }
    return { windows, identities }
}

// One tracker per raw `processStates` object: the store and its state share it,
// so every caller (sidebar instances, rail) reads the same windows.
const trackers = new WeakMap()

// Dev only (`import.meta.hot` is undefined in the build and under `node --test`).
// A hot reload of this module stops the watchers of the old trackers and hands
// their state to the new module: the new trackers start from it, so the windows
// survive the reload. The callers get the new trackers when they reload too.
const hot = import.meta.hot
const carriedStates = hot ? (hot.data.treeRootStates ?? new WeakMap()) : null
const liveScopes = hot ? new Map() : null // raw `processStates` -> { scope, current }
if (hot) {
    hot.dispose(data => {
        for (const [rawStates, { scope, current }] of liveScopes) {
            scope.stop()
            carriedStates.set(rawStates, current())
        }
        data.treeRootStates = carriedStates
    })
}

/**
 * The tracker of `owner` (the data store, or any `{ sessions, processStates }`
 * facade over the same state), created on the first call and kept up to date
 * eagerly from then on: a watcher, in a detached effect scope, updates it once
 * per reactive flush after any change of its inputs (the live process states,
 * their `started_at` and `spawn_ancestors`, and the tree-parent fields of the
 * loaded rows on the ancestry chains). The data store creates it at app init.
 *
 * `rootKey(id)` is the window key of the session `id` (undefined without a
 * window). Reactive per id: it reruns a reader only when the window of that id
 * opens or closes.
 *
 * @param {{ sessions: Object, processStates: Object }} owner
 * @returns {{ rootKey: (id: string) => (number|undefined), current: () => ReturnType<typeof nextRootWindows> }}
 */
export function getTreeRootWindows(owner) {
    const rawStates = toRaw(owner.processStates)
    let tracker = trackers.get(rawStates)
    if (!tracker) {
        tracker = createTracker(owner, rawStates)
        trackers.set(rawStates, tracker)
    }
    return tracker
}

function createTracker(owner, rawStates) {
    const keys = reactive(new Map())
    let state = carriedStates?.get(rawStates) ?? NO_ROOT_WINDOWS
    const scope = effectScope(true)
    scope.run(() => {
        const inputs = computed(previous => {
            noteStageRun('treeRoots:inputs')
            const next = collectProcessInputs(owner.processStates, id => owner.sessions[id] ?? null)
            return sameInputs(previous, next) ? previous : next
        })
        watch(inputs, (current) => {
            noteStageRun('treeRoots:update')
            state = nextRootWindows(state, current)
            for (const id of [...keys.keys()]) {
                if (!state.windows.has(id)) keys.delete(id)
            }
            for (const [id, { key }] of state.windows) {
                if (keys.get(id) !== key) keys.set(id, key)
            }
        }, { immediate: true })
    })
    const current = () => state
    liveScopes?.set(rawStates, { scope, current })
    return {
        rootKey: id => keys.get(id),
        current,
    }
}
