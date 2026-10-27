// Complete snapshots for one loaded chat. The Timeline page keeps its progressive feed.
import { compareNewestFirst, cursorOf } from '../timeline/eventTime.js'

/** Kinds with no row in the chat: turns are the chat's own flow, the creation is its first message. */
const HIDDEN_KINDS = new Set(['turn.started', 'turn.ended', 'session.created'])

export function createSessionEventHistoryState() {
    return { status: 'idle', snapshot: null, error: null }
}

export function createSessionEventHistory({ sessionId, fetchPage, state = createSessionEventHistoryState(), onSnapshot = () => {} }) {
    let started = false, disposed = false, hidden = false
    let full = true, force = false, latest = 0, cursor = 0
    let running = null, activeRequest = null
    // The first process start of the session: it is the session's own beginning, like its creation, so no row.
    let firstStartId = null
    const stopped = () => disposed || hidden
    const selected = event => event.session_id === sessionId && !HIDDEN_KINDS.has(event.kind)

    async function request(before, afterId) {
        activeRequest = new AbortController()
        try {
            const response = await fetchPage({ sessionId, before, afterId, signal: activeRequest.signal })
            if (stopped()) return null
            if (response.hidden_sessions?.includes(sessionId)) {
                hidden = true
                state.snapshot = null
                state.status = 'idle'
                state.error = null
                onSnapshot(null)
                return null
            }
            return response
        } finally { activeRequest = null }
    }

    function merge(target, response) {
        for (const event of response.events) if (selected(event)) target.events.set(event.id, event)
        Object.assign(target.sessions, response.sessions)
    }

    function publish(target) {
        const previous = state.snapshot
        const known = new Map(previous?.events.map(event => [event.id, event]) ?? [])
        const all = [...target.events.values()].sort((a, b) => compareNewestFirst(b, a))
        if (firstStartId === null) firstStartId = all.find(event => event.kind === 'process.started')?.id ?? null
        const events = all.filter(event => event.id !== firstStartId).map(event => known.get(event.id) ?? event)
        const sessions = {}
        for (const [id, value] of Object.entries(target.sessions)) {
            const old = previous?.sessions[id]
            sessions[id] = old && JSON.stringify(old) === JSON.stringify(value) ? old : value
        }
        const same = previous && events.length === previous.events.length
            && events.every((event, i) => event === previous.events[i])
            && Object.keys(sessions).length === Object.keys(previous.sessions).length
            && Object.entries(sessions).every(([id, session]) => previous.sessions[id] === session)
        if (!same) {
            state.snapshot = { events, sessions }
            onSnapshot(state.snapshot)
        }
        state.status = 'ready'
        state.error = null
    }

    async function loadComplete() {
        state.status = state.snapshot ? 'recovering' : 'loading'
        while (!stopped()) {
            firstStartId = null
            const target = { events: new Map(), sessions: {} }
            let response = await request(null, null)
            if (!response) return
            const watermark = response.latest_id ?? 0
            merge(target, response)
            while (response.has_more) {
                const tail = response.events.at(-1)
                if (!tail) throw new Error('Session event pagination made no progress')
                const before = cursorOf(tail)
                response = await request(before, null)
                if (!response) return
                const next = response.events.at(-1)
                if (response.has_more && (!next || cursorOf(next) === before || compareNewestFirst(next, tail) <= 0)) {
                    throw new Error('Session event pagination made no progress')
                }
                merge(target, response)
            }
            response = await request(null, watermark)
            if (!response) return
            if (response.truncated || response.shown_sessions?.includes(sessionId)) continue
            merge(target, response)
            cursor = response.latest_id ?? watermark
            full = false
            publish(target)
            return
        }
    }

    async function loadLive() {
        force = false
        const response = await request(null, cursor)
        if (!response) return
        if (response.truncated || response.shown_sessions?.includes(sessionId)) {
            full = true
            return
        }
        const target = { events: new Map(state.snapshot?.events.map(event => [event.id, event]) ?? []), sessions: { ...state.snapshot?.sessions } }
        merge(target, response)
        cursor = response.latest_id ?? cursor
        publish(target)
    }

    function pump() {
        if (running || stopped() || !started || state.status === 'error') return
        running = Promise.resolve().then(async () => {
            try {
                while (!stopped() && (full || force || latest > cursor)) {
                    if (full) { force = false; await loadComplete() }
                    else await loadLive()
                }
            } catch (error) {
                if (!stopped()) { state.status = 'error'; state.error = error }
            } finally { running = null }
        })
    }

    function refresh() {
        if (stopped()) return
        state.error = null
        if (state.status === 'error') state.status = state.snapshot ? 'recovering' : 'loading'
        force = true
        pump()
    }
    return {
        state,
        start() { if (started || stopped()) return; started = true; state.status = 'loading'; pump() },
        notifyLatest(id) { latest = Math.max(latest, id); pump() },
        refresh,
        retry: refresh,
        async idle() { while (running) await running },
        dispose() { disposed = true; activeRequest?.abort() },
    }
}
