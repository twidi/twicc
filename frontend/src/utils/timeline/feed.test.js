import assert from 'node:assert/strict'
import test from 'node:test'

import { compareNewestFirst } from './eventTime.js'
import { buildTimelineUrl, createFeedState, createTimelineFeed, mergeEvents } from './feed.js'

const PAGE = 2

/** Let the queue start the task it holds: a request reads the server when it STARTS. */
const started = () => new Promise((resolve) => setImmediate(resolve))

/** Time `n` seconds after the origin, in the wire format. */
const at = (n, micro = 0) => `2026-10-14T10:${String(Math.floor(n / 60)).padStart(2, '0')}:${String(n % 60).padStart(2, '0')}.${String(micro).padStart(6, '0')}Z`
let counter = 0
const event = (id, time, fields = {}) => ({ id, occurred_at: time, kind: 'turn.started', session_id: 's1', project_id: 'p1', ...fields })

/**
 * A server in memory with the semantics of GET /api/timeline/. The response is computed when the request
 * STARTS (the SQL read) and delivered when the gate opens, so a test can commit a row in between.
 */
function createServer(initial = [], { pageSize = PAGE } = {}) {
    const table = [...initial]
    const calls = []
    const held = []
    let holding = false
    // Instance events (no session, no project) are in every scope, like the endpoint. Of the filters, only the
    // session one is modelled: it is enough to tell the answers of two queries apart.
    const inScope = (e, projectIds) => !projectIds.length || e.project_id === null || projectIds.includes(e.project_id)
    const matches = (e, query = {}) => !query.session || e.session_id === query.session
    function compute({ projectIds, query, before, afterId }) {
        const latest_id = table.length ? Math.max(...table.map((e) => e.id)) : null
        const sessions = { s1: { title: `T${++counter}`, project_id: 'p1', provider: 'codex', hidden: false, archived: false } }
        if (afterId !== null) {
            const rows = table.filter((e) => e.id > afterId && inScope(e, projectIds) && matches(e, query)).sort((a, b) => a.id - b.id)
            return { events: rows.slice(0, 500), has_more: false, truncated: rows.length > 500, latest_id, sessions }
        }
        let rows = table.filter((e) => inScope(e, projectIds) && matches(e, query)).sort(compareNewestFirst)
        if (before) {
            const [time, id] = before.split(',')
            rows = rows.filter((e) => compareNewestFirst(e, { occurred_at: time, id: Number(id) }) > 0)
        }
        const page = rows.slice(0, pageSize + 1)
        return { events: page.slice(0, pageSize), has_more: page.length > pageSize, truncated: false, latest_id, sessions }
    }
    return {
        table,
        calls,
        commit(e) {
            table.push(e)
        },
        hold() {
            holding = true
        },
        /** Deliver every held response (in order), then stop holding. */
        release() {
            holding = false
            for (const resolve of held.splice(0)) resolve()
        },
        async fetchPage(params) {
            calls.push({ ...params })
            const body = compute(params)
            if (holding) await new Promise((resolve) => held.push(resolve))
            return JSON.parse(JSON.stringify(body))
        },
        failNext(error = new Error('boom')) {
            const original = this.fetchPage
            this.fetchPage = async (params) => {
                this.fetchPage = original
                calls.push({ ...params })
                throw error
            }
        },
    }
}

function setup(initial = [], options) {
    const server = createServer(initial, options)
    const liveErrors = []
    const feed = createTimelineFeed({
        fetchPage: (params) => server.fetchPage(params),
        state: createFeedState(),
        onLiveError: (error) => liveErrors.push(error),
    })
    return { server, feed, state: feed.state, liveErrors }
}

const ids = (state) => state.events.map((e) => e.id)
const kinds = (server) => server.calls.map((c) => (c.afterId !== null ? `after:${c.afterId}` : c.before ? 'before' : 'first'))

// Ids 1..5, increasing in time: the newest is 5.
const five = () => [1, 2, 3, 4, 5].map((id) => event(id, at(id)))

test('the first page fills the list newest first with the cursors and the side table', async () => {
    const { server, feed, state } = setup(five())
    feed.setScope([])
    assert.equal(state.status, 'loading')
    await feed.idle()
    assert.deepEqual(ids(state), [5, 4])
    assert.equal(state.status, 'ready')
    assert.equal(state.hasMore, true)
    assert.equal(state.liveCursor, 5)
    assert.equal(state.pageCursor, `${at(4)},4`)
    assert.ok(state.sessions.s1.title.startsWith('T'))
    assert.deepEqual(kinds(server), ['first'])
})

test('a scope that cannot be loaded yet sends nothing and stays idle', async () => {
    const { server, feed, state } = setup(five())
    feed.setScope(null)
    await feed.idle()
    assert.deepEqual([state.status, server.calls.length], ['idle', 0])
    feed.notifyLatest(9)
    feed.refresh()
    feed.loadMore()
    await feed.idle()
    assert.equal(server.calls.length, 0)
})

