import test from 'node:test'
import assert from 'node:assert/strict'
import { effectScope, reactive, watch } from 'vue'

import {
    POLL_INTERVAL_MS,
    STALE_THRESHOLDS_MS,
    genericCardPredicate,
    useToolResultFetch,
} from './useToolResultFetch.js'

// Design §8.3 "The fetch pipeline of a card" and the §9 cases it lists.

// Let every pending promise and Vue scheduler job run.
function flush() {
    return new Promise(resolve => setImmediate(resolve))
}

// A fake clock: intervals and one-shot callbacks fire in time order on
// `advance`, with a flush after each one.
function fakeClock() {
    let now = 0
    let nextId = 1
    const intervals = new Map()
    const oneShots = []
    return {
        setInterval(fn, ms) {
            const id = nextId++
            intervals.set(id, { fn, ms, next: now + ms })
            return id
        },
        clearInterval(id) { intervals.delete(id) },
        now: () => now,
        liveIntervals: () => intervals.size,
        schedule(delay, fn) { oneShots.push({ at: now + delay, fn }) },
        async advance(ms) {
            const target = now + ms
            for (;;) {
                let dueInterval = null
                for (const entry of intervals.values()) {
                    if (entry.next <= target && (!dueInterval || entry.next < dueInterval.next)) dueInterval = entry
                }
                let dueShot = null
                for (const shot of oneShots) {
                    if (shot.at <= target && (!dueShot || shot.at < dueShot.at)) dueShot = shot
                }
                if (!dueInterval && !dueShot) break
                if (dueShot && (!dueInterval || dueShot.at <= dueInterval.next)) {
                    oneShots.splice(oneShots.indexOf(dueShot), 1)
                    now = dueShot.at
                    dueShot.fn()
                } else {
                    now = dueInterval.next
                    dueInterval.next += dueInterval.ms
                    dueInterval.fn()
                }
                await flush()
            }
            now = target
            await flush()
        },
    }
}

// A fake `fetchRows`: each call returns a deferred promise the test settles.
// `abortAware` mimics the SPA path (an abort rejects the request); without it,
// the fake ignores its signal like the share path. `respondAfterMs` settles
// every call on its own after that delay (with `[{ call: <index> }]`).
function fakeFetcher({ abortAware, clock, respondAfterMs = null }) {
    const calls = []
    function fetchRows(signal) {
        let resolve
        let reject
        const promise = new Promise((res, rej) => { resolve = res; reject = rej })
        const index = calls.length
        const call = {
            index,
            signal,
            resolve: rows => resolve(rows),
            reject: (message = 'HTTP 502') => reject(new Error(message)),
        }
        if (abortAware) {
            signal.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError')))
        }
        if (respondAfterMs !== null) clock.schedule(respondAfterMs, () => resolve([{ call: index }]))
        calls.push(call)
        return promise
    }
    return { calls, fetchRows, last: () => calls.at(-1) }
}

// Mount one pipeline in its own effect scope (stopping it = unmount). The
// predicate is `genericCardPredicate` OR `state.term`, a stand-in for any
// other term (e.g. an agent card's open run).
async function mount({ state = {}, abortAware = true, clock = fakeClock(), respondAfterMs = null } = {}) {
    const fetcher = fakeFetcher({ abortAware, clock, respondAfterMs })
    const s = reactive({
        open: true, active: true, count: 0, displayCount: 1, frozen: false, epoch: 0,
        running: false, stale: false, term: false,
        ...state,
    })
    const predicate = p => s.term || genericCardPredicate({
        isToolRunning: s.running,
        rowCount: p.rowCount,
        displayCount: s.displayCount,
        lastSettleFailed: p.lastSettleFailed,
        isStaleToolUse: s.stale,
        countChanged: p.countChanged,
    })
    const scope = effectScope()
    const api = scope.run(() => {
        const pipeline = useToolResultFetch({
            fetchRows: fetcher.fetchRows,
            open: () => s.open,
            active: () => s.active,
            count: () => s.count,
            displayCount: () => s.displayCount,
            predicate,
            transcriptFrozen: () => s.frozen,
            connectionEpoch: () => s.epoch,
            timers: clock,
        })
        pipeline.start()
        return pipeline
    })
    await flush()
    return { api, s, clock, fetcher, calls: fetcher.calls, scope, unmount: () => scope.stop() }
}

