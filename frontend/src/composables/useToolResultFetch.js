/**
 * The result fetch pipeline of a tool card (design §8.3 "The fetch pipeline
 * of a card").
 *
 * One request slot per card (single flight with tokens), a 3 s ticker that
 * runs exactly while the card wants a fetch, a growing stale threshold that
 * kills a hung request, and the Entry / Leave pair driven by the card being
 * open and active. The only card-specific input is the polling predicate:
 * the pipeline never knows what kind of card it serves.
 *
 * Imports only `vue`: it runs the same in the SPA and in the share bundle,
 * and `node --test` can drive it with a fake fetch and a fake clock.
 */
import { computed, onScopeDispose, ref, shallowRef, watch } from 'vue'

export const POLL_INTERVAL_MS = 3000

// The stale threshold for the Nth stale kill since the last Leave or success
// settle. Past the last entry there is no threshold: the slot is not killed.
export const STALE_THRESHOLDS_MS = [30000, 60000, 120000]

// Wrapped so the globals are never called as methods of another object
// (browsers throw "Illegal invocation" on `timers.setInterval(...)`).
const DEFAULT_TIMERS = {
    setInterval: (fn, ms) => setInterval(fn, ms),
    clearInterval: id => clearInterval(id),
    now: () => Date.now(),
}

/**
 * The polling predicate of a non-agent card: the tool still runs, or it
 * holds fewer rows than its display count (not after a failed fetch, not
 * for a tool from before the session's cutoff), or a result arrived that no
 * request has seen yet. The frozen-transcript gate belongs to the pipeline
 * (`showsPending`), not to the predicate.
 */
export function genericCardPredicate({
    isToolRunning, rowCount, displayCount, lastSettleFailed, isStaleToolUse, countChanged,
}) {
    return !!isToolRunning
        || (rowCount < displayCount && !lastSettleFailed && !isStaleToolUse)
        || !!countChanged
}

/**
 * Build the fetch pipeline of one card.
 *
 * Every input is a getter (a closure over the card's reactive state), so the
 * refs this returns can be declared early in `<script setup>` while the
 * getters read computeds declared later. Nothing evaluates a getter before
 * `start()`, which creates the watchers: call it once, as the last statement
 * of setup.
 *
 * @param {object} options
 * @param {(signal: AbortSignal) => Promise<Array>} options.fetchRows - the `results` list
 * @param {() => boolean} options.open - the Result section (or inline result) is visible
 * @param {() => boolean} options.active - the card's session is active (KeepAlive)
 * @param {() => number} options.count - `toolState?.resultCount ?? 0`
 * @param {() => number} options.displayCount - rows needed before the result renders
 * @param {(state: {rowCount: number, count: number, countChanged: boolean,
 *          lastSettleFailed: boolean}) => boolean} options.predicate - the polling predicate
 * @param {() => boolean} options.transcriptFrozen - a frozen share snapshot
 * @param {() => number} options.connectionEpoch - bumped when a reconnect refresh ends
 * @param {{setInterval: Function, clearInterval: Function, now: () => number}} [options.timers]
 */