test('loadMore pages with the last fetched row as cursor and stops at the end', async () => {
    const { server, feed, state } = setup(five())
    feed.setScope([])
    await feed.idle()
    feed.loadMore()
    await feed.idle()
    assert.deepEqual(ids(state), [5, 4, 3, 2])
    assert.equal(server.calls[1].before, `${at(4)},4`)
    feed.loadMore()
    await feed.idle()
    assert.deepEqual(ids(state), [5, 4, 3, 2, 1])
    assert.equal(state.hasMore, false)
    feed.loadMore()
    await feed.idle()
    assert.equal(server.calls.length, 3, 'no request at the end of the list')
})

test('loading is not re-entrant: repeated loadMore calls make one request', async () => {
    const { server, feed, state } = setup(five())
    feed.setScope([])
    await feed.idle()
    server.hold()
    feed.loadMore()
    feed.loadMore()
    feed.loadMore()
    assert.equal(state.loadingMore, true)
    server.release()
    await feed.idle()
    assert.equal(server.calls.filter((c) => c.before).length, 1)
    assert.equal(state.loadingMore, false)
})

test('loadMore while the first page loads does nothing', async () => {
    const { server, feed } = setup(five())
    server.hold()
    feed.setScope([])
    feed.loadMore()
    server.release()
    await feed.idle()
    assert.deepEqual(kinds(server), ['first'])
})

test('a before page never advances the live cursor', async () => {
    const { server, feed, state } = setup(five())
    feed.setScope([])
    await feed.idle()
    server.commit(event(9, at(9)))
    feed.loadMore()
    await feed.idle()
    assert.equal(server.calls[1].before !== null, true)
    assert.equal(state.liveCursor, 5, 'the before response carried latest_id 9, the cursor stays')
})

test('the live cursor takes the latest_id of an after_id response even when its only row is dropped', async () => {
    const { server, feed, state } = setup(five())
    feed.setScope([])
    await feed.idle()
    assert.deepEqual([state.hasMore, state.liveCursor], [true, 5])
    // Row 9 is back-dated past the loaded tail: with older pages to come it is not inserted (the page that
    // reaches it brings it), yet the cursor must move on, or every later signal would fetch it again.
    server.commit(event(9, at(0)))
    feed.notifyLatest(9)
    await feed.idle()
    assert.deepEqual(ids(state), [5, 4])
    assert.equal(state.liveCursor, 9)
    feed.notifyLatest(9)
    await feed.idle()
    assert.deepEqual(kinds(server), ['first', 'after:5'], 'nothing left to fetch')
    feed.loadMore()
    feed.loadMore()
    await feed.idle()
    feed.loadMore()
    await feed.idle()
    assert.ok(ids(state).includes(9), 'the page that reaches it brings it')
})

test('the has_more of an after_id response is ignored: the list keeps its own', async () => {
    const { server, feed, state } = setup(five())
    feed.setScope([])
    await feed.idle()
    assert.equal(state.hasMore, true)
    const original = server.fetchPage
    server.fetchPage = async (params) => {
        server.fetchPage = original
        server.calls.push({ ...params })
        return { events: [event(6, at(6))], has_more: false, truncated: false, latest_id: 6, sessions: {} }
    }
    feed.notifyLatest(6)
    await feed.idle()
    assert.equal(state.hasMore, true, 'a live response said false: the older pages are still there')
    feed.loadMore()
    await feed.idle()
    assert.deepEqual(ids(state), [6, 5, 4, 3, 2])
})

test('the rows of an after_id response arrive in ascending id order and are sorted into the list', async () => {
    const { server, feed, state } = setup(five())
    feed.setScope([])
    await feed.idle()
    server.commit(event(8, at(8)))
    server.commit(event(6, at(6)))
    server.commit(event(7, at(7)))
    feed.notifyLatest(8)
    await feed.idle()
    assert.deepEqual(ids(state), [8, 7, 6, 5, 4])
})

test('a next page queued behind a live fetch that comes back truncated is dropped: no wasted first-page request', async () => {
    const { server, feed, state } = setup(five())
    feed.setScope([])
    await feed.idle()
    const original = server.fetchPage
    let release
    server.fetchPage = async (params) => {
        server.fetchPage = original
        server.calls.push({ ...params })
        await new Promise((resolve) => { release = resolve })
        return { events: [event(99, at(50))], has_more: false, truncated: true, latest_id: 700, sessions: {} }
    }
    server.commit(event(6, at(6)))
    feed.notifyLatest(6)
    await started()
    feed.loadMore() // queued behind the live fetch
    release()
    await feed.idle()
    assert.deepEqual(kinds(server), ['first', 'after:5', 'first'], 'the before page of the dead list never went out')
    assert.deepEqual([state.status, state.loadingMore, state.loadError], ['ready', false, null])
    assert.deepEqual(ids(state), [6, 5])
})

test('the live cursor is the latest_id of the response, not the id of the newest event', async () => {
    // Row 9 is the newest row written, but back-dated: it sits at the far end of the feed.
    const { server, feed, state } = setup([...five(), event(9, at(0))])
    feed.setScope([])
    await feed.idle()
    assert.deepEqual(ids(state), [5, 4])
    assert.equal(state.liveCursor, 9)
    feed.notifyLatest(9)
    await feed.idle()
    assert.deepEqual(kinds(server), ['first'])
})

test('a signal with a known latest id does nothing', async () => {
    const { server, feed } = setup(five())
    feed.setScope([])
    await feed.idle()
    feed.notifyLatest(5)
    feed.notifyLatest(3)
    await feed.idle()
    assert.deepEqual(kinds(server), ['first'])
})