// "No result available" is `'loaded'` with no displayable rows and no pending text.
function showsNoResultAvailable(api, displayCount = 1) {
    const rows = api.resultData.value?.length ?? 0
    return api.resultState.value === 'loaded' && rows < Math.max(1, displayCount) && !api.showsPending.value
}

// A finished card holding one row, fetched at `count`, section open.
async function mountLoaded({ count = 1, state = {}, abortAware = true } = {}) {
    const m = await mount({ state: { count, ...state }, abortAware })
    m.calls[0].resolve([{ row: 1 }])
    await flush()
    return m
}

// --- genericCardPredicate ---

test('genericCardPredicate: running, short of rows (not after an error, not stale), or a count change', () => {
    const base = { isToolRunning: false, rowCount: 1, displayCount: 1, lastSettleFailed: false,
        isStaleToolUse: false, countChanged: false }
    assert.equal(genericCardPredicate(base), false)
    assert.equal(genericCardPredicate({ ...base, isToolRunning: true }), true)
    assert.equal(genericCardPredicate({ ...base, rowCount: 0 }), true)
    assert.equal(genericCardPredicate({ ...base, rowCount: 0, lastSettleFailed: true }), false)
    assert.equal(genericCardPredicate({ ...base, rowCount: 0, isStaleToolUse: true }), false)
    assert.equal(genericCardPredicate({ ...base, countChanged: true }), true)
})

test('constants: 3 s ticker, 30/60/120 s stale thresholds', () => {
    assert.equal(POLL_INTERVAL_MS, 3000)
    assert.deepEqual(STALE_THRESHOLDS_MS, [30000, 60000, 120000])
})

// --- Single flight ---

test('single flight: a count change during a request waits for the settle, then one more request', async () => {
    const { api, s, calls, clock } = await mount({ state: { running: true } })
    assert.equal(calls.length, 1)
    s.count = 1
    await flush()
    assert.equal(calls.length, 1, 'no second request while the slot is busy')
    calls[0].resolve([])
    await flush()
    assert.equal(calls.length, 2, 'the pending refetch runs at the settle')
    // A slow response (27 s, under the 30 s threshold) is never killed by a tick.
    await clock.advance(27000)
    assert.equal(calls.length, 2)
    assert.equal(calls[1].signal.aborted, false)
    calls[1].resolve([{ row: 1 }])
    await flush()
    assert.deepEqual(api.resultData.value, [{ row: 1 }])
    assert.equal(api.resultState.value, 'loaded')
})

test('a tick during a slow request does not set refetchPending', async () => {
    const { s, calls, clock } = await mount({ state: { term: true } })
    calls[0].resolve([{ row: 1 }])
    await flush()
    await clock.advance(3000)
    assert.equal(calls.length, 2, 'the tick starts a refresh')
    await clock.advance(9000)
    assert.equal(calls.length, 2, 'ticks with a busy slot only run the stale check')
    s.term = false
    calls[1].resolve([{ row: 1 }])
    await flush()
    assert.equal(calls.length, 2, 'no extra request follows the settle')
    assert.equal(clock.liveIntervals(), 0)
    await clock.advance(30000)
    assert.equal(calls.length, 2)
})

// --- Stale kills ---

for (const abortAware of [true, false]) {
    const path = abortAware ? 'SPA' : 'share'

    test(`responses always 45 s: every other request lands (${path})`, async () => {
        const { api, calls, clock } = await mount({ state: { running: true }, abortAware, respondAfterMs: 45000 })
        const landed = []
        watch(api.resultData, rows => { if (rows?.length) landed.push(rows[0].call) })
        await clock.advance(33000)
        assert.equal(calls.length, 2, 'the first request is killed after 30 s')
        assert.equal(calls[0].signal.aborted, true)
        await clock.advance(45000)
        await flush()
        assert.deepEqual(landed, [1], 'the next request (60 s threshold) lands; the killed one never does')
        assert.equal(api.resultState.value, 'loaded')
        await clock.advance(600000)
        await flush()
        assert.ok(landed.length >= 5, `later refreshes land too (${landed.length})`)
        assert.ok(!landed.includes(0) && !landed.includes(2), 'a killed request never lands')
    })

    test(`responses slower than every threshold: the fourth request is not killed (${path})`, async () => {
        const { api, calls, clock } = await mount({ state: { running: true }, abortAware, respondAfterMs: 500000 })
        await clock.advance(33000)
        assert.equal(calls.length, 2, 'first kill after 30 s')
        await clock.advance(63000)
        assert.equal(calls.length, 3, 'second kill after 60 s')
        await clock.advance(123000)
        assert.equal(calls.length, 4, 'third kill after 120 s')
        await clock.advance(499000)
        assert.equal(calls.length, 4, 'no threshold after 3 kills')
        await clock.advance(1000)
        await flush()
        assert.deepEqual(api.resultData.value, [{ call: 3 }], 'the fourth request lands')
        // The success settle resets the count: the next request is killed after 30 s again.
        await clock.advance(3000)
        assert.equal(calls.length, 5)
        await clock.advance(33000)
        assert.equal(calls.length, 6)
    })

    test(`a Leave resets the stale kill count (${path})`, async () => {
        const { s, calls, clock } = await mount({ state: { running: true }, abortAware })
        await clock.advance(33000)
        await clock.advance(63000)
        await clock.advance(123000)
        assert.equal(calls.length, 4)
        s.open = false
        await flush()
        s.open = true
        await flush()
        assert.equal(calls.length, 5)
        await clock.advance(33000)
        assert.equal(calls.length, 6, 'killed after 30 s again')
    })
}

