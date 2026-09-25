import { pluralize } from './backgroundWork.js'

export function getProcessStateNotificationEffects(msg, previousState, options) {
    const enteredUserTurn = msg.state === 'user_turn'
        && previousState?.state !== 'user_turn'
    const userTurnEnabled = enteredUserTurn && msg.mute_on_user_turn !== true
    const newPendingCount = msg.pending_requests?.length || 0
    const previousPendingCount = previousState?.pending_requests?.length || 0
    const pendingRequestGrew = newPendingCount > previousPendingCount

    return {
        markViewed: enteredUserTurn && options.isViewingSession,
        showUserTurnToast: userTurnEnabled && !options.isViewingSession
            && options.userTurnToastEnabled,
        playUserTurnSound: userTurnEnabled,
        sendUserTurnBrowser: userTurnEnabled && options.userTurnBrowserEnabled,
        showPendingRequestToast: pendingRequestGrew && !options.isViewingSession,
        playPendingRequestSound: pendingRequestGrew,
        sendPendingRequestBrowser: pendingRequestGrew
            && options.pendingRequestBrowserEnabled,
        newPendingCount,
    }
}


/**
 * Title and detail line of the "finished" notification family (toast +
 * browser notification) for a process entering user_turn.
 *
 * A background shell the agent left running means the turn is
 * over but the work is not: the title then says the turn finished, and a
 * detail line counts what still runs. Without one, the wording is unchanged.
 *
 * @param {Object} options
 * @param {string} options.providerLabel
 * @param {number} [options.backgroundShells] - `background_work_in_progress.shells`.
 * @param {boolean} [options.ephemeral] - Ephemeral sessions keep their own title.
 * @returns {{title: string, detail: string|null}}
 */
export function getUserTurnNotificationText({ providerLabel, backgroundShells = 0, ephemeral = false }) {
    const shells = Number.isFinite(backgroundShells) && backgroundShells > 0 ? backgroundShells : 0
    let title
    if (ephemeral) title = 'Ephemeral session finished'
    else if (shells) title = `${providerLabel} finished its turn`
    else title = `${providerLabel} finished working`
    const detail = shells ? `${pluralize(shells, 'background shell')} still running` : null
    return { title, detail }
}