test('a new id triggers one after_id fetch from the live cursor and advances it', async () => {
    const { server, feed, state } = setup(five())
    feed.setScope([])
    await feed.idle()
    server.commit(event(6, at(6)))
    feed.notifyLatest(6)
    await feed.idle()
    assert.deepEqual(kinds(server), ['first', 'after:5'])
    assert.deepEqual(ids(state), [6, 5, 4])
    assert.equal(state.liveCursor, 6)
})

test('signals during a live fetch coalesce into exactly one follow-up fetch', async () => {
    const { server, feed, state } = setup(five())
    feed.setScope([])
    await feed.idle()
    server.commit(event(6, at(6)))
    server.hold()
    feed.notifyLatest(6)
    await started()
    server.commit(event(7, at(7)))
    feed.notifyLatest(7)
    server.commit(event(8, at(8)))
    feed.notifyLatest(8)
    feed.notifyLatest(8)
    server.release()
    await feed.idle()
    assert.deepEqual(kinds(server), ['first', 'after:5', 'after:6'])
    assert.deepEqual(ids(state), [8, 7, 6, 5, 4])
    assert.equal(state.liveCursor, 8)
})

test('fetched rows are inserted by (occurred_at, id), duplicates are ignored, a back-dated row lands in place', async () => {
    const { server, feed, state } = setup(five())
    feed.setScope([])
    await feed.idle()
    server.commit(event(6, at(2, 500)))  // back-dated: older than the loaded range [5, 4] ...
    server.commit(event(7, at(4, 500)))  // ... and this one falls between 5 and 4
    feed.notifyLatest(7)
    await feed.idle()
    // Row 6 is older than the tail while pages remain: left to the page that reaches it.
    assert.deepEqual(ids(state), [5, 7, 4])
    feed.loadMore()
    await feed.idle()
    assert.deepEqual(ids(state), [5, 7, 4, 3, 6], 'the page brings the back-dated row, in its place')
})

test('a live row older than the loaded tail is not inserted while has_more is true, and inserted once has_more is false', () => {
    const list = [event(5, at(5)), event(4, at(4))]
    const old = event(9, at(1))
    assert.deepEqual(mergeEvents(list, [old], { hasMore: true, live: true }), list)
    assert.deepEqual(mergeEvents(list, [old], { hasMore: false, live: true }).map((e) => e.id), [5, 4, 9])
    // The same row from a before page is always accepted.
    assert.deepEqual(mergeEvents(list, [old], { hasMore: true, live: false }).map((e) => e.id), [5, 4, 9])
})

test('mergeEvents ignores a duplicate id and keeps the same array when nothing is added', () => {
    const list = [event(5, at(5))]
    assert.equal(mergeEvents(list, [event(5, at(5))], { hasMore: false, live: true }), list)
    const merged = mergeEvents(list, [event(6, at(6)), event(6, at(6))], { hasMore: false, live: true })
    assert.deepEqual(merged.map((e) => e.id), [6, 5])
})

test('mergeEvents orders equal times by id and keeps microseconds', () => {
    const list = [event(2, at(1, 100))]
    const merged = mergeEvents(list, [event(3, at(1, 100)), event(1, at(1, 900))], { hasMore: false, live: true })
    assert.deepEqual(merged.map((e) => e.id), [1, 3, 2])
})

test('the pagination cursor stays the last fetched row after live insertions', async () => {
    const { server, feed, state } = setup(five())
    feed.setScope([])
    await feed.idle()
    const cursorBefore = state.pageCursor
    server.commit(event(6, at(6)))
    server.commit(event(7, at(7)))
    feed.notifyLatest(7)
    await feed.idle()
    assert.equal(state.pageCursor, cursorBefore)
    feed.loadMore()
    await feed.idle()
    assert.equal(server.calls.at(-1).before, cursorBefore)
    assert.deepEqual(ids(state), [7, 6, 5, 4, 3, 2])
})

test('truncated: the list and the cursors are discarded and the first page is reloaded', async () => {
    const { server, feed, state } = setup(five())
    feed.setScope([])
    await feed.idle()
    feed.loadMore()
    await feed.idle()
    const original = server.fetchPage
    server.fetchPage = async (params) => {
        server.fetchPage = original
        server.calls.push({ ...params })
        return { events: [event(99, at(50))], has_more: false, truncated: true, latest_id: 700, sessions: {} }
    }
    server.commit(event(6, at(6)))
    feed.notifyLatest(6)
    await feed.idle()
    assert.deepEqual(kinds(server), ['first', 'before', 'after:5', 'first'])
    assert.deepEqual(ids(state), [6, 5], 'the truncated rows are not used')
    assert.equal(state.liveCursor, 6, 'the live cursor never took the truncated latest_id')
    assert.equal(state.status, 'ready')
})

test('a signal during the first page is deferred and replayed after it', async () => {
    const { server, feed, state } = setup(five())
    server.hold()
    feed.setScope([])
    await started()
    server.commit(event(6, at(6)))
    feed.notifyLatest(6)
    server.release()
    await feed.idle()
    // The first page was read before row 6 existed (latest_id 5): the replay fetches it.
    assert.deepEqual(kinds(server), ['first', 'after:5'])
    assert.deepEqual(ids(state), [6, 5, 4])
})

