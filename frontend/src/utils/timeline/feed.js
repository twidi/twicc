// frontend/src/utils/timeline/feed.js
//
// The data side of the Timeline page: the list of events, the cursors and the single fetch queue
// (display design section 9.2). Pure: the network call is injected (`fetchPage`) and the state object
// is owned by the caller (a `reactive()` object in the app, a plain one in the tests).
//
// The rules this module keeps, each pinned by feed.test.js:
//   * ONE queue serializes every list fetch (first page, `before` pages, `after_id` fetches). A live
//     fetch therefore applies its insertion rules against the tail and `has_more` that the pending load
//     left behind. Signals that arrive meanwhile coalesce into one follow-up fetch.
//   * The LIVE CURSOR (highest id surely received from the head) advances only on a first-page or an
//     `after_id` response, to its `latest_id`, never on a `before` response and never on a truncated one.
//     A null cursor counts as 0.
//   * The PAGINATION CURSOR is the last row of the last fetched page, never the tail after live inserts.
//   * Fetched rows are INSERTED by (occurred_at, id) descending. A live row older than the loaded tail is
//     not inserted while `has_more` is true: the page that reaches it brings it.
//   * A change of the project-id SET discards list, cursors and queue; a response of an older set is ignored.

import { compareNewestFirst, cursorOf } from './eventTime.js'

/**
 * @typedef {Object} FeedState
 * @property {'idle'|'loading'|'ready'|'error'} status - State of the first page. `idle`: no scope to load.
 * @property {Array<Object>} events - Newest first.
 * @property {Object<string, Object>} sessions - The side table; a later response overwrites an entry.
 * @property {boolean} hasMore - More events exist older than the pagination cursor.
 * @property {number|null} liveCursor - Highest id surely received from the head; null counts as 0.
 * @property {string|null} pageCursor - `<occurred_at>,<id>` of the last row of the last fetched page.
 * @property {boolean} loadingMore - A `before` page is queued or running.
 * @property {{phase: 'first'|'before', error: *}|null} loadError - The failure the Retry action replays.
 */

/** @returns {FeedState} */
export function createFeedState() {
    return {
        status: 'idle', events: [], sessions: {}, hasMore: false, liveCursor: null, pageCursor: null,
        loadingMore: false, loadError: null,
    }
}

/**
 * URL of one request. `URLSearchParams` percent-encodes the cursor, so a `+` offset or a `,` survives.
 *
 * @param {Object} params
 * @param {string[]} [params.projectIds] - Empty means the global scope.
 * @param {string|null} [params.before] - `<occurred_at>,<id>`.
 * @param {number|null} [params.afterId]
 * @param {number|null} [params.limit]
 * @param {string|null} [params.provider]
 * @returns {string}
 */
export function buildTimelineUrl({ projectIds = [], before = null, afterId = null, limit = null, provider = null } = {}) {
    const query = new URLSearchParams()
    if (projectIds.length) query.set('project_ids', projectIds.join(','))
    if (provider) query.set('provider', provider)
    if (limit !== null) query.set('limit', String(limit))
    if (before) query.set('before', before)
    if (afterId !== null) query.set('after_id', String(afterId))
    const text = query.toString()
    return `/api/timeline/${text ? `?${text}` : ''}`
}

/**
 * Insert fetched rows into the list by (occurred_at, id) descending.
 *
 * @param {Array<Object>} list - Newest first.
 * @param {Array<Object>} incoming - In any order.
 * @param {{hasMore: boolean, live: boolean}} options - `live`: rows of an `after_id` fetch.
 * @returns {Array<Object>} `list` itself when nothing was added, else a new array.
 */
export function mergeEvents(list, incoming, { hasMore, live }) {
    const known = new Set(list.map((event) => event.id))
    const tail = list.length ? list[list.length - 1] : null
    const accepted = []
    for (const event of incoming) {
        if (known.has(event.id)) continue
        known.add(event.id)
        // Older than what is loaded, with older pages still to come: that page will bring it.
        if (live && hasMore && tail && compareNewestFirst(event, tail) > 0) continue
        accepted.push(event)
    }
    if (!accepted.length) return list
    return [...list, ...accepted].sort(compareNewestFirst)
}

function scopeKeyOf(projectIds) {
    return projectIds === null ? null : [...projectIds].sort().join('\u0000')
}

/**
 * @param {Object} options
 * @param {(params: {projectIds: string[], before: (string|null), afterId: (number|null)}) => Promise<Object>} options.fetchPage
 *   Resolves with the response body of `GET /api/timeline/`; rejects on any failure.
 * @param {FeedState} [options.state]
 * @param {(error: *) => void} [options.onLiveError] - A failed `after_id` fetch is silent: the next signal retries.
 */
