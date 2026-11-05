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
import { useSummariesStore } from './summaries.js'

// Each Pinia instance owns one coordinator. Requests, tokens and listeners stay outside reactive state.
const coordinators = new WeakMap()
// After this much silence the listener may have lost track of where they are: announce the full context again.
const CONTEXT_SILENCE_MS = 3 * 60 * 1000
const wireTime = milliseconds => new Date(milliseconds).toISOString().replace('Z', '000Z')

/** Native mode has no Web feed, summary preparation, or playback coordinator. */
function createNativeCoordinator(store, { dataStore, workspacesStore }) {
    const tts = useTextToSpeechStore(), filters = useTimelineFiltersStore(), client = tts.nativeClient()
    let activationId = null, unwatchMuted = null, lastError = null
    const reportedReadings = new Set()
    const readingKey = (readingId, generation) => `${readingId}:${generation}`
    function reportReading(readingId, generation, message) {
        const key = readingKey(readingId, generation)
        if (reportedReadings.has(key)) return
        reportedReadings.add(key)
        if (reportedReadings.size > 64) reportedReadings.delete(reportedReadings.values().next().value)
        store._showError(message)
    }
    function mirror(snapshot) {
        const listening = snapshot?.listening
        if (listening?.activationId !== activationId) {
            unwatchMuted?.(); unwatchMuted = null
            activationId = listening?.activationId ?? null
            if (listening) {
                // Restore this scope, even when the reloaded page opens another route.
                filters.setIncludeMuted(listening.pageId, listening.includeMuted)
                unwatchMuted = watch(() => filters.filtersFor(listening.pageId).includeMuted,
                    value => { void client.setMuted(value) }, { flush: 'sync' })
            }
        }
        store.activePageId = listening?.pageId ?? null
        store.activeLabel = listening?.scope?.label ?? ''
        store.paused = listening?.paused === true
        store.pendingCount = snapshot?.pendingCount ?? 0
        const results = snapshot?.results ?? []
        for (const result of results) {
            if (result.automatic && result.readingId && result.ended?.ok === false && !result.ended.aborted) {
                reportReading(result.readingId, result.generation, result.ended.error)
            }
        }
        const error = snapshot?.error
        const key = error ? JSON.stringify(error) : null
        // A raw backend state can omit ownership metadata. The retained native result keeps it.
        const terminal = error?.reading_id ? results.find(result => result.readingId === error.reading_id
            && result.generation === error.generation) : null
        const operationId = error?.operation_id ?? terminal?.operationId
        const handledByControl = operationId && client.handlesOperation?.(operationId)
        if (error?.reading_id && !handledByControl) reportReading(error.reading_id, error.generation, error.message)
        else if (key && key !== lastError && !handledByControl && !error.reading_id) store._showError(error.message)
        lastError = key
    }
    mirror(tts.nativeSnapshot)
    const unwatch = watch(() => tts.nativeSnapshot, mirror, { flush: 'sync' })
    return {
        async enable(pageId) {
            if (!tts.available) return
            const scope = resolveTimelineScope({ pageId, allProjectsId: ALL_PROJECTS_ID,
                projectsLoaded: dataStore.projectsLoaded,
                workspaceOf: id => workspacesStore.getWorkspaceById(id) ? { allProjectIds: workspacesStore.getAllProjectIds(id) } : null,
                projectOf: id => dataStore.getProject(id), projectScopeIds: id => dataStore.getProjectScopeIds(id) })
            if (scope.state !== 'ready') return
            const id = scope.kind === 'global' ? null : scope.kind === 'workspace' ? extractWorkspaceId(pageId) : scope.projectId
            const label = scope.kind === 'global' ? 'All projects' : scope.kind === 'workspace'
                ? workspacesStore.getWorkspaceById(id)?.name ?? '' : dataStore.getProjectDisplayName(pageId)
            const accepted = await client.listen({ pageId, scope: { kind: scope.kind, id, label },
                includeMuted: filters.filtersFor(pageId).includeMuted, boundary: wireTime(store._now()) })
            // Native displays the advice. Its next state poll adopts listening only after confirmation.
            if (!accepted?.accepted && accepted?.error?.code !== 'confirmation_required')
                store._showError(accepted?.error?.message || 'Timeline reading could not start. Try again.')
        },
        disable() { void client.disable() },
        dispose() { unwatchMuted?.(); unwatch() },
    }
}

