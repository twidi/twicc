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
//   * A change of the project-id SET or of the QUERY (the filters, `filters.js` `toQuery`) discards list,
//     cursors and queue; a response of an older set or query is ignored. Every request carries the query.
//   * HIDDEN SESSIONS: the server leaves out the rows of a session hidden at read time. An `after_id` response
//     names the sessions hidden (`hidden_sessions`) or shown again (`shown_sessions`) inside its range: the rows
//     of a hidden one leave the list in place (cursors and `has_more` untouched); a shown one is BACKFILLED, its
//     rows fetched (the query plus `session=<id>`) from the newest down to the pagination cursor and merged.
//     Nothing is reloaded; a failed backfill is queued again after the next successful live fetch. The edge
//     of the loaded window is the pagination cursor, not the tail, which a removal may have made newer.

import { compareNewestFirst, cursorOf, parseOccurredAt } from './eventTime.js'
import { queryKey } from './filters.js'

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
        status: 'idle', events: [], sessions: {}, turnsBelow: {}, hasMore: false, liveCursor: null, pageCursor: null,
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
 * @param {Object<string, string>} [params.query] - The filter parameters (`toQuery`), appended after `project_ids`.
 * @returns {string}
 */
export function buildTimelineUrl({ projectIds = [], before = null, afterId = null, limit = null, query = {} } = {}) {
    const params = new URLSearchParams()
    if (projectIds.length) params.set('project_ids', projectIds.join(','))
    for (const [key, value] of Object.entries(query)) params.set(key, value)
    if (limit !== null) params.set('limit', String(limit))
    if (before) params.set('before', before)
    if (afterId !== null) params.set('after_id', String(afterId))
    const text = params.toString()
    return `/api/timeline/${text ? `?${text}` : ''}`
}

/**
 * The position a `before` cursor names, as an event-like `{occurred_at, id}` (`compareNewestFirst` accepts it).
 *
 * @param {string|null} cursor - `<occurred_at>,<id>`.
 * @returns {{occurred_at: string, id: number}|null}
 */
export function positionOfCursor(cursor) {
    if (!cursor) return null
    const comma = cursor.lastIndexOf(',')
    return { occurred_at: cursor.slice(0, comma), id: Number(cursor.slice(comma + 1)) }
}

/**
 * Insert fetched rows into the list by (occurred_at, id) descending.
 *
 * @param {Array<Object>} list - Newest first.
 * @param {Array<Object>} incoming - In any order.
 * @param {{hasMore: boolean, live: boolean, floor: ({occurred_at: string, id: number}|null|undefined)}} options -
 *   `live`: rows of an `after_id` fetch. `floor`: the edge of the loaded window (the pagination cursor), when the
 *   tail may be newer than it (rows of a hidden session left the list); defaults to the tail.
 * @returns {Array<Object>} `list` itself when nothing was added, else a new array.
 */
