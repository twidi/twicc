import { computed, toRaw } from 'vue'
import { sessionSortComparator } from './sessionSort.js'

const listsByState = new WeakMap()

/** Retain list identity when the same records remain in the same order. */
export function retainSessionArray(previous, next) {
    return previous?.length === next.length && next.every((entry, i) => entry === previous[i]) ? previous : next
}

/** Cache membership separately from ordering so active mtime updates do not sort again. */
export function getStableSessionList(state, scopeId, allProjectsId, hasActiveStartupPhase) {
    let lists = listsByState.get(state)
    if (!lists) listsByState.set(state, lists = new Map())
    if (!lists.has(scopeId)) {
        const members = computed(previous => {
            const scope = state.localState.projects[scopeId]
            const oldestMtime = scope?.hasMoreSessions ? scope.oldestSessionMtime : null
            const startup = hasActiveStartupPhase(state.startupProgress)
            // Keep startup's membership tracking without subscribing to thousands
            // of individual metadata fields. Normal tracking resumes afterwards.
            Object.keys(state.sessions)
            const sessions = Object.values(startup ? toRaw(state.sessions) : state.sessions)
            const next = sessions.filter(session =>
                (scopeId === allProjectsId || session.project_id === scopeId)
                && !session.parent_session_id && !session.hidden
                && (oldestMtime == null || session.mtime >= oldestMtime))
            return retainSessionArray(previous, next)
        })
        const ordered = computed(previous => {
            // Track the mode here too: stable membership must not prevent the
            // sort from acquiring normal field dependencies after startup.
            const startup = hasActiveStartupPhase(state.startupProgress)
            const processes = startup ? toRaw(state.processStates) : state.processStates
            const next = [...members.value].sort(sessionSortComparator(processes))
            return retainSessionArray(previous, next)
        })
        lists.set(scopeId, ordered)
    }
    return lists.get(scopeId).value
}
