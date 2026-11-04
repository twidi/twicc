import { defineStore } from 'pinia'
import { watch } from 'vue'
import { ALL_PROJECTS_ID } from '../constants.js'
import { createTimelineFeed, positionOfCursor } from '../utils/timeline/feed.js'
import { compareNewestFirst, shiftEventTime } from '../utils/timeline/eventTime.js'
import { bindTimelineEvents } from '../utils/timeline/liveEvents.js'
import { resolveTimelineScope } from '../utils/timeline/scope.js'
import { sessionMention } from '../utils/timeline/sessions.js'
import { extractWorkspaceId } from '../utils/workspaceIds.js'
import { REFUSED } from '../utils/speechOutcome.js'
import { useTextToSpeechStore } from './textToSpeech.js'
import { useTimelineFiltersStore } from './timelineFilters.js'

// Each Pinia instance owns one coordinator. Requests, tokens and listeners stay outside reactive state.
const coordinators = new WeakMap()
const wireTime = milliseconds => new Date(milliseconds).toISOString().replace('Z', '000Z')

function createCoordinator(store, { dataStore, workspacesStore, connectionStatus, eventTarget }) {
    const tts = useTextToSpeechStore()
    const filters = useTimelineFiltersStore()
    let active = null
    const current = activation => active === activation
    const count = activation => { store.pendingCount = activation.pending.length }

    function scopeOf() {
        if (!store.activePageId) return null
        const scope = resolveTimelineScope({
            pageId: store.activePageId, allProjectsId: ALL_PROJECTS_ID,
            projectsLoaded: dataStore.projectsLoaded,
            workspaceOf: id => workspacesStore.getWorkspaceById(id)
                ? { allProjectIds: workspacesStore.getAllProjectIds(id) } : null,
            projectOf: id => dataStore.getProject(id),
            projectScopeIds: id => dataStore.getProjectScopeIds(id),
        })
        const members = scope.kind === 'global' ? Object.keys(dataStore.projects) : scope.projectIds
        const label = scope.kind === 'global' ? 'All projects'
            : scope.kind === 'workspace' ? workspacesStore.getWorkspaceById(extractWorkspaceId(store.activePageId))?.name ?? ''
                : dataStore.getProjectDisplayName(store.activePageId)
        return { ...scope, members, label }
    }

    function hidden(activation, event) {
        return activation.hidden.has(event.session_id)
            || dataStore.getSession(event.session_id)?.hidden === true
            || activation.feed?.state.sessions[event.session_id]?.hidden === true
            || event.id <= (activation.shown.get(event.session_id) ?? -1)
    }

    function includeMuted() {
        return filters.filtersFor(store.activePageId).includeMuted
    }

    function muted(activation, event) {
        return dataStore.getSession(event.session_id)?.mute_on_user_turn === true
            || activation.feed?.state.sessions[event.session_id]?.mute_on_user_turn === true
    }

    function purge(activation) {
        activation.pending = activation.pending.filter(event => activation.boundaries.has(event.project_id)
            && !hidden(activation, event) && (includeMuted() || !muted(activation, event)))
        count(activation)
    }

    function disable(stop = true) {
        const previous = active
        active = null
        // Invalidate every continuation before notifying the player or publishing idle UI state.
        previous?.unbind?.()
        previous?.feed?.dispose()
        store.activePageId = null
        store.activeLabel = ''
        store.pendingCount = 0
        if (previous && stop) tts.stop({ owner: previous.token })
    }

    function rememberStart(activation) {
        const event = activation.reading
        if (!event) return
        if (!activation.watermark || event.occurred_at > activation.watermark) activation.watermark = event.occurred_at
        activation.context = { project: event.project_id, session: event.session_id }
    }

    function announcement(activation, event) {
        const context = activation.context
        const names = []
        if (context?.project !== event.project_id) names.push(dataStore.getProjectDisplayName(event.project_id))
        if (context?.session !== event.session_id) names.push(sessionMention(event.session_id, activation.feed.state.sessions).title)
        return names.map(name => `${name}.`).join(' ')
    }

    async function drain(activation) {
        if (!current(activation) || activation.processing || activation.bootstrap || !activation.caughtUp
            || connectionStatus.value !== 'OPEN' || !tts.available || tts.status !== 'idle') return
        const event = activation.pending.shift()
        if (!event) return
        count(activation)
        activation.processing = true
        activation.reading = event
        try {
            let result = await tts.play(event.data.text, { owner: activation.token, announcement: announcement(activation, event) })
            if (!current(activation)) return
            if (result.busy) {
                activation.pending.push(event)
                activation.pending.sort((a, b) => compareNewestFirst(b, a))
                purge(activation)
                return
            }
            if (result.ok) {
                result = await result.ended
                if (!current(activation)) return
            }
            if (!result.ok) {
                activation.context = null
                if (result.error) store._showError(result.error)
                if (!tts.available || result.error === REFUSED) disable()
            }
        } finally {
            activation.processing = false
            activation.reading = null
            if (current(activation)) void drain(activation)
        }
    }

    function consume(activation, feed, responseEpoch, { phase, body }) {
        if (!current(activation) || activation.feed !== feed || phase === 'backfill') return
        if (phase === 'live' && body.truncated) {
            store._showError('Timeline catch-up is incomplete. Automatic listening stopped.')
            // The feed checks disposal immediately after this callback, before its display-history reset.
            disable()
            return
        }
        if (phase === 'before' && !activation.bootstrap) return
        for (const id of body.hidden_sessions ?? []) activation.hidden.add(id)
        for (const id of body.shown_sessions ?? []) activation.hidden.delete(id)
        // Inspect the whole raw page before any admission: a show event can follow older messages in the body.
        for (const event of body.events) {
            if (event.kind !== 'hidden.ended') continue
            activation.shown.set(event.session_id, Math.max(event.id, activation.shown.get(event.session_id) ?? -1))
            activation.hidden.delete(event.session_id)
        }
        purge(activation)
        for (const event of body.events) {
            if (activation.seen.has(event.id)) continue
            activation.seen.add(event.id)
            const boundary = activation.boundaries.get(event.project_id)
            if (event.kind !== 'assistant_message.sent' || typeof event.data?.text !== 'string' || !event.data.text.trim()
                || !boundary || event.occurred_at <= boundary || hidden(activation, event)
                || (!includeMuted() && muted(activation, event))
                || (activation.watermark && event.occurred_at < shiftEventTime(activation.watermark, -5000))) continue
            activation.pending.push(event)
        }
        activation.pending.sort((a, b) => compareNewestFirst(b, a))
        count(activation)
        if (phase === 'live' && responseEpoch === activation.connectionEpoch && connectionStatus.value === 'OPEN') activation.caughtUp = true
        if (activation.bootstrap && (phase === 'first' || phase === 'before')) {
            const cursor = positionOfCursor(feed.state.pageCursor)
            if (feed.state.hasMore && cursor?.occurred_at > activation.boundary) feed.loadMore()
            else activation.bootstrap = false
        }
        void drain(activation)
    }

    function replaceScope(scope) {
        const activation = active
        if (!activation) return
        if (scope?.state !== 'ready' || !scope.members?.length) {
            disable()
            return
        }
        store.activeLabel = scope.label
        const key = [...scope.members].sort().join('\u0000')
        if (key === activation.scopeKey) return
        const joinedAt = activation.scopeKey === null ? activation.boundary : wireTime(store._now())
        const boundaries = new Map()
        for (const id of scope.members) boundaries.set(id, activation.boundaries.get(id) ?? joinedAt)
        activation.boundaries = boundaries
        activation.scopeKey = key
        purge(activation)
        const previous = activation.feed
        const afterId = activation.bootstrap ? null : previous?.state.liveCursor ?? 0
        activation.unbind?.()
        previous?.dispose()
        let responseEpoch = null
        const feed = createTimelineFeed({
            async fetchPage(params) {
                const epoch = activation.connectionEpoch
                const body = await store._fetchPage(params)
                responseEpoch = epoch
                return body
            },
            onPage: response => consume(activation, feed, responseEpoch, response),
        })
        activation.feed = feed
        if (afterId !== null) activation.caughtUp = false
        activation.unbind = bindTimelineEvents(eventTarget, {
            notifyLatest(id) {
                if (!current(activation) || activation.feed !== feed) return
                if (activation.bootstrap && feed.state.loadError) feed.retry()
                // A failed reconnection refresh must retry even if this invalidation names the same cursor.
                if (!activation.caughtUp) feed.refresh()
                else feed.notifyLatest(id)
            },
            refresh() {
                if (!current(activation) || activation.feed !== feed) return
                if (activation.bootstrap && feed.state.loadError) feed.retry()
                feed.refresh()
            },
        })
        // Observe mute changes even while excluded, without replaying messages when inclusion changes.
        feed.setScope(scope.projectIds, { include_muted: '1' }, { afterId })
        // Scope reset clears metadata; retain known titles before fresh responses merge over them.
        feed.state.sessions = { ...previous?.state.sessions }
        // A replacement bootstrap must inherit the reconnect refresh discarded with its previous feed.
        if (!activation.caughtUp && connectionStatus.value === 'OPEN') feed.refresh()
    }

    const subscriptions = [
        watch(scopeOf, replaceScope, { deep: true, flush: 'sync' }),
        watch(() => tts.available, available => { if (!available) disable() }, { flush: 'sync' }),
        watch(() => tts.playback, playback => {
            const activation = active
            if (!activation) return
            if (playback.status === 'playing') {
                if (playback.owner === activation.token) rememberStart(activation)
                else activation.context = null
            }
            if (playback.status === 'idle') void drain(activation)
        }, { flush: 'sync' }),
        watch(connectionStatus, status => {
            if (!active) return
            active.caughtUp = false
            active.connectionEpoch += 1
            if (status === 'OPEN') {
                if (active.bootstrap && active.feed.state.loadError) active.feed.retry()
                active.feed.refresh()
            }
        }, { flush: 'sync' }),
        watch(() => {
            // Reading the count tracks queue replacement; session flags remain independent of view ownership.
            void store.pendingCount
            return [includeMuted(), active?.pending.map(event => {
                const session = dataStore.getSession(event.session_id)
                return [session?.hidden, session?.mute_on_user_turn]
            }) ?? []]
        }, () => { if (active) purge(active) }, { flush: 'sync' }),
        tts.$onAction(({ name, args }) => {
            if (name !== 'stop' || !active || tts.playback.owner !== active.token) return
            const owner = args[0]?.owner ?? null
            if (owner === null || owner === active.token) disable(false)
        }, true),
    ]
    const onPageHide = () => disable()
    eventTarget.addEventListener('pagehide', onPageHide)
    return {
        enable(pageId) {
            const boundary = wireTime(store._now())
            tts.prime()
            disable()
            if (!tts.available) return
            active = {
                token: Symbol('timeline-speech'), boundary, boundaries: new Map(), scopeKey: null,
                seen: new Set(), shown: new Map(), hidden: new Set(), pending: [], reading: null,
                bootstrap: true, caughtUp: connectionStatus.value === 'OPEN', processing: false, context: null, watermark: null,
                feed: null, unbind: null, connectionEpoch: 0,
            }
            store.activePageId = pageId
        },
        disable,
        dispose() {
            disable()
            for (const unsubscribe of subscriptions) unsubscribe()
            eventTarget.removeEventListener('pagehide', onPageHide)
        },
    }
}

export const useTimelineSpeechStore = defineStore('timelineSpeech', {
    state: () => ({ activePageId: null, activeLabel: '', pendingCount: 0 }),
    actions: {
        initialize({ dataStore, workspacesStore, connectionStatus, eventTarget = window }) {
            if (coordinators.has(this)) return
            coordinators.set(this, createCoordinator(this, { dataStore, workspacesStore, connectionStatus, eventTarget }))
        },
        enable(pageId) { coordinators.get(this)?.enable(pageId) },
        disable() { coordinators.get(this)?.disable() },
        dispose() {
            coordinators.get(this)?.dispose()
            coordinators.delete(this)
        },
        _now() { return Date.now() },
        async _fetchPage(params) {
            const { fetchTimelinePage } = await import('../composables/useTimelineFeed.js')
            return fetchTimelinePage(params)
        },
        async _showError(message) {
            const { toast } = await import('../composables/useToast.js')
            toast.error(message)
        },
    },
})