test('staleKills reset: 3 kills, a success, then a hanging request is killed after 30 s again', async () => {
    const { calls, clock } = await mount({ state: { running: true } })
    await clock.advance(33000)
    await clock.advance(63000)
    await clock.advance(123000)
    assert.equal(calls.length, 4)
    calls[3].resolve([{ row: 1 }])
    await flush()
    await clock.advance(3000)
    assert.equal(calls.length, 5)
    await clock.advance(30000)
    assert.equal(calls.length, 5, 'not killed at exactly 30 s')
    await clock.advance(3000)
    assert.equal(calls.length, 6)
    assert.equal(calls[4].signal.aborted, true)
})

test('a request that never settles: killed after 30 s, also the first one; the late settle is ignored', async () => {
    // A stale tool: the predicate is false, only `'loading'` keeps the ticker.
    const { api, calls, clock } = await mount({ state: { stale: true }, abortAware: false })
    assert.equal(api.resultState.value, 'loading')
    assert.equal(clock.liveIntervals(), 1)
    await clock.advance(33000)
    assert.equal(calls.length, 2)
    assert.equal(api.resultState.value, 'loading')
    assert.equal(api.resultError.value, null, 'no error text')
    calls[0].resolve([{ late: true }])
    await flush()
    assert.equal(api.resultData.value, null, 'the late settle is ignored')
    assert.equal(api.resultState.value, 'loading')
    await clock.advance(63000)
    assert.equal(calls.length, 3, 'retry after 60 s')
    await clock.advance(123000)
    assert.equal(calls.length, 4, 'retry after 120 s')
    await clock.advance(600000)
    assert.equal(calls.length, 4, 'then none')
    assert.equal(api.resultError.value, null)
})

test('ticker at mount: a card created open with a true predicate requests every 3 s', async () => {
    const { api, calls, clock } = await mount({ state: { running: true } })
    assert.equal(calls.length, 1)
    assert.equal(clock.liveIntervals(), 1)
    calls[0].resolve([])
    await flush()
    assert.equal(api.showsPending.value, true)
    for (let i = 2; i <= 4; i++) {
        await clock.advance(3000)
        assert.equal(calls.length, i)
        calls.at(-1).resolve([])
        await flush()
    }
})

// --- Entry / Leave ---

test('Entry after an error with the Result section closed: no request; opening the section retries once', async () => {
    const { api, s, calls, clock } = await mount()
    calls[0].reject()
    await flush()
    assert.equal(api.resultState.value, 'error')
    assert.equal(clock.liveIntervals(), 0)
    // Card closed (its section closes with it), then reopened with the section closed.
    s.open = false
    await flush()
    // Reactivation with the section closed.
    s.active = false
    await flush()
    s.active = true
    await flush()
    assert.equal(calls.length, 1)
    s.open = true
    await flush()
    assert.equal(calls.length, 2, 'one retry')
    calls[1].reject()
    await flush()
    await clock.advance(30000)
    assert.equal(calls.length, 2, 'no ticker after the retry fails')
})

test('Entry on a finished card with its rows and no failed fetch: no request', async () => {
    const { s, calls, clock } = await mountLoaded({ count: 1 })
    assert.equal(clock.liveIntervals(), 0)
    for (const knob of ['open', 'active']) {
        s[knob] = false
        await flush()
        s[knob] = true
        await flush()
    }
    assert.equal(calls.length, 1)
})