test('a deferred signal that the first page already covers is not replayed', async () => {
    const { server, feed } = setup(five())
    server.hold()
    feed.setScope([])
    await started()
    feed.notifyLatest(5)
    server.release()
    await feed.idle()
    assert.deepEqual(kinds(server), ['first'])
})

test('a null live cursor on an empty feed counts as 0: the first event ever recorded is fetched', async () => {
    const { server, feed, state } = setup([])
    feed.setScope([])
    await feed.idle()
    assert.deepEqual([state.liveCursor, state.status, state.events.length], [null, 'ready', 0])
    server.commit(event(1, at(1)))
    feed.notifyLatest(1)
    await feed.idle()
    assert.deepEqual(kinds(server), ['first', 'after:0'])
    assert.deepEqual(ids(state), [1])
    assert.equal(state.liveCursor, 1)
})

test('the queue race: a row committed after a before query read but inside the new range is not lost', async () => {
    // Loaded [10, 9], pages remain. The before page below reads rows 8, 7. Row 20 is then committed with a
    // time between them (back-dated), which the before snapshot cannot contain.
    const rows = [event(10, at(10)), event(9, at(9)), event(8, at(8)), event(7, at(7)), event(6, at(6))]
    const { server, feed, state } = setup(rows)
    feed.setScope([])
    await feed.idle()
    assert.deepEqual(ids(state), [10, 9])
    server.hold()
    feed.loadMore()
    await started()
    server.commit(event(20, at(7, 500)))
    feed.notifyLatest(20)
    server.release()
    await feed.idle()
    assert.deepEqual(kinds(server), ['first', 'before', 'after:10'])
    assert.deepEqual(ids(state), [10, 9, 8, 20, 7], 'row 20 is inside the loaded range once the page landed')
})

test('a project-id set change reloads the first page; the same set in another order does not', async () => {
    const { server, feed, state } = setup([event(1, at(1), { project_id: 'a' }), event(2, at(2), { project_id: 'b' }), event(3, at(3), { project_id: 'c' })])
    feed.setScope(['a', 'b'])
    await feed.idle()
    assert.deepEqual(ids(state), [2, 1])
    feed.setScope(['b', 'a'])
    await feed.idle()
    assert.equal(server.calls.length, 1)
    feed.setScope(['a', 'b', 'c'])
    await feed.idle()
    assert.deepEqual(kinds(server), ['first', 'first'])
    assert.deepEqual(ids(state), [3, 2])
    assert.deepEqual(server.calls[1].projectIds, ['a', 'b', 'c'])
})

test('every after_id fetch uses the current set', async () => {
    const { server, feed } = setup([event(1, at(1), { project_id: 'a' }), event(2, at(2), { project_id: 'b' })])
    feed.setScope(['a'])
    await feed.idle()
    feed.setScope(['a', 'b'])
    await feed.idle()
    server.commit(event(3, at(3), { project_id: 'b' }))
    feed.notifyLatest(3)
    await feed.idle()
    assert.deepEqual(server.calls.at(-1), { projectIds: ['a', 'b'], query: {}, before: null, afterId: 2 })
})

test('a response for a set that is no longer current is ignored, and its queue is dropped', async () => {
    const { server, feed, state } = setup([event(1, at(1), { project_id: 'a' }), event(2, at(2), { project_id: 'b' })])
    server.hold()
    feed.setScope(['a'])
    await started()
    feed.notifyLatest(5)
    feed.setScope(['b'])
    server.release()
    await feed.idle()
    assert.deepEqual(ids(state), [2], 'only the data of the current set')
    assert.deepEqual(server.calls.map((c) => c.projectIds), [['a'], ['b']], 'the queued live fetch of the old set never ran')
})

test('going back to no scope clears the list', async () => {
    const { feed, state } = setup(five())
    feed.setScope([])
    await feed.idle()
    feed.setScope(null)
    assert.deepEqual([state.status, state.events.length, state.hasMore, state.liveCursor, state.pageCursor], ['idle', 0, false, null, null])
})

test('a failed first page shows the error and Retry replays it', async () => {
    const { server, feed, state } = setup(five())
    server.failNext()
    feed.setScope([])
    await feed.idle()
    assert.equal(state.status, 'error')
    assert.equal(state.loadError.phase, 'first')
    feed.retry()
    assert.equal(state.status, 'loading')
    assert.equal(state.loadError, null)
    await feed.idle()
    assert.deepEqual([state.status, ids(state)], ['ready', [5, 4]])
})

test('a failed before page stops the automatic loading until Retry', async () => {
    const { server, feed, state } = setup(five())
    feed.setScope([])
    await feed.idle()
    server.failNext()
    feed.loadMore()
    await feed.idle()
    assert.equal(state.loadError.phase, 'before')
    assert.equal(state.loadingMore, false)
    assert.equal(state.status, 'ready')
    feed.loadMore()
    await feed.idle()
    assert.equal(server.calls.filter((c) => c.before).length, 1, 'no automatic retry')
    feed.retry()
    await feed.idle()
    assert.deepEqual(ids(state), [5, 4, 3, 2])
    assert.equal(state.loadError, null)
})