export function createTimelineFeed({ fetchPage, state = createFeedState(), onLiveError = () => {} }) {
    let generation = 0
    let chain = Promise.resolve()
    let disposed = false
    let scope = null // string[] (empty = global) or null (nothing to load)
    let scopeKey
    let loadPending = false // a first page or a `before` page is queued or running
    let liveQueued = false
    let liveInfo = { latest: null, force: false }

    const stale = (gen) => disposed || gen !== generation

    function enqueue(task) {
        const gen = generation
        chain = chain
            .then(() => (stale(gen) ? undefined : task(gen)))
            .catch((error) => {
                // A task handles its own failures; this is only a guard so one bug cannot freeze the queue.
                console.error('Timeline feed task failed', error)
            })
    }

    function reset() {
        Object.assign(state, createFeedState())
        loadPending = false
        liveQueued = false
        liveInfo = { latest: null, force: false }
    }

    function mergeSessions(sessions) {
        if (sessions && Object.keys(sessions).length) state.sessions = { ...state.sessions, ...sessions }
    }

    async function runFirst(gen) {
        state.status = 'loading'
        state.loadError = null
        let body
        try {
            body = await fetchPage({ projectIds: scope, before: null, afterId: null })
        } catch (error) {
            if (stale(gen)) return
            state.status = 'error'
            state.loadError = { phase: 'first', error }
            loadPending = false
            return
        }
        if (stale(gen)) return
        state.events = [...body.events].sort(compareNewestFirst)
        state.sessions = { ...state.sessions, ...body.sessions }
        state.hasMore = body.has_more === true
        state.pageCursor = state.events.length ? cursorOf(state.events[state.events.length - 1]) : null
        state.liveCursor = body.latest_id ?? null
        state.status = 'ready'
        loadPending = false
    }

    async function runBefore(gen) {
        // A live fetch that came back truncated reset the list while this page was queued behind it: there is no
        // cursor left, and the first page that the reset queued comes next. Sending `before: null` would only
        // fetch the first page twice.
        if (state.pageCursor === null) return
        state.loadingMore = true
        state.loadError = null
        let body
        try {
            body = await fetchPage({ projectIds: scope, before: state.pageCursor, afterId: null })
        } catch (error) {
            if (stale(gen)) return
            state.loadingMore = false
            state.loadError = { phase: 'before', error }
            loadPending = false
            return
        }
        if (stale(gen)) return
        state.events = mergeEvents(state.events, body.events, { hasMore: false, live: false })
        mergeSessions(body.sessions)
        // The cursor never moves backwards through live rows: it is the last row of THIS page.
        if (body.events.length) state.pageCursor = cursorOf(body.events[body.events.length - 1])
        // An empty page ends the list, whatever `has_more` says: no endless loop on a misbehaving server.
        state.hasMore = body.has_more === true && body.events.length > 0
        state.loadingMore = false
        loadPending = false
    }

    async function runLive(gen) {
        const info = liveInfo
        liveInfo = { latest: null, force: false }
        liveQueued = false
        const cursor = state.liveCursor ?? 0
        if (!info.force && !(info.latest !== null && info.latest > cursor)) return
        let body
        try {
            body = await fetchPage({ projectIds: scope, before: null, afterId: cursor })
        } catch (error) {
            if (!stale(gen)) onLiveError(error)
            return
        }
        if (stale(gen)) return
        if (body.truncated === true) {
            // Too much happened to patch the list: start again from the first page.
            reset()
            state.status = 'loading'
            loadPending = true
            enqueue(runFirst)
            return
        }
        state.events = mergeEvents(state.events, body.events, { hasMore: state.hasMore, live: true })
        mergeSessions(body.sessions)
        if (body.latest_id !== null && body.latest_id !== undefined && body.latest_id > cursor) state.liveCursor = body.latest_id
    }

    function requestLive({ latest = null, force = false }) {
        if (disposed || scope === null) return
        if (latest !== null && (liveInfo.latest === null || latest > liveInfo.latest)) liveInfo.latest = latest
        liveInfo.force = liveInfo.force || force
        if (liveQueued) return
        liveQueued = true
        enqueue(runLive)
    }

    function setScope(projectIds) {
        if (disposed) return
        const key = scopeKeyOf(projectIds)
        if (key === scopeKey) return
        scopeKey = key
        scope = projectIds === null ? null : [...projectIds]
        generation += 1
        chain = Promise.resolve()
        reset()
        if (scope === null) return
        state.status = 'loading'
        loadPending = true
        enqueue(runFirst)
    }

    function loadMore() {
        if (disposed || scope === null) return
        if (state.status !== 'ready' || !state.hasMore || state.loadError || loadPending) return
        loadPending = true
        state.loadingMore = true
        enqueue(runBefore)
    }

    function retry() {
        if (disposed || scope === null || !state.loadError) return
        const { phase } = state.loadError
        state.loadError = null
        if (phase === 'first') {
            state.status = 'loading'
            loadPending = true
            enqueue(runFirst)
        } else {
            loadMore()
        }
    }

    function notifyLatest(latestId) {
        if (disposed || scope === null || state.status === 'idle' || state.status === 'error') return
        requestLive({ latest: latestId })
    }

    function refresh() {
        if (disposed || scope === null) return
        if (state.status === 'error') retry()
        else requestLive({ force: true })
    }

    async function idle() {
        let last
        do {
            last = chain
            await last
        } while (last !== chain)
    }

    function dispose() {
        disposed = true
        generation += 1
    }

    return {
        state,
        /**
         * Point the feed at a project-id set. `[]` is the global scope, `null` means "nothing to load yet or at
         * all" (projects not loaded, empty workspace, unknown project): no request is sent. The SET is compared,
         * not the order: an unchanged set keeps everything.
         *
         * @param {string[]|null} projectIds
         */
        setScope,
        /** Ask for the next page. A no-op while a load is pending, after a failure (use `retry`) or at the end. */
        loadMore,
        /** Replay the failed load (the Retry action). */
        retry,
        /**
         * A `timeline_changed` signal. When the queued live task runs it does nothing if the id is not above the
         * live cursor (null counts as 0); a signal that arrives while the first page loads is replayed after it.
         *
         * @param {number} latestId
         */
        notifyLatest,
        /**
         * The socket reached OPEN after the page loaded, reconnected, or the tab became active again: catch up
         * from the live cursor, or reload the first page when there is no list.
         */
        refresh,
        /** Resolves when the queue is empty (tests). */
        idle,
        dispose,
    }
}