test('no flicker: a loaded card never returns to loading, with rows or with 0 rows, across deactivation', async () => {
    for (const rows of [[{ row: 1 }], []]) {
        const { api, s, calls, clock } = await mount({ state: { running: true } })
        const states = []
        watch(api.resultState, v => states.push(v), { flush: 'sync' })
        calls[0].resolve(rows)
        await flush()
        await clock.advance(3000)
        assert.equal(calls.length, 2)
        assert.equal(api.resultState.value, 'loaded')
        if (!rows.length) assert.equal(api.showsPending.value, true)
        calls[1].resolve(rows)
        await flush()
        s.active = false
        await flush()
        s.active = true
        await flush()
        assert.equal(calls.length, 3)
        assert.equal(api.resultState.value, 'loaded')
        assert.deepEqual(states, ['loaded'], `states seen: ${states}`)
    }
})

test('reopen / reactivate a running generic card with 0 rows: pending at once, never "No result available"', async () => {
    const { api, s, calls } = await mount({ state: { running: true } })
    calls[0].resolve([])
    await flush()
    for (const knob of ['open', 'active']) {
        s[knob] = false
        await flush()
        s[knob] = true
        assert.equal(api.showsPending.value, true)
        assert.equal(showsNoResultAvailable(api), false)
        await flush()
        assert.equal(showsNoResultAvailable(api), false)
    }
    assert.equal(calls.length, 3)
})

test('no stuck state: a first request in flight, Leave → idle, Entry → a new request', async () => {
    for (const knob of ['open', 'active']) {
        const { api, s, calls } = await mount()
        assert.equal(api.resultState.value, 'loading')
        s[knob] = false
        await flush()
        assert.equal(api.resultState.value, 'idle')
        assert.equal(calls[0].signal.aborted, true)
        s[knob] = true
        await flush()
        assert.equal(calls.length, 2)
        assert.equal(api.resultState.value, 'loading')
    }
})

test('Leave after a stale kill of the first request: idle again, the next Entry fetches', async () => {
    const { api, s, calls, clock } = await mount({ state: { stale: true } })
    await clock.advance(33000)
    assert.equal(calls.length, 2)
    s.open = false
    await flush()
    assert.equal(api.resultState.value, 'idle')
    assert.equal(clock.liveIntervals(), 0)
    s.open = true
    await flush()
    assert.equal(calls.length, 3)
})

test('slot ownership (share, no abort): the old settle writes nothing and does not free the new slot', async () => {
    const { api, s, calls } = await mount({ abortAware: false })
    s.open = false
    await flush()
    s.open = true
    await flush()
    assert.equal(calls.length, 2, 'the reopen starts its own request at once')
    calls[0].resolve([{ old: true }])
    await flush()
    assert.equal(api.resultData.value, null)
    assert.equal(api.resultState.value, 'loading')
    // The new request still owns the slot: a count change only marks a refetch.
    s.count = 1
    await flush()
    assert.equal(calls.length, 2)
    calls[1].resolve([{ row: 1 }])
    await flush()
    assert.deepEqual(api.resultData.value, [{ row: 1 }])
    assert.equal(calls.length, 3, 'the pending refetch follows the current settle')
})

test('success with 0 rows and a false predicate ends loaded; an error with 0 rows ends in error', async () => {
    const ok = await mount({ state: { stale: true } })
    ok.calls[0].resolve([])
    await flush()
    assert.equal(ok.api.resultState.value, 'loaded')
    assert.deepEqual(ok.api.resultData.value, [])
    assert.equal(showsNoResultAvailable(ok.api), true)
    assert.equal(ok.clock.liveIntervals(), 0)
    const ko = await mount({ state: { stale: true } })
    ko.calls[0].reject('HTTP 500')
    await flush()
    assert.equal(ko.api.resultState.value, 'error')
    assert.equal(ko.api.resultError.value, 'HTTP 500')
    assert.equal(ko.clock.liveIntervals(), 0)
})

test('a frozen transcript fetches once and never polls', async () => {
    const { api, calls, clock } = await mount({ state: { running: true, frozen: true } })
    calls[0].resolve([])
    await flush()
    assert.equal(api.showsPending.value, false)
    assert.equal(clock.liveIntervals(), 0)
    await clock.advance(30000)
    assert.equal(calls.length, 1)
})

// --- Errors ---