test('refresh (first OPEN, reconnect, tab return) catches up from the live cursor and coalesces', async () => {
    const { server, feed, state } = setup(five())
    feed.setScope([])
    await feed.idle()
    server.commit(event(6, at(6)))
    feed.refresh()
    feed.refresh()
    feed.refresh()
    await feed.idle()
    assert.deepEqual(kinds(server), ['first', 'after:5'])
    assert.deepEqual(ids(state), [6, 5, 4])
})

test('refresh with nothing new still fetches once (the signal may have been missed)', async () => {
    const { server, feed } = setup(five())
    feed.setScope([])
    await feed.idle()
    feed.refresh()
    await feed.idle()
    assert.deepEqual(kinds(server), ['first', 'after:5'])
})

test('refresh with no list reloads the first page', async () => {
    const { server, feed, state } = setup(five())
    server.failNext()
    feed.setScope([])
    await feed.idle()
    feed.refresh()
    await feed.idle()
    assert.deepEqual(kinds(server), ['first', 'first'])
    assert.equal(state.status, 'ready')
})

test('a live fetch that fails is silent and the next signal retries it', async () => {
    const { server, feed, state, liveErrors } = setup(five())
    feed.setScope([])
    await feed.idle()
    server.commit(event(6, at(6)))
    server.failNext(new Error('offline'))
    feed.notifyLatest(6)
    await feed.idle()
    assert.equal(liveErrors.length, 1)
    assert.deepEqual([state.status, state.loadError, state.liveCursor], ['ready', null, 5])
    feed.notifyLatest(6)
    await feed.idle()
    assert.deepEqual(ids(state), [6, 5, 4])
})

test('a later response overwrites the side-table entry of a session', async () => {
    const { server, feed, state } = setup(five())
    feed.setScope([])
    await feed.idle()
    const first = state.sessions.s1.title
    server.commit(event(6, at(6)))
    feed.notifyLatest(6)
    await feed.idle()
    assert.notEqual(state.sessions.s1.title, first)
})

test('an empty page ends the list even if has_more says otherwise', async () => {
    const { server, feed, state } = setup(five())
    feed.setScope([])
    await feed.idle()
    const original = server.fetchPage
    server.fetchPage = async () => {
        server.fetchPage = original
        return { events: [], has_more: true, truncated: false, latest_id: 5, sessions: {} }
    }
    feed.loadMore()
    await feed.idle()
    assert.equal(state.hasMore, false)
})

test('a disposed feed ignores every call and keeps its state as it was', async () => {
    const { server, feed, state } = setup(five())
    feed.setScope([])
    await feed.idle()
    const before = { ...state }
    feed.dispose()
    feed.loadMore()
    feed.notifyLatest(9)
    feed.refresh()
    feed.retry()
    feed.setScope(['x'])
    await feed.idle()
    assert.equal(server.calls.length, 1, 'only the first page of before the dispose')
    assert.deepEqual({ ...state }, before, 'no flag, list or cursor moved (loadingMore included)')
})

test('a feed disposed with a request in flight drops its answer', async () => {
    const { server, feed, state } = setup(five())
    server.hold()
    feed.setScope([])
    await started()
    feed.dispose()
    server.release()
    await feed.idle()
    assert.deepEqual([state.status, state.events.length], ['loading', 0])
})

test('a signal while the first page failed does nothing: the Retry action reloads', async () => {
    const { server, feed, state } = setup(five())
    server.failNext()
    feed.setScope([])
    await feed.idle()
    assert.equal(state.status, 'error')
    feed.notifyLatest(9)
    await feed.idle()
    assert.deepEqual([server.calls.length, state.status], [1, 'error'])
    feed.retry()
    await feed.idle()
    assert.deepEqual([server.calls.length, state.status], [2, 'ready'])
})

test('in a scoped feed the live cursor follows latest_id, which is global, even when no row is in scope', async () => {
    const { server, feed, state } = setup([event(1, at(1), { project_id: 'a' }), event(2, at(2), { project_id: 'a' })])
    feed.setScope(['a'])
    await feed.idle()
    assert.equal(state.liveCursor, 2)
    server.commit(event(3, at(3), { project_id: 'b' }))
    feed.notifyLatest(3)
    await feed.idle()
    assert.deepEqual(kinds(server), ['first', 'after:2'])
    assert.deepEqual(ids(state), [2, 1], 'nothing in scope')
    assert.equal(state.liveCursor, 3)
    feed.notifyLatest(3)
    await feed.idle()
    assert.deepEqual(kinds(server), ['first', 'after:2'], 'the same signal fetches nothing more')
})

test('buildTimelineUrl encodes the cursor and keeps after_id 0', () => {
    assert.equal(buildTimelineUrl({}), '/api/timeline/')
    assert.equal(buildTimelineUrl({ projectIds: ['a', 'b'] }), '/api/timeline/?project_ids=a%2Cb')
    assert.equal(buildTimelineUrl({ afterId: 0 }), '/api/timeline/?after_id=0')
    const url = buildTimelineUrl({ projectIds: ['-p'], before: '2026-10-14T10:00:00.123456Z,42', limit: 50 })
    assert.equal(url, '/api/timeline/?project_ids=-p&limit=50&before=2026-10-14T10%3A00%3A00.123456Z%2C42')
    assert.equal(new URL(`http://x${url}`).searchParams.get('before'), '2026-10-14T10:00:00.123456Z,42')
    assert.equal(buildTimelineUrl({ before: 'a+b,1' }), '/api/timeline/?before=a%2Bb%2C1')
})

