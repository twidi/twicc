/**
 * useSessionMute — the "mute this session's finished-working notification" action.
 *
 * Single entry point for every UI that flips `mute_on_user_turn` (session
 * header bell, command palette). The write itself lives in
 * `utils/sessionMute.js`, which stays free of app imports so its unit test can
 * load it under plain node; what belongs here is the part that needs the
 * stores and the toast.
 *
 * The flag gates four notification channels at once (in-app toast, sound,
 * browser notification, Apprise push). When none of them is enabled, flipping
 * it changes nothing observable — it stays a durable preference, correct again
 * the day a channel comes back, but the user is told so instead of being left
 * to guess. That explanation lives here so every entry point says the same thing.
 */
import { useDataStore } from '../stores/data'
import { useSettingsStore } from '../stores/settings'
import { hasAnyUserTurnChannel } from '../utils/userTurnChannels'
import { toast } from './useToast'

/** Where to turn a "finished working" channel back on. */
export const USER_TURN_SETTINGS_PATH = 'Settings → Notifications → Agent finished working'

/**
 * Whether flipping the flag would change anything the user can perceive.
 *
 * @returns {boolean}
 */
export function isUserTurnMuteInert() {
    return !hasAnyUserTurnChannel(useSettingsStore())
}

/**
 * Flip `mute_on_user_turn` on one session, warning when it is inert.
 *
 * @param {string} sessionId
 */
export function toggleSessionMute(sessionId) {
    const store = useDataStore()
    const session = store.getSession(sessionId)
    if (!session || session.draft) return
    store.setSessionMuteOnUserTurn(session.project_id, sessionId, !session.mute_on_user_turn)
    if (isUserTurnMuteInert()) {
        toast.warning(
            `No "finished working" notification is enabled, so this has no effect right now. `
            + `Turn one on in ${USER_TURN_SETTINGS_PATH}.`,
        )
    }
}