test('persistent fetch error with count ≥ 1: a first error runs no ticker', async () => {
    const { api, calls, clock } = await mount({ state: { count: 1 } })
    calls[0].reject()
    await flush()
    assert.equal(api.countChanged.value, false)
    assert.equal(clock.liveIntervals(), 0)
    await clock.advance(30000)
    assert.equal(calls.length, 1)
})

test('persistent fetch error on a count change: 3 tries in total, then stop; reopen retries once', async () => {
    const { api, s, calls, clock } = await mountLoaded({ count: 1 })
    s.count = 2
    await flush()
    assert.equal(calls.length, 2)
    calls[1].reject()
    await flush()
    assert.equal(api.countChanged.value, true)
    await clock.advance(3000)
    assert.equal(calls.length, 3)
    calls[2].reject()
    await flush()
    await clock.advance(3000)
    assert.equal(calls.length, 4)
    calls[3].reject()
    await flush()
    assert.equal(api.countChanged.value, false, 'the third error records fetchedAtCount')
    assert.equal(clock.liveIntervals(), 0)
    await clock.advance(30000)
    assert.equal(calls.length, 4)
    s.open = false
    await flush()
    s.open = true
    await flush()
    assert.equal(calls.length, 5, 'reopen retries once')
    calls[4].reject()
    await flush()
    await clock.advance(30000)
    assert.equal(calls.length, 5)
})

test('transient error on a count change: the next tick succeeds and the row shows without a reopen', async () => {
    const { api, s, calls, clock } = await mountLoaded({ count: 1 })
    s.count = 2
    await flush()
    calls[1].reject()
    await flush()
    await clock.advance(3000)
    assert.equal(calls.length, 3)
    calls[2].resolve([{ row: 1 }, { row: 2 }])
    await flush()
    assert.equal(api.resultData.value.length, 2)
    assert.equal(api.lastSettleFailed.value, false)
    assert.equal(api.resultError.value, null, 'the success clears the error text')
    assert.equal(clock.liveIntervals(), 0)
})

test('long error streak: a count change, then the other term ends → the next error records the count', async () => {
    const { api, s, calls, clock } = await mountLoaded({ count: 1, state: { term: true } })
    for (let i = 0; i < 4; i++) {
        await clock.advance(3000)
        calls.at(-1).reject()
        await flush()
    }
    assert.equal(api.errorStreak.value, 4)
    const before = calls.length
    s.count = 2
    await flush()
    assert.equal(calls.length, before + 1)
    s.term = false
    assert.equal(api.showsPending.value, true, 'the count term still holds')
    calls.at(-1).reject()
    await flush()
    assert.equal(api.countChanged.value, false)
    assert.equal(api.showsPending.value, false)
    assert.equal(clock.liveIntervals(), 0)
    await clock.advance(30000)
    assert.equal(calls.length, before + 1)
})

test('a request that fails while a count change is pending fetches again; otherwise the error stays', async () => {
    const { s, calls } = await mountLoaded({ count: 1 })
    s.count = 2
    await flush()
    s.count = 3
    await flush()
    assert.equal(calls.length, 2)
    calls[1].reject()
    await flush()
    assert.equal(calls.length, 3, 'the settle consumes refetchPending')
    const other = await mount()
    other.calls[0].reject('HTTP 503')
    await flush()
    await other.clock.advance(30000)
    assert.equal(other.calls.length, 1)
    assert.equal(other.api.resultState.value, 'error')
    assert.equal(other.api.resultError.value, 'HTTP 503')
})

test('error with rows held: rows kept and retried; fewer rows than the display count shows the error', async () => {
    const { api, calls, clock } = await mount({ state: { running: true } })
    calls[0].resolve([{ row: 1 }])
    await flush()
    await clock.advance(3000)
    calls[1].reject()
    await flush()
    assert.equal(api.resultState.value, 'loaded')
    assert.deepEqual(api.resultData.value, [{ row: 1 }])
    assert.equal(api.refreshNote.value, false, 'the ticker still retries')
    await clock.advance(3000)
    assert.equal(calls.length, 3)
    const short = await mount({ state: { displayCount: 2, term: true } })
    short.calls[0].resolve([{ ack: true }])
    await flush()
    await short.clock.advance(3000)
    short.calls[1].reject()
    await flush()
    assert.equal(short.api.resultState.value, 'error')
})