test('a signal during the first page that then fails sends nothing and leaves no list', async () => {
    const { server, feed, state } = setup(five())
    let fail
    server.fetchPage = async (params) => {
        server.calls.push({ ...params })
        await new Promise((resolve) => { fail = resolve })
        throw new Error('boom')
    }
    feed.setScope([])
    await started()
    feed.notifyLatest(9) // queued behind the first page
    fail()
    await feed.idle()
    assert.deepEqual(kinds(server), ['first'])
    assert.deepEqual([state.status, state.events.length, state.liveCursor, state.loadError.phase], ['error', 0, null, 'first'])
})

test('a signal right after a truncated reset, before the first page runs, sends no after_id against the empty list', async () => {
    const server = createServer(five())
    // The state fires a signal the moment the truncated reset puts the feed back to `loading`.
    let armed = false
    let status = 'idle'
    let feed
    const state = createFeedState()
    Object.defineProperty(state, 'status', {
        get: () => status,
        set: (value) => {
            status = value
            if (armed && value === 'loading') {
                armed = false
                feed.notifyLatest(7)
            }
        },
        enumerable: true,
    })
    feed = createTimelineFeed({ fetchPage: (params) => server.fetchPage(params), state })
    feed.setScope([])
    await feed.idle()
    const original = server.fetchPage
    server.fetchPage = async (params) => {
        server.fetchPage = original
        server.calls.push({ ...params })
        return { events: [], has_more: false, truncated: true, latest_id: 700, sessions: {} }
    }
    server.commit(event(6, at(6)))
    armed = true
    feed.notifyLatest(6)
    await feed.idle()
    // No `after:0`: the signal waits for the first page, then is replayed from the cursor it brought (6).
    assert.deepEqual(kinds(server), ['first', 'after:5', 'first', 'after:6'])
    assert.deepEqual(ids(state), [6, 5])
})

test('a signal deferred during a successful first page is replayed with one after_id', async () => {
    const { server, feed, state } = setup(five())
    server.hold()
    feed.setScope([])
    await started()
    server.commit(event(6, at(6)))
    feed.notifyLatest(6)
    feed.notifyLatest(6)
    server.release()
    await feed.idle()
    assert.deepEqual(kinds(server), ['first', 'after:5'])
    assert.deepEqual(ids(state), [6, 5, 4])
})

test('a scope change while a before request is in flight keeps the new scope data intact', async () => {
    const { server, feed, state } = setup([event(1, at(1), { project_id: 'a' }), event(2, at(2), { project_id: 'a' }), event(3, at(3), { project_id: 'a' }), event(4, at(4), { project_id: 'b' })])
    feed.setScope(['a'])
    await feed.idle()
    server.hold()
    feed.loadMore()
    await started()
    feed.setScope(['b'])
    server.release()
    await feed.idle()
    assert.deepEqual(ids(state), [4])
    assert.deepEqual([state.loadingMore, state.status, state.hasMore, state.pageCursor], [false, 'ready', false, `${at(4)},4`])
})

test('a before request that fails after a scope change leaves the new scope without error', async () => {
    const { server, feed, state } = setup(five())
    feed.setScope([])
    await feed.idle()
    const original = server.fetchPage
    let fail
    server.fetchPage = async (params) => {
        server.fetchPage = original
        server.calls.push({ ...params })
        await new Promise((resolve) => { fail = resolve })
        throw new Error('late')
    }
    feed.loadMore()
    await started()
    feed.setScope(['p1'])
    fail()
    await feed.idle()
    assert.deepEqual([state.loadError, state.loadingMore, state.status], [null, false, 'ready'])
})

test('a scope change while a live request is in flight keeps the new scope data intact', async () => {
    const { server, feed, state, liveErrors } = setup([event(1, at(1), { project_id: 'a' }), event(2, at(2), { project_id: 'b' })])
    feed.setScope(['a'])
    await feed.idle()
    server.hold()
    server.commit(event(3, at(3), { project_id: 'a' }))
    feed.notifyLatest(3)
    await started()
    feed.setScope(['b'])
    server.release()
    await feed.idle()
    assert.deepEqual(ids(state), [2])
    assert.equal(state.liveCursor, 3)
    assert.deepEqual(liveErrors, [])
})

test('a live request that fails after a scope change stays silent', async () => {
    const { server, feed, liveErrors } = setup(five())
    feed.setScope([])
    await feed.idle()
    const original = server.fetchPage
    let fail
    server.fetchPage = async (params) => {
        server.fetchPage = original
        server.calls.push({ ...params })
        await new Promise((resolve) => { fail = resolve })
        throw new Error('late')
    }
    server.commit(event(6, at(6)))
    feed.notifyLatest(6)
    await started()
    feed.setScope(['p1'])
    fail()
    await feed.idle()
    assert.deepEqual(liveErrors, [])
})