export function mergeEvents(list, incoming, { hasMore, live, floor }) {
    const known = new Set(list.map((event) => event.id))
    const tail = floor ?? (list.length ? list[list.length - 1] : null)
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

/** The key of a scope and its query: equal for the same project-id set and an equal query. */
function feedKeyOf(projectIds, query) {
    return projectIds === null ? null : `${scopeKeyOf(projectIds)}\u0001${queryKey(query)}`
}

/**
 * @param {Object} options
 * @param {(params: {projectIds: string[], query: Object<string, string>, before: (string|null), afterId: (number|null)}) => Promise<Object>} options.fetchPage
 *   Resolves with the response body of `GET /api/timeline/`; rejects on any failure.
 * @param {FeedState} [options.state]
 * @param {(error: *) => void} [options.onLiveError] - A failed `after_id` fetch is silent: the next signal retries.
 * @param {(page: {phase: 'first'|'before'|'live'|'backfill', body: Object}) => void} [options.onPage]
 *   Receives accepted endpoint bodies. Live pages notify after session metadata merges, before display filtering.
 */
export function createTimelineFeed({ fetchPage, state = createFeedState(), onLiveError = () => {}, onPage = () => {} }) {
    let generation = 0
    let chain = Promise.resolve()
    let disposed = false
    let active = true
    let pausedTasks = []
    let scope = null // string[] (empty = global) or null (nothing to load)
    let query = {} // the filter parameters of the current generation
    let scopeKey
    let loadPending = false // a first page or a `before` page is queued or running
    let liveQueued = false
    let liveInfo = { latest: null, force: false }
    // Sessions whose backfill failed: queued again after the next successful live fetch (their `hidden.ended` row is
    // behind the live cursor, so no response would name them again). Cleared with the list.
    let failedBackfills = new Set()

    const stale = (gen) => disposed || gen !== generation

    function enqueue(task, allowInactive = false) {
        const gen = generation
        chain = chain
            .then(async () => {
                if (stale(gen)) return
                // Drain older deferred work before this task, including on a rapid resume
                // while other callbacks are still queued on the promise chain.
                while (active && pausedTasks.length && !stale(gen)) {
                    const pending = pausedTasks.shift()
                    if (!stale(pending.gen)) await pending.task(pending.gen)
                }
                if (stale(gen)) return
                if (!active && !allowInactive) {
                    pausedTasks.push({ gen, task })
                    return
                }
                return task(gen)
            })
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
        failedBackfills = new Set()
        pausedTasks = []
    }

    /**
     * `state.turnsBelow`: for each session of a page, when the turn its oldest rows belong to started below that
     * page (ms), else null. Each page answers for the sessions it holds, so a later (older) page replaces the
     * answer of a session an earlier page held: the oldest page that has rows of a session knows its turn best.
     * A live response says nothing about the bottom of the list and never calls this.
     */
    function mergeTurnsBelow(previous, body) {
        const next = { ...previous }
        const below = body.turns_below ?? {}
        for (const event of body.events) {
            const sessionId = event.session_id
            if (sessionId) next[sessionId] = Object.hasOwn(below, sessionId) ? parseOccurredAt(below[sessionId]) : null
        }
        return next
    }

    function mergeSessions(sessions) {
        if (sessions && Object.keys(sessions).length) state.sessions = { ...state.sessions, ...sessions }
    }

    async function runFirst(gen) {
        state.status = 'loading'
        state.loadError = null
        let body
        try {
            body = await fetchPage({ projectIds: [...scope], query: { ...query }, before: null, afterId: null })
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
        state.turnsBelow = mergeTurnsBelow({}, body)
        state.hasMore = body.has_more === true && state.events.length > 0
        state.pageCursor = state.events.length ? cursorOf(state.events[state.events.length - 1]) : null
        state.liveCursor = body.latest_id ?? null
        state.status = 'ready'
        loadPending = false
        onPage({ phase: 'first', body })
        if (stale(gen)) return
        // A signal that arrived while the page loaded was not consumed (see runLive): replay it, unless a
        // queued live task already will.
        const deferred = liveInfo.force || (liveInfo.latest !== null && liveInfo.latest > (state.liveCursor ?? 0))
        if (deferred && !liveQueued) {
            liveQueued = true
            enqueue(runLive)
        }
    }

    async function runBefore(gen) {
        // A live fetch that came back truncated reset the list while this page was queued behind it: there is no
        // cursor left, and the first page that the reset queued comes next. Sending `before: null` would only
        // fetch the first page twice.
        if (state.pageCursor === null) {
            loadPending = false
            state.loadingMore = false
            return
        }
        state.loadingMore = true
        state.loadError = null
        let body
        try {
            body = await fetchPage({ projectIds: [...scope], query: { ...query }, before: state.pageCursor, afterId: null })
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
        state.turnsBelow = mergeTurnsBelow(state.turnsBelow, body)
        // The cursor never moves backwards through live rows: it is the last row of THIS page.
        if (body.events.length) state.pageCursor = cursorOf(body.events[body.events.length - 1])
        // An empty page ends the list, whatever `has_more` says: no endless loop on a misbehaving server.
        state.hasMore = body.has_more === true && body.events.length > 0
        state.loadingMore = false
        loadPending = false
        onPage({ phase: 'before', body })
    }

    async function runLive(gen) {
        // No list to patch: the first page is loading or failed (or a truncated reset is pending). Keep the
        // signal for runFirst to replay; consuming it here would fetch `after_id` against an empty list.
        if (state.status !== 'ready') {
            liveQueued = false
            return
        }
        const info = liveInfo
        liveInfo = { latest: null, force: false }
        liveQueued = false
        const cursor = state.liveCursor ?? 0
        if (!info.force && !(info.latest !== null && info.latest > cursor)) return
        let body
        try {
            body = await fetchPage({ projectIds: [...scope], query: { ...query }, before: null, afterId: cursor })
        } catch (error) {
            if (!stale(gen)) onLiveError(error)
            return
        }
        if (stale(gen)) return
        mergeSessions(body.sessions)
        onPage({ phase: 'live', body })
        if (stale(gen)) return
        if (body.truncated === true) {
            // Too much happened to patch the list: start again from the first page.
            reset()
            state.status = 'loading'
            loadPending = true
            enqueue(runFirst)
            return
        }
        // The server also uses these visibility lists for muted sessions when they are excluded.
        removeSessions(body.hidden_sessions)
        // The edge of the window is the pagination cursor, not the tail: a removal may have taken the tail away.
        const floor = positionOfCursor(state.pageCursor)
        state.events = mergeEvents(state.events, body.events, { hasMore: state.hasMore, live: true, floor })
        if (body.latest_id !== null && body.latest_id !== undefined && body.latest_id > cursor) state.liveCursor = body.latest_id
        const backfills = new Set(failedBackfills)
        failedBackfills = new Set()
        for (const sessionId of body.shown_sessions ?? []) {
            // Under a filter on another session, a shown session has nothing to bring.
            if (!query.session || query.session === sessionId) backfills.add(sessionId)
        }
        for (const sessionId of backfills) queueBackfill(sessionId, null)
        // The removal emptied the list while older pages remain: nothing on screen would ask for the next one.
        if (!state.events.length && state.hasMore) loadMore()
    }

    /** Drop, in place, every loaded row of the sessions hidden since (the `hidden_sessions` of an `after_id` fetch). */
    function removeSessions(sessionIds) {
        if (!sessionIds?.length) return
        const hidden = new Set(sessionIds)
        if (!state.events.some((event) => hidden.has(event.session_id))) return
        state.events = state.events.filter((event) => !hidden.has(event.session_id))
    }

    function queueBackfill(sessionId, before) {
        enqueue((gen) => runBackfill(gen, sessionId, before))
    }

    /**
     * One page of the rows of a session shown again (`shown_sessions`), newest first, kept inside the loaded window:
     * with older pages still to load, a row older than the pagination cursor is dropped (the page that reaches it
     * brings it). Pages until one reaches that edge, or until the end of the session when every page is loaded.
     *
     * Like every task, a scope or query change drops it (the generation). A truncated live fetch resets the list and
     * queues the first page BEHIND the backfills already queued: they find no list (`status` is not `ready`) and
     * stop, and the first page reads every row of the session anyway.
     */
    async function runBackfill(gen, sessionId, before) {
        if (state.status !== 'ready') return
        let body
        try {
            body = await fetchPage({ projectIds: [...scope], query: { ...query, session: sessionId }, before, afterId: null })
        } catch (error) {
            // Silent, like a failed live fetch. Whatever page failed, the next live fetch starts it again from the newest
            // row: the rows already merged are skipped by id.
            if (stale(gen)) return
            failedBackfills.add(sessionId)
            onLiveError(error)
            return
        }
        if (stale(gen)) return
        const floor = state.hasMore ? positionOfCursor(state.pageCursor) : null
        const rows = floor ? body.events.filter((event) => compareNewestFirst(event, floor) <= 0) : body.events
        state.events = mergeEvents(state.events, rows, { hasMore: false, live: false })
        mergeSessions(body.sessions)
        onPage({ phase: 'backfill', body })
        if (stale(gen)) return
        // The oldest row of the page, in one pass.
        const last = body.events.reduce((oldest, event) => (!oldest || compareNewestFirst(event, oldest) > 0 ? event : oldest), null)
        if (body.has_more !== true || !last) return
        if (floor && compareNewestFirst(last, floor) >= 0) return // this page reached the edge of the window
        queueBackfill(sessionId, cursorOf(last))
    }

    function requestLive({ latest = null, force = false }) {
        if (disposed || scope === null) return
        if (latest !== null && (liveInfo.latest === null || latest > liveInfo.latest)) liveInfo.latest = latest
        liveInfo.force = liveInfo.force || force
        if (liveQueued) return
        liveQueued = true
        enqueue(runLive)
    }

    function setScope(projectIds, nextQuery, { afterId = null } = {}) {
        if (disposed) return
        const filterQuery = nextQuery ?? {} // null or undefined: no filter
        const key = feedKeyOf(projectIds, filterQuery)
        if (key === scopeKey && afterId === null) return
        scopeKey = key
        scope = projectIds === null ? null : [...projectIds]
        query = { ...filterQuery }
        generation += 1
        chain = Promise.resolve()
        reset()
        if (scope === null) return
        if (afterId !== null) {
            state.status = 'ready'
            state.liveCursor = afterId
            requestLive({ force: true })
            return
        }
        state.status = 'loading'
        loadPending = true
        // A hidden scope may load its initial page, but never its follow-up work.
        enqueue(runFirst, true)
    }

    function setActive(value) {
        if (disposed || active === value) return
        active = value
        // The queued callback drains paused work without discarding rows or cursors.
        if (active) enqueue(() => {})
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
        pausedTasks = []
    }

    return {
        state,
        /**
         * Point the feed at a project-id set and a filter query. `[]` is the global scope, `null` means "nothing
         * to load yet or at all" (projects not loaded, empty workspace, unknown project): no request is sent,
         * whatever the query. The SET is compared, not the order, and the query by `queryKey`, not its key
         * order: an unchanged set with an equal query keeps everything. Any other change reloads the first page.
         * A non-null `afterId` seed resets the feed and fetches incrementally without loading history.
         *
         * @param {string[]|null} projectIds
         * @param {Object<string, string>|null} [query] - The `toQuery` parameters every request of this scope carries;
         *   null or omitted means no filter.
         * @param {{afterId?: number|null}} [options] - An optional live cursor seed.
         */
        setScope,
        /** Pause queued work while hidden. An in-flight response may finish; its follow-ups wait for resume. */
        setActive,
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