test('error streak with rows kept: the note after 3 failures, cleared by a success', async () => {
    const { api, calls, clock } = await mount({ state: { term: true } })
    calls[0].resolve([{ ack: true }])
    await flush()
    for (let i = 0; i < 3; i++) {
        await clock.advance(3000)
        calls.at(-1).reject()
        await flush()
        assert.equal(api.refreshNote.value, i === 2)
    }
    await clock.advance(3000)
    calls.at(-1).resolve([{ ack: true }])
    await flush()
    assert.equal(api.refreshNote.value, false)
    // The success reset the streak: one more failure is a streak of 1, no note.
    await clock.advance(3000)
    calls.at(-1).reject()
    await flush()
    assert.equal(api.errorStreak.value, 1)
    assert.equal(api.refreshNote.value, false)
})

test('error streak with rows kept: the note survives a failed reopen retry and a failed epoch retry', async () => {
    const { api, s, calls, clock } = await mount({ state: { term: true } })
    calls[0].resolve([{ ack: true }])
    await flush()
    for (let i = 0; i < 3; i++) {
        await clock.advance(3000)
        calls.at(-1).reject()
        await flush()
    }
    s.term = false
    s.open = false
    await flush()
    s.open = true
    await flush()
    const reopened = calls.length
    calls.at(-1).reject()
    await flush()
    assert.equal(api.errorStreak.value, 1)
    assert.equal(api.showsPending.value, false)
    assert.equal(api.refreshNote.value, true)
    s.epoch += 1
    await flush()
    assert.equal(calls.length, reopened + 1)
    calls.at(-1).reject()
    await flush()
    assert.equal(api.errorStreak.value, 1)
    assert.equal(api.refreshNote.value, true)
})

test('connection epoch: retries once after a 3-try streak and shows the row; no-op without a failed settle', async () => {
    const { api, s, calls, clock } = await mountLoaded({ count: 1 })
    s.count = 2
    await flush()
    calls[1].reject()
    await flush()
    await clock.advance(3000)
    calls[2].reject()
    await flush()
    await clock.advance(3000)
    calls[3].reject()
    await flush()
    assert.equal(clock.liveIntervals(), 0)
    s.epoch += 1
    await flush()
    assert.equal(calls.length, 5)
    assert.equal(api.errorStreak.value, 0)
    calls[4].resolve([{ row: 1 }, { row: 2 }])
    await flush()
    assert.equal(api.resultData.value.length, 2)
    s.epoch += 1
    await flush()
    assert.equal(calls.length, 5, 'lastSettleFailed false: nothing')
})

test('a finished card\'s one-off Entry or epoch retry that hangs is stale-killed', async () => {
    for (const trigger of ['entry', 'epoch']) {
        const { s, calls, clock } = await mount()
        calls[0].reject()
        await flush()
        if (trigger === 'entry') {
            s.open = false
            await flush()
            s.open = true
        } else {
            s.epoch += 1
        }
        await flush()
        assert.equal(calls.length, 2)
        assert.equal(clock.liveIntervals(), 1, 'the busy slot keeps wantsFetch true')
        await clock.advance(33000)
        assert.equal(calls.length, 3)
        assert.equal(calls[1].signal.aborted, true)
    }
})

// --- Closed and deactivated cards ---

test('a card whose section closes or that deactivates runs no ticker and sends no request', async () => {
    for (const knob of ['open', 'active']) {
        const { s, calls, clock } = await mount({ state: { running: true } })
        calls[0].resolve([])
        await flush()
        await clock.advance(3000)
        s[knob] = false
        await flush()
        assert.equal(clock.liveIntervals(), 0)
        const before = calls.length
        s.count += 1
        await flush()
        await clock.advance(30000)
        assert.equal(calls.length, before)
    }
})

test('Leave on the SPA path aborts the in-flight request: on close and on unmount', async () => {
    const closed = await mount()
    closed.s.open = false
    await flush()
    assert.equal(closed.calls[0].signal.aborted, true)
    const unmounted = await mount()
    unmounted.unmount()
    assert.equal(unmounted.calls[0].signal.aborted, true)
    assert.equal(unmounted.clock.liveIntervals(), 0)
})

test('Leave on a hidden section with refetchPending set: no ticker left, fetches again when shown', async () => {
    const { s, calls, clock } = await mount({ abortAware: false })
    s.count = 1
    await flush()
    s.open = false
    await flush()
    assert.equal(clock.liveIntervals(), 0)
    calls[0].resolve([])
    await flush()
    assert.equal(calls.length, 1, 'the dropped settle does not consume a refetch')
    s.open = true
    await flush()
    assert.equal(calls.length, 2)
    // Leave cleared refetchPending: the Entry request's settle starts nothing more.
    calls[1].resolve([{ row: 1 }])
    await flush()
    assert.equal(calls.length, 2)
    assert.equal(clock.liveIntervals(), 0)
})