function createCoordinator(store, { dataStore, workspacesStore, connectionStatus, eventTarget, settingsStore }) {
    const tts = useTextToSpeechStore()
    const filters = useTimelineFiltersStore()
    const summaries = useSummariesStore()
    const summaryEnabled = () => settingsStore?.timelineSummaryEnabled !== false
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

    function releasePreparation(activation, id) {
        const preparation = activation.preparations.get(id)
        if (!preparation) return
        activation.preparations.delete(id)
        preparation.unwatch?.()
        preparation.unwatchEntry?.()
        preparation.subscription?.release()
    }

    function acquirePreparation(activation, preparation) {
        if (!preparation.subscription) {
            preparation.subscription = summaries.request({ event_id: preparation.event.id }, { manual: false })
            preparation.unwatchEntry = watch(() => preparation.subscription.entry.state, () => {
                if (current(activation)) prepare(activation)
            })
        }
        return preparation.subscription
    }

    function prepare(activation) {
        if (!current(activation)) return
        const pendingIds = new Set(activation.pending.map(event => event.id))
        for (const id of activation.preparations.keys()) {
            if (activation.reading?.id !== id && (!summaryEnabled() || !pendingIds.has(id))) releasePreparation(activation, id)
        }
        if (!summaryEnabled()) { queueMicrotask(() => drain(activation)); return }
        for (const event of activation.pending) {
            if (activation.preparations.has(event.id)) continue
            const preparation = { event, descriptor: null, subscription: null }
            activation.preparations.set(event.id, preparation)
            preparation.unwatch = watch(() => summaries.versionFor({ event_id: event.id }), async () => {
                preparation.descriptor = null
                // Selected readings retain their frozen policy and owner until playback settles.
                if (activation.reading?.id === event.id) return
                preparation.unwatchEntry?.()
                preparation.subscription?.release()
                preparation.subscription = null
                const version = summaries.versionFor({ event_id: event.id })
                const [descriptor] = await summaries.resolve([{ event_id: event.id }])
                if (!current(activation) || activation.preparations.get(event.id) !== preparation
                    || summaries.versionFor({ event_id: event.id }) !== version) return
                preparation.descriptor = descriptor
                prepare(activation)
            }, { immediate: true })
        }
        let running = [...activation.preparations.values()].filter(preparation =>
            preparation.event.id !== activation.reading?.id && preparation.subscription
            && preparation.subscription.entry.state === 'running' && !preparation.descriptor?.saved).length
        for (const event of activation.pending) {
            const preparation = activation.preparations.get(event.id)
            if (preparation.subscription || !preparation.descriptor?.eligible) continue
            if (running >= 2 && !preparation.descriptor.saved) continue
            acquirePreparation(activation, preparation)
            if (!preparation.descriptor.saved) running++
        }
        queueMicrotask(() => drain(activation))
    }

    function purge(activation) {
        activation.pending = activation.pending.filter(event => activation.boundaries.has(event.project_id)
            && !hidden(activation, event) && (includeMuted() || !muted(activation, event)))
        count(activation)
        prepare(activation)
    }

    function disable() {
        const previous = active
        active = null
        // Invalidate every continuation before notifying the player or publishing idle UI state.
        if (previous) for (const id of previous.preparations.keys()) releasePreparation(previous, id)
        previous?.unbind?.()
        previous?.feed?.dispose()
        store.activePageId = null
        store.activeLabel = ''
        store.pendingCount = 0
        if (previous) tts.stop({ owner: previous.token })
    }

    function rememberStart(activation) {
        const event = activation.reading
        if (!event) return
        if (!activation.watermark || event.occurred_at > activation.watermark) activation.watermark = event.occurred_at
        activation.context = { project: event.project_id, session: event.session_id, endedAt: null }
    }

    function announcement(activation, event) {
        let context = activation.context
        if (context?.endedAt && Date.now() - context.endedAt > CONTEXT_SILENCE_MS) context = null
        const names = []
        if (context?.project !== event.project_id) names.push(dataStore.getProjectDisplayName(event.project_id))
        if (context?.session !== event.session_id) names.push(sessionMention(event.session_id, activation.feed.state.sessions).title)
        return names.map(name => `${name}.`).join(' ')
    }

    async function drain(activation) {
        if (!current(activation) || activation.processing || activation.bootstrap || !activation.caughtUp
            || connectionStatus.value !== 'OPEN' || !tts.available || tts.status !== 'idle') return
        const next = activation.pending[0]
        if (!next) return
        const preparation = summaryEnabled() ? activation.preparations.get(next.id) : null
        if (summaryEnabled() && !preparation?.descriptor) return
        const event = activation.pending.shift()
        activation.processing = true
        activation.reading = event
        count(activation)
        try {
            let result
            const descriptor = preparation?.descriptor
            if (descriptor?.error) result = { ok: false, error: descriptor.error }
            else if (descriptor?.eligible) {
                const subscription = acquirePreparation(activation, preparation)
                if (['failed', 'cancelled'].includes(subscription.entry.state)) {
                    result = { ok: false, error: subscription.entry.error || 'The summary is unavailable' }
                } else {
                    // Reserve the existing player before handle acquisition. Settings freeze here.
                    let acquisitionError = null
                    const reference = subscription.ready.then(() => ({ id: subscription.id }), error => {
                        acquisitionError = error.message || 'The summary is unavailable'
                        throw error
                    })
                    result = await tts.playSummary(reference, {
                        identity: { sourceKey: descriptor.source_key, sourceHash: descriptor.source_hash, variant: 'summary' },
                        owner: activation.token, announcement: announcement(activation, event),
                    })
                    if (acquisitionError && !result.aborted) result = { ...result, error: acquisitionError }
                }
            } else result = await tts.play(event.data.text, { owner: activation.token, announcement: announcement(activation, event) })
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
            if (!activation.pending.some(pending => pending.id === event.id)) releasePreparation(activation, event.id)
            if (current(activation)) prepare(activation)
        }
    }

    function consume(activation, feed, responseEpoch, { phase, body }) {
        if (!current(activation) || activation.feed !== feed || phase === 'backfill') return
        if (phase === 'live' && body.truncated) {
            store._showError('Some messages could not be loaded. Turn timeline reading on again.')
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
        prepare(activation)
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
        watch(summaryEnabled, () => { if (active) prepare(active) }, { flush: 'sync' }),
        watch(scopeOf, replaceScope, { deep: true, flush: 'sync' }),
        watch(() => tts.available, available => { if (!available) disable() }, { flush: 'sync' }),
        watch(() => tts.playback, playback => {
            const activation = active
            if (!activation) return
            if (playback.status === 'playing') {
                if (playback.owner === activation.token) rememberStart(activation)
                else activation.context = null
            }
            if (playback.status === 'idle') {
                if (activation.context) activation.context.endedAt = Date.now()
                void drain(activation)
            }
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
                seen: new Set(), shown: new Map(), hidden: new Set(), pending: [], preparations: new Map(), reading: null,
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
    state: () => ({ activePageId: null, activeLabel: '', pendingCount: 0, paused: false }),
    actions: {
        initialize({ dataStore, workspacesStore, connectionStatus, eventTarget = window, settingsStore = null }) {
            if (coordinators.has(this)) return
            const create = useTextToSpeechStore().nativeMode ? createNativeCoordinator : createCoordinator
            coordinators.set(this, create(this, { dataStore, workspacesStore, connectionStatus, eventTarget, settingsStore }))
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