export function useToolResultFetch({
    fetchRows,
    open,
    active,
    count,
    displayCount,
    predicate,
    transcriptFrozen,
    connectionEpoch,
    timers = DEFAULT_TIMERS,
}) {
    const resultState = ref('idle') // 'idle' | 'loading' | 'loaded' | 'error'
    const resultData = ref(null)
    const resultError = ref(null)
    // The `count` read at Start by the last request that settled as current
    // with a success, or with an error once `errorStreak` >= 3. Null until then.
    const fetchedAtCount = ref(null)
    const errorStreak = ref(0)
    const lastSettleFailed = ref(false)
    const refetchPending = ref(false)
    // The request slot: `{ token, startedAt, controller }` while busy, null when free.
    const slot = shallowRef(null)
    // Stale kills since the last Leave or success settle (never read reactively).
    let staleKills = 0
    let lastToken = 0
    let intervalId = null

    const rowCount = computed(() => resultData.value?.length ?? 0)
    const countChanged = computed(() => fetchedAtCount.value !== null && count() !== fetchedAtCount.value)
    // The card shows rows: `displayResult` is non-null.
    const showsRows = computed(() => rowCount.value >= Math.max(1, displayCount()))

    const showsPending = computed(() => !transcriptFrozen() && !!predicate({
        rowCount: rowCount.value,
        count: count(),
        countChanged: countChanged.value,
        lastSettleFailed: lastSettleFailed.value,
    }))

    const openAndActive = computed(() => !!(open() && active()))

    const wantsFetch = computed(() => openAndActive.value && (
        showsPending.value
        || resultState.value === 'idle'
        || resultState.value === 'loading'
        || refetchPending.value
        || slot.value !== null
    ))

    // Nothing will retry by itself, yet the rows on screen may be out of date.
    const refreshNote = computed(() => showsRows.value && lastSettleFailed.value
        && (errorStreak.value >= 3 || !showsPending.value))

    // --- Rule 1: single flight with tokens ---

    function requestFetch() {
        if (slot.value !== null) {
            refetchPending.value = true
            return
        }
        startRequest()
    }

    // --- Rule 2: start ---

    function startRequest() {
        const token = ++lastToken
        const controller = new AbortController()
        const countAtStart = count()
        if (resultState.value === 'idle') resultState.value = 'loading'
        slot.value = { token, startedAt: timers.now(), controller }
        let promise
        try {
            promise = Promise.resolve(fetchRows(controller.signal))
        } catch (err) {
            promise = Promise.reject(err)
        }
        promise.then(
            rows => settle(token, countAtStart, true, rows),
            err => settle(token, countAtStart, false, err),
        )
    }

    // --- Rule 3: settle ---

    function settle(token, countAtStart, ok, payload) {
        // Invalidated (Leave, stale kill): write nothing, not even the slot.
        if (slot.value?.token !== token) return
        if (ok) {
            resultData.value = payload
            resultState.value = 'loaded'
            resultError.value = null
            fetchedAtCount.value = countAtStart
            errorStreak.value = 0
            staleKills = 0
            lastSettleFailed.value = false
        } else {
            resultError.value = payload?.message ?? String(payload)
            lastSettleFailed.value = true
            errorStreak.value += 1
            // A card that shows rows keeps them (and 'loaded') through a failed refresh.
            if (!showsRows.value) resultState.value = 'error'
            if (errorStreak.value >= 3) fetchedAtCount.value = countAtStart
        }
        slot.value = null
        if (refetchPending.value && wantsFetch.value) {
            refetchPending.value = false
            requestFetch()
        }
    }

    // --- Rule 4: ticker ---

    function startTicker() {
        if (intervalId !== null) return
        intervalId = timers.setInterval(tick, POLL_INTERVAL_MS)
    }

    function stopTicker() {
        if (intervalId === null) return
        timers.clearInterval(intervalId)
        intervalId = null
    }

    function tick() {
        if (!wantsFetch.value) return
        const current = slot.value
        if (current === null) {
            if (showsPending.value || resultState.value === 'idle' || resultState.value === 'loading') {
                requestFetch()
            }
            return
        }
        const threshold = STALE_THRESHOLDS_MS[staleKills]
        if (threshold === undefined || timers.now() - current.startedAt <= threshold) return
        // Stale kill: abort (SPA path; the share path ignores the signal and
        // its late settle is dropped by the token check), free, retry.
        current.controller.abort()
        slot.value = null
        staleKills += 1
        requestFetch()
    }

    // --- Rule 5: Leave ---

    function leave() {
        stopTicker()
        const current = slot.value
        if (current !== null) {
            current.controller.abort()
            slot.value = null
        }
        refetchPending.value = false
        staleKills = 0
        errorStreak.value = 0
        // No request ever settled: back to the never-loaded state.
        if (resultState.value === 'loading') resultState.value = 'idle'
    }

    // --- Rule 6: Entry, and the watchers ---

    function entry() {
        if (wantsFetch.value || lastSettleFailed.value) requestFetch()
    }

    let started = false

    function start() {
        if (started) return
        started = true

        watch(openAndActive, (isOn, wasOn) => {
            if (isOn) entry()
            else if (wasOn) leave()
        }, { immediate: true })

        watch(wantsFetch, (wants) => {
            if (wants) startTicker()
            else stopTicker()
        }, { immediate: true })

        watch(count, () => {
            if (openAndActive.value) requestFetch()
        })

        watch(connectionEpoch, () => {
            if (openAndActive.value && lastSettleFailed.value) {
                errorStreak.value = 0
                requestFetch()
            }
        })

        // Unmount (or the test's effect scope stopping): Leave.
        onScopeDispose(leave)
    }

    return {
        resultState,
        resultData,
        resultError,
        errorStreak,
        lastSettleFailed,
        countChanged,
        showsPending,
        wantsFetch,
        refreshNote,
        start,
    }
}