test('Entry mirror: a section hidden during its first request, then shown again, fetches at once', async () => {
    const { s, calls } = await mount()
    s.open = false
    await flush()
    s.open = true
    await flush()
    assert.equal(calls.length, 2)
})

test('own run closing while polling: pending text stops at once; the ticker at the in-flight settle', async () => {
    const idle = await mount({ state: { term: true } })
    idle.calls[0].resolve([{ ack: true }])
    await flush()
    idle.s.term = false
    assert.equal(idle.api.showsPending.value, false)
    await flush()
    assert.equal(idle.clock.liveIntervals(), 0)

    const busy = await mount({ state: { term: true } })
    busy.calls[0].resolve([{ ack: true }])
    await flush()
    await busy.clock.advance(3000)
    assert.equal(busy.calls.length, 2)
    busy.s.term = false
    assert.equal(busy.api.showsPending.value, false)
    await flush()
    assert.equal(busy.clock.liveIntervals(), 1, 'the busy slot keeps the ticker')
    await busy.clock.advance(6000)
    assert.equal(busy.calls.length, 2, 'ticks only run the stale check')
    busy.calls[1].resolve([{ ack: true }, { answer: true }])
    await flush()
    assert.equal(busy.api.resultData.value.length, 2, 'the in-flight request applies its rows')
    assert.equal(busy.clock.liveIntervals(), 0)
    await busy.clock.advance(30000)
    assert.equal(busy.calls.length, 2)
})

// --- Non-agent cards (genericCardPredicate) ---

test('non-agent: a running tool reopened with complete display rows polls again', async () => {
    const { s, calls, clock } = await mount({ state: { running: true } })
    calls[0].resolve([{ row: 1 }])
    await flush()
    s.open = false
    await flush()
    s.open = true
    await flush()
    assert.equal(calls.length, 2)
    assert.equal(clock.liveIntervals(), 1)
})

test('non-agent: a tool from before the session cutoff stops polling, with rows and with none', async () => {
    for (const rows of [[{ row: 1 }], []]) {
        const { api, calls, clock } = await mount({ state: { stale: true } })
        calls[0].resolve(rows)
        await flush()
        assert.equal(clock.liveIntervals(), 0)
        if (!rows.length) assert.equal(showsNoResultAvailable(api), true)
        await clock.advance(30000)
        assert.equal(calls.length, 1)
    }
})

test('non-agent: a failed card retries once on an epoch change and once on a count change', async () => {
    const { s, calls, clock } = await mount()
    calls[0].reject()
    await flush()
    s.epoch += 1
    await flush()
    assert.equal(calls.length, 2)
    calls[1].reject()
    await flush()
    s.count = 1
    await flush()
    assert.equal(calls.length, 3)
    calls[2].reject()
    await flush()
    await clock.advance(30000)
    assert.equal(calls.length, 3)
})

test('non-agent: a finished card whose fetch fails shows the error and runs no ticker', async () => {
    const { api, calls, clock } = await mount({ state: { count: 1 } })
    calls[0].reject()
    await flush()
    assert.equal(api.resultState.value, 'error')
    assert.equal(clock.liveIntervals(), 0)
})

test('non-agent: a running card whose fetch fails retries every 3 s until the tool stops', async () => {
    const { api, s, calls, clock } = await mount({ state: { running: true } })
    calls[0].reject()
    await flush()
    assert.equal(api.resultState.value, 'error')
    for (let i = 2; i <= 4; i++) {
        await clock.advance(3000)
        assert.equal(calls.length, i)
        calls.at(-1).reject()
        await flush()
    }
    s.running = false
    await flush()
    assert.equal(clock.liveIntervals(), 0)
    await clock.advance(30000)
    assert.equal(calls.length, 4)
})

test('non-agent: the final row lands during a request or while the section is closed → fetched', async () => {
    const during = await mount({ state: { running: true } })
    during.s.count = 1
    await flush()
    during.s.running = false
    during.calls[0].resolve([])
    await flush()
    assert.equal(during.calls.length, 2)
    during.calls[1].resolve([{ row: 1 }])
    await flush()
    assert.deepEqual(during.api.resultData.value, [{ row: 1 }])
    assert.equal(during.clock.liveIntervals(), 0)

    const closed = await mountLoaded({ count: 1 })
    closed.s.open = false
    await flush()
    closed.s.count = 2
    await flush()
    assert.equal(closed.calls.length, 1)
    closed.s.open = true
    await flush()
    assert.equal(closed.calls.length, 2)
})

