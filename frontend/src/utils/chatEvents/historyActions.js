import { createSessionEventHistory, createSessionEventHistoryState } from './history.js'
import { bindTimelineEvents } from '../timeline/liveEvents.js'

/** Pinia action factory: controllers and subscriptions stay outside reactive state. */
export function createSessionEventHistoryActions({ fetchPage, eventTarget = () => globalThis.window }) {
    const owners = new WeakMap()
    function syncActive(entry) {
        const active = [...entry.consumers.values()].some(Boolean)
        if (active && !entry.unbind) {
            const target = eventTarget()
            entry.unbind = target ? bindTimelineEvents(target, entry.controller) : () => {}
        } else if (!active && entry.unbind) {
            entry.unbind()
            entry.unbind = null
        }
        entry.controller.setActive(active)
    }
    return {
        ensureSessionEventHistory(sessionId) {
            const session = this.sessions[sessionId]
            if (!session || session.draft || session.hidden || session.ephemeral) return null
            let entries = owners.get(this)
            if (!entries) { entries = new Map(); owners.set(this, entries) }
            if (entries.has(sessionId)) return entries.get(sessionId).controller
            this.localState.sessionEventHistory[sessionId] = createSessionEventHistoryState()
            const state = this.localState.sessionEventHistory[sessionId]
            const controller = createSessionEventHistory({ sessionId, state,
                fetchPage: async params => {
                    const deadline = new AbortController()
                    const timer = setTimeout(() => deadline.abort(new Error('Session event request timed out')), 30000)
                    try { return await fetchPage({ ...params, signal: AbortSignal.any([params.signal, deadline.signal]) }) }
                    finally { clearTimeout(timer) }
                },
                onSnapshot: () => {
                    if (this.localState.sessionEventHistory[sessionId] === state) this.recomputeVisualItems(sessionId)
                },
            })
            const target = eventTarget()
            const unbind = target ? bindTimelineEvents(target, controller) : () => {}
            entries.set(sessionId, { controller, unbind, consumers: new Map() })
            controller.start()
            return controller
        },
        retainSessionEventHistory(sessionId, owner, active) {
            const controller = this.ensureSessionEventHistory(sessionId)
            if (!controller) return
            const entry = owners.get(this).get(sessionId)
            entry.consumers.set(owner, active)
            syncActive(entry)
        },
        releaseSessionEventHistory(sessionId, owner) {
            const entry = owners.get(this)?.get(sessionId)
            if (!entry) return
            entry.consumers.delete(owner)
            if (!entry.consumers.size) this.clearSessionEventHistory(sessionId)
            else syncActive(entry)
        },
        refreshSessionEventHistory(sessionId) {
            const existing = owners.get(this)?.get(sessionId)?.controller
            if (existing) existing.refresh()
            else this.ensureSessionEventHistory(sessionId)
        },
        retrySessionEventHistory(sessionId) {
            const controller = owners.get(this)?.get(sessionId)?.controller
            if (controller) controller.retry()
            else this.ensureSessionEventHistory(sessionId)
        },
        clearSessionEventHistory(sessionId) {
            const entries = owners.get(this), entry = entries?.get(sessionId)
            entry?.controller.dispose()
            entry?.unbind?.()
            entries?.delete(sessionId)
            const hadState = !!this.localState.sessionEventHistory[sessionId]
            delete this.localState.sessionEventHistory[sessionId]
            delete this.localState.sessionEventSegments[sessionId]
            delete this.localState.sessionEventRetired[sessionId]
            if (hadState) this.recomputeVisualItems(sessionId)
        },
    }
}