test('the first page is sorted newest first even when the rows arrive unsorted', async () => {
    const { server, feed, state } = setup(five())
    server.fetchPage = async () => ({ events: [event(1, at(1)), event(3, at(3)), event(2, at(2))], has_more: false, truncated: false, latest_id: 3, sessions: {} })
    feed.setScope([])
    await feed.idle()
    assert.deepEqual(ids(state), [3, 2, 1])
    assert.equal(state.pageCursor, `${at(1)},1`)
})

test('a live response with a smaller latest_id never moves the live cursor back', async () => {
    const { server, feed, state } = setup(five())
    feed.setScope([])
    await feed.idle()
    server.fetchPage = async () => ({ events: [event(6, at(6))], has_more: false, truncated: false, latest_id: 2, sessions: {} })
    feed.notifyLatest(6)
    await feed.idle()
    assert.equal(state.liveCursor, 5)
})

test('an empty first page with has_more true ends the list: loadMore leaves no flag set', async () => {
    const { server, feed, state } = setup(five())
    server.fetchPage = async () => ({ events: [], has_more: true, truncated: false, latest_id: 5, sessions: {} })
    feed.setScope([])
    await feed.idle()
    assert.equal(state.hasMore, false)
    feed.loadMore()
    await feed.idle()
    assert.deepEqual([state.loadingMore, server.calls.length], [false, 0])
})

test('a before request with no page cursor clears its flags', async () => {
    const { server, feed, state } = setup(five())
    feed.setScope([])
    await feed.idle()
    state.pageCursor = null // defensive path: forced, the first-page rule above keeps it unreachable
    feed.loadMore()
    await feed.idle()
    assert.deepEqual([state.loadingMore, server.calls.length], [false, 1])
    state.pageCursor = `${at(4)},4`
    feed.loadMore()
    await feed.idle()
    assert.deepEqual(ids(state), [5, 4, 3, 2])
})

test('the scope array passed to fetchPage is a copy: a caller mutating it changes nothing', async () => {
    const { server } = setup(five())
    const seen = []
    const original = server.fetchPage.bind(server)
    const feed2 = createTimelineFeed({ fetchPage: (params) => { seen.push(params.projectIds); return original(params) }, state: createFeedState() })
    const scope = ['p1']
    feed2.setScope(scope)
    await feed2.idle()
    seen[0].push('hacked')
    scope.push('other')
    feed2.refresh()
    await feed2.idle()
    assert.deepEqual(seen[1], ['p1'])
})

test('an instance event (no session, no project) is listed and inserted in a scoped feed', async () => {
    const { server, feed, state } = setup([event(1, at(1), { project_id: 'a' }), event(2, at(2), { project_id: null, session_id: null, kind: 'instance.started' })])
    feed.setScope(['a'])
    await feed.idle()
    assert.deepEqual(ids(state), [2, 1])
    server.commit(event(3, at(3), { project_id: null, session_id: null, kind: 'instance.started' }))
    feed.notifyLatest(3)
    await feed.idle()
    assert.deepEqual(ids(state), [3, 2, 1])
})

// Events of two sessions, alternating: s1 has the odd ids, s2 the even ones. Newest is 6.
const twoSessions = () => [1, 2, 3, 4, 5, 6].map((id) => event(id, at(id), { session_id: id % 2 ? 's1' : 's2' }))

test('the same set with an equal query in another key order sends nothing new', async () => {
    const { server, feed } = setup(five())
    feed.setScope(['p1'], { session: 's1', view: 'waits_problems' })
    await feed.idle()
    feed.setScope(['p1'], { view: 'waits_problems', session: 's1' })
    await feed.idle()
    assert.equal(server.calls.length, 1)
    assert.deepEqual(server.calls[0].query, { session: 's1', view: 'waits_problems' })
})

test('the default query is empty: setScope(ids) and setScope(ids, {}) are the same scope', async () => {
    const { server, feed } = setup(five())
    feed.setScope(['p1'])
    await feed.idle()
    feed.setScope(['p1'], {})
    await feed.idle()
    assert.deepEqual(server.calls, [{ projectIds: ['p1'], query: {}, before: null, afterId: null }])
})

test('a changed query reloads the first page with the new query', async () => {
    const { server, feed, state } = setup(twoSessions())
    feed.setScope([])
    await feed.idle()
    assert.deepEqual(ids(state), [6, 5])
    feed.setScope([], { session: 's2' })
    assert.deepEqual([state.status, state.events.length], ['loading', 0])
    await feed.idle()
    assert.deepEqual(kinds(server), ['first', 'first'])
    assert.deepEqual(server.calls[1], { projectIds: [], query: { session: 's2' }, before: null, afterId: null })
    assert.deepEqual(ids(state), [6, 4])
    feed.setScope([])
    await feed.idle()
    assert.deepEqual(kinds(server), ['first', 'first', 'first'], 'back to no filter reloads too')
    assert.deepEqual(ids(state), [6, 5])
})