test('compaction-duplicated tool_use line (resultCount 4, 2 rows per card): one fetch per count change', async () => {
    const { api, s, calls, clock } = await mount({ state: { count: 2 } })
    calls[0].resolve([{ row: 1 }, { row: 2 }])
    await flush()
    s.count = 4
    await flush()
    assert.equal(calls.length, 2)
    calls[1].resolve([{ row: 1 }, { row: 2 }])
    await flush()
    assert.equal(api.countChanged.value, false)
    assert.equal(clock.liveIntervals(), 0)
    await clock.advance(60000)
    assert.equal(calls.length, 2)
})

// --- Control card: expected count 2, display count 1 (Task 17, design §8.3) ---

test('display count: a control card with expected 2 / display 1 shows the ack at once, then fetches the second row on the count flip', async () => {
    // `term` stands in for the agent card's own-run term (`needsMoreRows`):
    // its run is open and it holds fewer rows than its expected count (2),
    // while `displayCount` stays forced to 1 for control cards.
    const { api, s, calls, clock } = await mount({ state: { count: 1, displayCount: 1, term: true } })
    calls[0].resolve([{ ack: true }])
    await flush()
    assert.equal(api.resultState.value, 'loaded')
    assert.deepEqual(api.resultData.value, [{ ack: true }], 'the ack shows at once, at 1 row')
    assert.equal(api.showsPending.value, true, 'the own run is still open, so the card keeps polling')
    assert.equal(api.wantsFetch.value, true)
    assert.equal(clock.liveIntervals(), 1)

    // The first result set `opensRun`: the card's expected count flips from
    // 1 to 2. The pipeline itself only sees `toolState.resultCount` change —
    // the `count` watcher (rule 6) fetches the second row as soon as it lands.
    s.count = 2
    await flush()
    assert.equal(calls.length, 2, 'the count change triggers a new request')
    calls[1].resolve([{ ack: true }, { end: true }])
    await flush()
    assert.deepEqual(api.resultData.value, [{ ack: true }, { end: true }])

    // The run has now closed: the own-run term drops out, and the card stops.
    s.term = false
    await flush()
    assert.equal(api.showsPending.value, false)
    assert.equal(api.wantsFetch.value, false)
    assert.equal(clock.liveIntervals(), 0)
})

// --- Interval hygiene ---

test('interval hygiene: live intervals always equal the instances that want a fetch', async () => {
    const clock = fakeClock()
    const cards = []
    for (let i = 0; i < 40; i++) {
        cards.push(await mount({
            clock,
            state: { running: i % 3 === 0, stale: i % 3 === 1, term: i % 7 === 0 },
            abortAware: i % 2 === 0,
        }))
    }
    const alive = new Set(cards)
    function check(step) {
        const expected = [...alive].filter(c => c.api.wantsFetch.value).length
        assert.equal(clock.liveIntervals(), expected, step)
    }
    check('mounted')
    cards.forEach((c, i) => { if (i % 4 !== 3) c.calls[0].resolve(i % 2 ? [] : [{ row: i }]) })
    await flush()
    check('first settles')
    await clock.advance(3000)
    check('first tick')
    cards.forEach((c, i) => { if (i % 5 === 0) c.s.open = false })
    await flush()
    check('some closed')
    cards.forEach((c, i) => { if (i % 5 === 1) c.s.active = false })
    await flush()
    check('some deactivated')
    cards.forEach((c, i) => { if (i % 5 === 2) { c.unmount(); alive.delete(c) } })
    await flush()
    check('some unmounted')
    cards.forEach((c, i) => { if (i % 6 === 0) c.s.running = !c.s.running })
    await flush()
    check('predicates flipped')
    cards.forEach(c => c.calls.filter(call => !call.signal.aborted).forEach(call => call.resolve([{ row: 0 }])))
    await flush()
    check('all settled')
    cards.forEach((c, i) => { if (i % 5 === 0) c.s.open = true; if (i % 5 === 1) c.s.active = true })
    await flush()
    check('reopened and reactivated')
    await clock.advance(40000)
    check('after 40 s')
    cards.forEach(c => c.unmount())
    alive.clear()
    check('all unmounted')
})
