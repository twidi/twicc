import { remoteUiError } from '../i18n/remoteMessages.js'
import { localUiMessage } from '../i18n/uiMessages.js'

const muteUpdateStates = new WeakMap()

function getMuteUpdateState(sessions, sessionId) {
    let sessionStates = muteUpdateStates.get(sessions)
    if (!sessionStates) {
        sessionStates = new Map()
        muteUpdateStates.set(sessions, sessionStates)
    }

    let state = sessionStates.get(sessionId)
    if (!state) {
        const session = sessions[sessionId]
        state = {
            tail: null,
            latestRequestId: 0,
            confirmedHasValue: Boolean(session) && Object.hasOwn(session, 'mute_on_user_turn'),
            confirmedValue: session?.mute_on_user_turn,
        }
        sessionStates.set(sessionId, state)
    }

    return { sessionStates, state }
}

/**
 * Persist one session's finished-working notification preference.
 *
 * @param {Object<string, object>} sessions
 * @param {Function} apiFetch
 * @param {string} projectId
 * @param {string} sessionId
 * @param {boolean} value
 * @throws {Error} If the update fails
 */
export async function applySessionMuteOnUserTurn(sessions, apiFetch, projectId, sessionId, value) {
    const session = sessions[sessionId]
    const { sessionStates, state } = getMuteUpdateState(sessions, sessionId)
    const requestId = state.latestRequestId + 1
    state.latestRequestId = requestId

    if (session) {
        session.mute_on_user_turn = value
    }

    const persist = async () => {
        try {
            const response = await apiFetch(
                `/api/projects/${projectId}/sessions/${sessionId}/`,
                {
                    method: 'PATCH',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ mute_on_user_turn: value }),
                },
            )
            if (!response.ok) {
                const data = await response.json()
                throw remoteUiError(data, data.error || localUiMessage('session.notifications.errors.update'))
            }
            const updatedSession = await response.json()
            state.confirmedHasValue = true
            state.confirmedValue = Object.hasOwn(updatedSession, 'mute_on_user_turn')
                ? updatedSession.mute_on_user_turn
                : value
            if (requestId === state.latestRequestId) {
                sessions[sessionId] = { ...sessions[sessionId], ...updatedSession }
            }
        } catch (error) {
            const currentSession = sessions[sessionId]
            if (requestId === state.latestRequestId && currentSession) {
                if (state.confirmedHasValue) {
                    currentSession.mute_on_user_turn = state.confirmedValue
                } else {
                    delete currentSession.mute_on_user_turn
                }
            }
            throw error
        }
    }

    const request = state.tail ? state.tail.then(persist) : persist()
    const settled = request.then(
        () => undefined,
        () => undefined,
    )
    state.tail = settled

    try {
        return await request
    } finally {
        if (state.tail === settled) {
            sessionStates.delete(sessionId)
            if (sessionStates.size === 0) {
                muteUpdateStates.delete(sessions)
            }
        }
    }
}