test('a response of the previous query is dropped', async () => {
    const { server, feed, state } = setup(twoSessions())
    server.hold()
    feed.setScope([], { session: 's1' })
    await started()
    feed.setScope([], { session: 's2' })
    await started()
    // Both requests are held: A is delivered first, then B.
    server.release()
    await feed.idle()
    assert.deepEqual(server.calls.map((c) => c.query), [{ session: 's1' }, { session: 's2' }])
    assert.deepEqual(ids(state), [6, 4], 'only the events of query B')
    assert.ok(state.events.every((e) => e.session_id === 's2'))
    assert.equal(state.status, 'ready')
})

test('a before page in flight for the previous query is dropped', async () => {
    const { server, feed, state } = setup(twoSessions())
    feed.setScope([], { session: 's1' })
    await feed.idle()
    server.hold()
    feed.loadMore()
    await started()
    feed.setScope([], { session: 's2' })
    server.release()
    await feed.idle()
    assert.deepEqual(ids(state), [6, 4])
    assert.deepEqual([state.loadingMore, state.pageCursor], [false, `${at(4)},4`])
})

test('a live fetch in flight for the previous query is dropped', async () => {
    const { server, feed, state } = setup(twoSessions())
    feed.setScope([], { session: 's1' })
    await feed.idle()
    server.commit(event(7, at(7), { session_id: 's1' }))
    server.hold()
    feed.notifyLatest(7)
    await started()
    feed.setScope([], { session: 's2' })
    server.release()
    await feed.idle()
    assert.deepEqual(ids(state), [6, 4], 'row 7 of query A never lands')
    assert.equal(state.liveCursor, 7)
})

test('before and after_id requests carry the current query', async () => {
    const { server, feed, state } = setup(twoSessions())
    feed.setScope(['p1'], { session: 's2' })
    await feed.idle()
    feed.loadMore()
    await feed.idle()
    server.commit(event(7, at(7), { session_id: 's1' }))
    server.commit(event(8, at(8), { session_id: 's2' }))
    feed.notifyLatest(8)
    await feed.idle()
    assert.deepEqual(kinds(server), ['first', 'before', 'after:6'])
    assert.deepEqual(server.calls.map((c) => c.query), [{ session: 's2' }, { session: 's2' }, { session: 's2' }])
    assert.deepEqual(ids(state), [8, 6, 4, 2], 'the row of the other session is not shown')
    assert.equal(state.liveCursor, 8)
})

test('a truncated live fetch reloads with the same query', async () => {
    const { server, feed, state } = setup(twoSessions())
    feed.setScope(['p1'], { session: 's2' })
    await feed.idle()
    const original = server.fetchPage
    server.fetchPage = async (params) => {
        server.fetchPage = original
        server.calls.push({ ...params })
        return { events: [], has_more: false, truncated: true, latest_id: 700, sessions: {} }
    }
    server.commit(event(7, at(7), { session_id: 's2' }))
    feed.notifyLatest(7)
    await feed.idle()
    assert.deepEqual(kinds(server), ['first', 'after:6', 'first'])
    assert.deepEqual(server.calls.at(-1), { projectIds: ['p1'], query: { session: 's2' }, before: null, afterId: null })
    assert.deepEqual(ids(state), [7, 6])
    assert.equal(state.status, 'ready')
})

test('the query passed to fetchPage is a copy: a caller mutating it changes nothing', async () => {
    const { server, feed } = setup(twoSessions())
    const query = { session: 's2' }
    feed.setScope([], query)
    await feed.idle()
    server.calls[0].query.session = 'hacked'
    query.session = 'other'
    feed.refresh()
    await feed.idle()
    assert.deepEqual(server.calls[1].query, { session: 's2' })
})

test('a null or undefined query means no filter and does not throw', async () => {
    const { server, feed } = setup(five())
    feed.setScope(['p1'], null)
    await feed.idle()
    assert.deepEqual(server.calls, [{ projectIds: ['p1'], query: {}, before: null, afterId: null }])
    feed.setScope(['p1'], undefined)
    feed.setScope(['p1'], {})
    await feed.idle()
    assert.equal(server.calls.length, 1, 'null, undefined and {} are the same query')
    feed.setScope(['p1'], { session: 's1' })
    await feed.idle()
    feed.setScope(['p1'], null)
    await feed.idle()
    assert.deepEqual(server.calls.map((c) => c.query), [{}, { session: 's1' }, {}], 'back to null clears the filter')
})

test('a null scope sends nothing whatever the query', async () => {
    const { server, feed, state } = setup(five())
    feed.setScope(null, { session: 's1' })
    await feed.idle()
    assert.deepEqual([state.status, server.calls.length], ['idle', 0])
})

test('buildTimelineUrl appends the query entries after project_ids and percent-encodes them', () => {
    const url = buildTimelineUrl({
        projectIds: ['a', 'b'],
        query: { view: 'waits_problems', families: 'sessions,turns', session: 'x y&z' },
        limit: 50,
    })
    assert.equal(url, '/api/timeline/?project_ids=a%2Cb&view=waits_problems&families=sessions%2Cturns&session=x+y%26z&limit=50')
    const params = new URL(`http://x${url}`).searchParams
    assert.deepEqual([params.get('families'), params.get('session')], ['sessions,turns', 'x y&z'])
    assert.equal(buildTimelineUrl({ query: { provider: 'codex' }, afterId: 3 }), '/api/timeline/?provider=codex&after_id=3')
    assert.equal(buildTimelineUrl({ query: {} }), '/api/timeline/')
})
